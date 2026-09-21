"""Authorization code + PKCE against the shared login.

Follows the same consumer contract as the other DTC services: unpredictable
state and nonce, an S256 challenge, and full validation of the returned ID
token before a service session is created. The shared session is not reused
as our session.

A successful sign-in proves identity only. Admin rights come from the host
allowlist (plus the bootstrap root admin), never from authentication alone —
so an ordinary community account that signs in is still refused admin access.
"""

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request

import jwt

from . import config, security

_jwk_client = None
_log = logging.getLogger(__name__)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def configured():
    return all([config.AUTH_BASE_URL, config.AUTH_CLIENT_ID, config.AUTH_ISSUER, config.AUTH_JWKS_URL])


def safe_next(next_path):
    """Only same-site paths survive the round trip through the login.

    A leading `//` or `/\\` is a protocol-relative URL, which browsers follow
    off-site — so "starts with a slash" is not sufficient on its own.
    """
    candidate = str(next_path or "")
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return "/admin"
    return candidate


def begin(next_path="/admin"):
    """Return (authorize_url, state_token) for the login redirect."""
    verifier = _b64(os.urandom(48))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    state = _b64(os.urandom(24))
    nonce = _b64(os.urandom(24))

    query = urllib.parse.urlencode(
        {
            "client_id": config.AUTH_CLIENT_ID,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": config.AUTH_CALLBACK_URL,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    token = security.sign(
        {
            "kind": "oidc",
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "next": safe_next(next_path),
            "exp": int(time.time()) + config.OIDC_TTL_SECONDS,
        }
    )
    return f"{config.AUTH_BASE_URL}/oauth2/authorize?{query}", token


def _exchange(code, verifier):
    payload = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": config.AUTH_CLIENT_ID,
            "code": code,
            "redirect_uri": config.AUTH_CALLBACK_URL,
            "code_verifier": verifier,
        }
    ).encode()
    request = urllib.request.Request(
        f"{config.AUTH_BASE_URL}/oauth2/token",
        data=payload,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=10) as handle:
        return json.loads(handle.read().decode())


def _claims(id_token):
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(config.AUTH_JWKS_URL)
    key = _jwk_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256"],
        audience=config.AUTH_CLIENT_ID,
        issuer=config.AUTH_ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )


def complete(pending, code, state):
    """Validate the callback and return the signed-in email, or None.

    Every rejection is logged with a reason. The reasons carry no token, no
    signature, and no address — enough to tell a misconfiguration from a genuine
    refusal without putting credentials in CloudWatch.
    """
    if not pending:
        _log.warning("sign-in rejected: no pending state cookie")
        return None, None
    if not code:
        _log.warning("sign-in rejected: no authorization code in the callback")
        return None, None
    if not security.constant_time_equals(state, pending.get("state", "")):
        _log.warning("sign-in rejected: state mismatch")
        return None, None
    try:
        tokens = _exchange(code, pending["verifier"])
    except Exception:
        _log.exception("sign-in rejected: token exchange failed")
        return None, None
    try:
        claims = _claims(tokens["id_token"])
    except Exception:
        _log.exception("sign-in rejected: id token validation failed")
        return None, None
    if not security.constant_time_equals(str(claims.get("nonce", "")), pending.get("nonce", "")):
        _log.warning("sign-in rejected: nonce mismatch")
        return None, None
    # `email_verified` is not re-checked here: the shared pool's pre-sign-up
    # trigger already requires a verified address on every Google
    # authentication. The email claim itself still has to be present, because
    # admin grants are stored under it — but it grants nothing on its own.
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        _log.warning("sign-in rejected: no email claim")
        return None, None
    return email.lower(), pending.get("next", "/admin")


def logout_url():
    query = urllib.parse.urlencode(
        {"client_id": config.AUTH_CLIENT_ID, "logout_uri": f"{config.SITE_URL}/"}
    )
    return f"{config.AUTH_BASE_URL}/logout?{query}"
