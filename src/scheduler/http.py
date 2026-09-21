"""Helpers for API Gateway HTTP API payload format 2.0."""

import decimal
import json


class HttpError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _default(value):
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def dumps(body):
    return json.dumps(body, default=_default, separators=(",", ":"))


def response(status, body="", *, content_type="text/plain; charset=utf-8", headers=None, cookies=None):
    result = {
        "statusCode": status,
        "headers": {"content-type": content_type, **(headers or {})},
        "body": body,
    }
    if cookies:
        result["cookies"] = cookies
    return result


def json_response(status, body, *, headers=None, cookies=None):
    return response(
        status,
        dumps(body),
        content_type="application/json",
        headers={"cache-control": "no-store", **(headers or {})},
        cookies=cookies,
    )


def html_response(status, body, *, headers=None, cookies=None):
    return response(
        status,
        body,
        content_type="text/html; charset=utf-8",
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer", **(headers or {})},
        cookies=cookies,
    )


def error_response(exc):
    return json_response(exc.status, {"error": {"code": exc.code, "message": exc.message}})


def redirect(location, *, status=302, cookies=None, headers=None):
    return response(
        status,
        "",
        headers={"location": location, "cache-control": "no-store", **(headers or {})},
        cookies=cookies,
    )


def method(event):
    return event.get("requestContext", {}).get("http", {}).get("method", "GET").upper()


def path(event):
    return event.get("rawPath", "/") or "/"


def query(event):
    return event.get("queryStringParameters") or {}


def header(event, name, default=""):
    headers = event.get("headers") or {}
    return headers.get(name, headers.get(name.lower(), default)) or default


def source_ip(event):
    return event.get("requestContext", {}).get("http", {}).get("sourceIp", "")


def cookie(event, name):
    prefix = f"{name}="
    raw = event.get("cookies")
    if not raw:
        raw = [part.strip() for part in header(event, "cookie").split(";") if part.strip()]
    return next((part[len(prefix):] for part in raw if part.startswith(prefix)), "")


def body(event):
    import base64

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "invalid_json", "Request body must be valid JSON.")
    if not isinstance(parsed, dict):
        raise HttpError(400, "invalid_json", "Request body must be a JSON object.")
    return parsed


def set_cookie(name, value, *, max_age, path_="/", same_site="Lax", http_only=True):
    parts = [f"{name}={value}", f"Path={path_}", f"Max-Age={max_age}", "Secure", f"SameSite={same_site}"]
    if http_only:
        parts.append("HttpOnly")
    return "; ".join(parts)


def clear_cookie(name, *, path_="/"):
    return set_cookie(name, "", max_age=0, path_=path_)
