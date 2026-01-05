from flask import Flask, request, Response, jsonify
from gsearch.googlesearch import search as google_search
from urllib.parse import urlparse, parse_qs, quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import requests

app = Flask(__name__)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _filter_headers(headers):
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


def _extract_google_result_url(href):
    if not href:
        return None
    if href.startswith("/url?"):
        query = parse_qs(urlparse(href).query)
        if "q" in query and query["q"]:
            return query["q"][0]
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def _search_with_playwright(query, num_results, timeout, user_agent):
    timeout_ms = int(timeout * 1000)
    encoded_query = quote_plus(query)
    url = f"https://www.google.com/search?q={encoded_query}&num={num_results}&hl=en&gl=us"
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=user_agent,
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        context.add_init_script("window.chrome = { runtime: {} };")
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded")
        for selector in ("button#L2AGLb", "button:has-text('I agree')", "button:has-text('Accept all')"):
            try:
                if page.locator(selector).first.is_visible(timeout=1000):
                    page.locator(selector).first.click()
                    break
            except PlaywrightTimeoutError:
                pass

        try:
            page.wait_for_selector("a h3", timeout=5000)
        except PlaywrightTimeoutError:
            pass

        html = page.content()
        if "unusual traffic" in html.lower() or "recaptcha" in html.lower():
            raise RuntimeError("google blocked automated traffic (captcha)")

        items = page.eval_on_selector_all(
            "a h3",
            "els => els.map(el => ({title: el.innerText, href: el.closest('a') ? el.closest('a').getAttribute('href') : ''}))",
        )
        for item in items:
            title = (item.get("title") or "").strip()
            href = _extract_google_result_url(item.get("href"))
            if title and href:
                results.append((title, href))
        context.close()
        browser.close()

    return results


@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="ok")


@app.route("/proxy", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def proxy():
    target_url = request.args.get("url")
    payload = {}

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if not target_url:
            target_url = payload.get("url")
    elif not target_url:
        target_url = request.form.get("url")

    if not target_url:
        return jsonify(error="missing url"), 400

    method = (payload.get("method") if isinstance(payload, dict) else None) or request.method
    params = payload.get("params") if isinstance(payload, dict) else None
    headers = payload.get("headers") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    json_body = payload.get("json") if isinstance(payload, dict) else None

    timeout = float(os.getenv("PROXY_TIMEOUT", "20"))
    verify = os.getenv("PROXY_VERIFY", "true").lower() not in {"0", "false", "no"}

    outbound_headers = _filter_headers(dict(request.headers))
    outbound_headers.pop("Host", None)
    outbound_headers.pop("Content-Length", None)
    if isinstance(headers, dict):
        outbound_headers.update(headers)

    body = None
    if data is not None or json_body is not None:
        body = data
    elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        body = request.get_data()

    try:
        upstream = requests.request(
            method=method,
            url=target_url,
            params=params if params is not None else None,
            headers=outbound_headers,
            data=body,
            json=json_body if json_body is not None else None,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
            verify=verify,
        )
    except requests.RequestException as exc:
        return jsonify(error="upstream request failed", details=str(exc)), 502

    response_headers = _filter_headers(upstream.headers)
    return Response(upstream.content, status=upstream.status_code, headers=response_headers)


@app.route("/search", methods=["GET", "POST"])
def search():
    query = request.args.get("q")
    if request.is_json and not query:
        payload = request.get_json(silent=True) or {}
        query = payload.get("q")
    elif not query:
        query = request.form.get("q")

    if not query:
        return jsonify(error="missing q"), 400

    num_results = request.args.get("num_results")
    if request.is_json and num_results is None:
        payload = request.get_json(silent=True) or {}
        num_results = payload.get("num_results")
    elif num_results is None:
        num_results = request.form.get("num_results")

    if num_results is None:
        num_results = int(os.getenv("SEARCH_RESULTS", "10"))
    else:
        try:
            num_results = int(num_results)
        except (TypeError, ValueError):
            return jsonify(error="invalid num_results"), 400

    try:
        timeout = float(os.getenv("SEARCH_TIMEOUT", "20"))
        user_agent = os.getenv(
            "SEARCH_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        )
        results = _search_with_playwright(query, num_results, timeout, user_agent)
        if not results:
            results = google_search(query, num_results=num_results)
    except Exception as exc:
        return jsonify(error="search request failed", details=str(exc)), 502

    return jsonify(results=results)


@app.route("/fetch", methods=["GET", "POST"])
def fetch():
    target_url = request.args.get("url")
    if request.is_json and not target_url:
        payload = request.get_json(silent=True) or {}
        target_url = payload.get("url")
    elif not target_url:
        target_url = request.form.get("url")

    if not target_url:
        return jsonify(error="missing url"), 400

    timeout = float(os.getenv("FETCH_TIMEOUT", "20"))
    verify = os.getenv("FETCH_VERIFY", "true").lower() not in {"0", "false", "no"}
    user_agent = os.getenv(
        "FETCH_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    )

    try:
        upstream = requests.get(
            target_url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            allow_redirects=True,
            verify=verify,
        )
    except requests.RequestException as exc:
        return jsonify(error="fetch failed", details=str(exc)), 502

    response_headers = {"Content-Type": "text/html; charset=utf-8"}
    return Response(upstream.text, status=upstream.status_code, headers=response_headers)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "9443"))
    app.run(host=host, port=port)
