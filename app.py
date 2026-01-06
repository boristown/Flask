from flask import Flask, request, Response, jsonify
from gsearch.googlesearch import search as google_search
from urllib.parse import urlparse, parse_qs, quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import json
import os
import requests
import subprocess
import sys

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "log")
if not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(BASE_DIR, ".playwright")

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


def _log_api(api_name, status_code, detail=None):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = datetime.utcnow().isoformat() + "Z"
        remote = request.headers.get("X-Forwarded-For", request.remote_addr)
        path = request.full_path.rstrip("?")
        line = f"{timestamp}\t{remote}\t{request.method}\t{path}\t{status_code}"
        if detail:
            line = f"{line}\t{detail}"
        log_path = os.path.join(LOG_DIR, f"{api_name}.txt")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except Exception:
        pass


def _run_python_sandbox(code, stdin_data, timeout):
    harness = (
        "import json, sys, io, builtins, traceback\n"
        "payload = json.load(sys.stdin)\n"
        "code = payload.get('code') or ''\n"
        "stdin_data = payload.get('stdin') or ''\n"
        "allowed_modules = {'math','random','statistics','re','datetime','decimal','fractions','itertools','functools'}\n"
        "safe_builtins = {\n"
        "  'abs': builtins.abs, 'all': builtins.all, 'any': builtins.any,\n"
        "  'bool': builtins.bool, 'dict': builtins.dict, 'float': builtins.float,\n"
        "  'int': builtins.int, 'len': builtins.len, 'list': builtins.list,\n"
        "  'max': builtins.max, 'min': builtins.min, 'print': builtins.print,\n"
        "  'range': builtins.range, 'str': builtins.str, 'sum': builtins.sum,\n"
        "  'enumerate': builtins.enumerate, 'zip': builtins.zip, 'set': builtins.set,\n"
        "  'tuple': builtins.tuple, 'sorted': builtins.sorted, 'repr': builtins.repr,\n"
        "  'input': builtins.input,\n"
        "  'Exception': builtins.Exception, 'ValueError': builtins.ValueError,\n"
        "  'TypeError': builtins.TypeError, 'KeyError': builtins.KeyError,\n"
        "  'IndexError': builtins.IndexError, 'ZeroDivisionError': builtins.ZeroDivisionError,\n"
        "}\n"
        "def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "  if name in allowed_modules:\n"
        "    return __import__(name, globals, locals, fromlist, level)\n"
        "  raise ImportError(f\"import of '{name}' is blocked\")\n"
        "safe_builtins['__import__'] = guarded_import\n"
        "sys.stdin = io.StringIO(stdin_data)\n"
        "sandbox_globals = {'__builtins__': safe_builtins, '__name__': '__main__'}\n"
        "try:\n"
        "  exec(compile(code, '<sandbox>', 'exec'), sandbox_globals, None)\n"
        "except SystemExit:\n"
        "  raise\n"
        "except Exception:\n"
        "  traceback.print_exc()\n"
    )

    payload = json.dumps({"code": code, "stdin": stdin_data or ""})
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-u", "-c", harness],
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return {
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + "sandbox timeout\n",
            "returncode": 124,
            "timed_out": True,
        }


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


@app.route("/health", methods=["GET", "POST"])
def health():
    response = jsonify(status="ok")
    _log_api("health", response.status_code)
    return response


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
        _log_api("proxy", 400, "missing url")
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
        _log_api("proxy", 502, "upstream request failed")
        return jsonify(error="upstream request failed", details=str(exc)), 502

    response_headers = _filter_headers(upstream.headers)
    response = Response(upstream.content, status=upstream.status_code, headers=response_headers)
    _log_api("proxy", response.status_code)
    return response


@app.route("/search", methods=["GET", "POST"])
def search():
    query = request.args.get("q")
    if request.is_json and not query:
        payload = request.get_json(silent=True) or {}
        query = payload.get("q")
    elif not query:
        query = request.form.get("q")

    if not query:
        _log_api("search", 400, "missing q")
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
            _log_api("search", 400, "invalid num_results")
            return jsonify(error="invalid num_results"), 400

    timeout = float(os.getenv("SEARCH_TIMEOUT", "20"))
    user_agent = os.getenv(
        "SEARCH_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    )
    try:
        results = _search_with_playwright(query, num_results, timeout, user_agent)
    except Exception as exc:
        try:
            results = google_search(query, num_results=num_results)
        except Exception as fallback_exc:
            _log_api("search", 502, "search request failed")
            detail = f"{exc} | fallback failed: {fallback_exc}"
            return jsonify(error="search request failed", details=detail), 502
    if not results:
        try:
            results = google_search(query, num_results=num_results)
        except Exception as exc:
            _log_api("search", 502, "search request failed")
            return jsonify(error="search request failed", details=str(exc)), 502

    response = jsonify(results=results)
    _log_api("search", response.status_code)
    return response


@app.route("/fetch", methods=["GET", "POST"])
def fetch():
    target_url = request.args.get("url")
    if request.is_json and not target_url:
        payload = request.get_json(silent=True) or {}
        target_url = payload.get("url")
    elif not target_url:
        target_url = request.form.get("url")

    if not target_url:
        _log_api("fetch", 400, "missing url")
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
        _log_api("fetch", 502, "fetch failed")
        return jsonify(error="fetch failed", details=str(exc)), 502

    response_headers = {"Content-Type": "text/html; charset=utf-8"}
    response = Response(upstream.text, status=upstream.status_code, headers=response_headers)
    _log_api("fetch", response.status_code)
    return response


@app.route("/sandbox", methods=["GET", "POST"])
def sandbox():
    code = request.args.get("code")
    stdin_data = request.args.get("stdin")
    timeout = request.args.get("timeout")

    if request.is_json and not code:
        payload = request.get_json(silent=True) or {}
        code = payload.get("code")
        stdin_data = payload.get("stdin")
        timeout = payload.get("timeout")
    elif not code:
        code = request.form.get("code")
        stdin_data = request.form.get("stdin")
        timeout = request.form.get("timeout")

    if not code:
        _log_api("sandbox", 400, "missing code")
        return jsonify(error="missing code"), 400

    if timeout is None:
        timeout = float(os.getenv("SANDBOX_TIMEOUT", "5"))
    else:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            _log_api("sandbox", 400, "invalid timeout")
            return jsonify(error="invalid timeout"), 400

    result = _run_python_sandbox(code, stdin_data, timeout)
    response = jsonify(result)
    _log_api("sandbox", response.status_code, "timed_out" if result.get("timed_out") else None)
    return response


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "9443"))
    app.run(host=host, port=port)
