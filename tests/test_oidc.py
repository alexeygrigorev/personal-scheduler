"""The sign-in callback: the shared pool owns verification, this service
validates the token and the session. Mirrors the dataqna contract."""

import pytest

from scheduler import oidc


def pending(**overrides):
    payload = {"state": "s-value", "nonce": "n-value", "verifier": "v", "next": "/admin"}
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("claims", [
    {"email": "Host@DataTalks.Club", "email_verified": "true", "nonce": "n-value"},
    {"email": "Host@DataTalks.Club", "email_verified": "false", "nonce": "n-value"},
    {"email": "Host@DataTalks.Club", "nonce": "n-value"},
])
def test_verified_pool_account_signs_in_whatever_the_flag_says(monkeypatch, claims):
    monkeypatch.setattr(oidc, "_exchange", lambda code, verifier: {"id_token": "token"})
    monkeypatch.setattr(oidc, "_claims", lambda token: claims)
    email, next_path = oidc.complete(pending(), "code", "s-value")
    assert email == "host@datatalks.club"
    assert next_path == "/admin"


@pytest.mark.parametrize("claims", [
    {"email_verified": "true", "nonce": "n-value"},
    {"email": "", "email_verified": "true", "nonce": "n-value"},
])
def test_a_token_without_an_email_is_refused(monkeypatch, claims):
    monkeypatch.setattr(oidc, "_exchange", lambda code, verifier: {"id_token": "token"})
    monkeypatch.setattr(oidc, "_claims", lambda token: claims)
    assert oidc.complete(pending(), "code", "s-value") == (None, None)


def test_state_mismatch_is_refused(monkeypatch):
    monkeypatch.setattr(oidc, "_exchange", lambda code, verifier: {"id_token": "token"})
    monkeypatch.setattr(oidc, "_claims", lambda token: {
        "email": "host@datatalks.club", "email_verified": True, "nonce": "n-value"
    })
    assert oidc.complete(pending(), "code", "wrong-state") == (None, None)


@pytest.mark.parametrize("candidate, expected", [
    ("/admin/bookings", "/admin/bookings"),
    ("https://evil.example", "/admin"),
    ("//evil.example/admin", "/admin"),
    ("", "/admin"),
    (None, "/admin"),
])
def test_only_same_site_paths_survive_login(candidate, expected):
    assert oidc.safe_next(candidate) == expected
