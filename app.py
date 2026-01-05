from flask import Flask, request, Response, jsonify
from gsearch.googlesearch import search as google_search
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
        results = google_search(query, num_results=num_results)
    except Exception as exc:
        return jsonify(error="search request failed", details=str(exc)), 502

    return jsonify(results=results)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "9443"))
    app.run(host=host, port=port)
