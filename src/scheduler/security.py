"""Signed tokens and capability hashing.

Sessions and OIDC pending-state cookies are HMAC-signed JSON rather than
server-side records: they are read on every request and hold no authority of
their own. Admin authorization is always re-resolved from the allowlist, so a
stale cookie cannot outlive a revoked grant.

Invitee management tokens are bearer capabilities: only a SHA-256 hash is
stored, never the token itself.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import boto3

from . import config

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_secret_cache = None


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def secret():
    """The stack's signing secret, fetched once per container."""
    global _secret_cache
    if _secret_cache is None:
        override = os.environ.get("SESSION_SECRET")
        if override:
            _secret_cache = override.encode()
        else:
            client = boto3.client("secretsmanager")
            value = client.get_secret_value(SecretId=config.SECRET_ARN)["SecretString"]
            try:
                _secret_cache = json.loads(value)["signing_secret"].encode()
            except (json.JSONDecodeError, KeyError, TypeError):
                _secret_cache = value.encode()
    return _secret_cache


def sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(token, kind=None):
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    expected = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    if kind is not None and payload.get("kind") != kind:
        return None
    return payload


def new_session_token(email):
    return sign(
        {
            "kind": "session",
            "email": email.lower(),
            "exp": int(time.time()) + config.SESSION_TTL_SECONDS,
        }
    )


def session_email(token):
    payload = verify(token, kind="session")
    return payload.get("email") if payload else None


def new_management_token():
    """A bearer capability for one booking; the raw value goes to the invitee
    by email only, and only its hash is ever stored or logged."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_management_token(raw)


def hash_management_token(token):
    return hashlib.sha256(token.strip().encode()).hexdigest()


def new_id(prefix):
    raw = secrets.token_hex(8)
    return f"{prefix}{raw}"


def new_reference():
    return "BK-" + secrets.token_hex(4).upper()


def constant_time_equals(left, right):
    return hmac.compare_digest(str(left), str(right))
