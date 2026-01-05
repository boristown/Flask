from flask import Flask, request, Response, jsonify
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

    timeout = float(os.getenv("SEARCH_TIMEOUT", "20"))
    user_agent = os.getenv(
        "SEARCH_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    )

    try:
        upstream = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return jsonify(error="search request failed", details=str(exc)), 502

    response_headers = _filter_headers(upstream.headers)
    response_headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(upstream.content, status=upstream.status_code, headers=response_headers)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "9443"))
    app.run(host=host, port=port)
