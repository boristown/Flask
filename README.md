# Flask Proxy & Search Service

Base URL: https://xn--zlvp56j.com

## Health

- Endpoint: `GET /health`
- Description: Service health check.

Example:
```bash
curl "https://xn--zlvp56j.com/health"
```

## Proxy

- Endpoint: `GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS /proxy`
- Description: Forward requests to a target URL.
- Query/Form Params:
  - `url` (required): target URL
- JSON Body (optional):
  - `url` (string): target URL
  - `method` (string): override method
  - `params` (object): query params for upstream
  - `headers` (object): extra headers for upstream
  - `data` (any): raw body
  - `json` (object): JSON body

Example:
```bash
curl "https://xn--zlvp56j.com/proxy?url=https%3A%2F%2Fexample.com"
```

## Search

- Endpoint: `GET|POST /search`
- Description: Google search results via Playwright (fallback to `python-gsearch`).
- Query/Form Params:
  - `q` (required): search keyword
  - `num_results` (optional): results count, default 10
- Response: `{"results":[["Title","Link"], ...]}`

Example:
```bash
curl "https://xn--zlvp56j.com/search?q=Full%20Stack%20Developer&num_results=5"
```

## Fetch HTML

- Endpoint: `GET|POST /fetch`
- Description: Fetch static HTML of a page.
- Query/Form Params:
  - `url` (required): target URL
- Response: HTML content

Example:
```bash
curl "https://xn--zlvp56j.com/fetch?url=https%3A%2F%2Fexample.com"
```

## Environment Variables

- `HOST` / `PORT`: service bind address and port
- `PROXY_TIMEOUT`, `PROXY_VERIFY`
- `SEARCH_TIMEOUT`, `SEARCH_UA`, `SEARCH_RESULTS`
- `FETCH_TIMEOUT`, `FETCH_VERIFY`, `FETCH_UA`
