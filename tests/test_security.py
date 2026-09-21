"""Signed cookies prove nothing on their own; authorization is re-resolved."""

from scheduler import security


def test_session_round_trip_returns_the_email():
    token = security.new_session_token("Host@DataTalks.Club")
    assert security.session_email(token) == "host@datatalks.club"


def test_tampered_token_is_refused():
    token = security.new_session_token("host@datatalks.club")
    body, _mac = token.split(".")
    assert security.session_email(body + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") is None


def test_wrong_kind_is_refused():
    token = security.sign({"kind": "oidc", "exp": 4102444800})
    assert security.session_email(token) is None


def test_management_tokens_store_only_a_hash():
    raw, digest = security.new_management_token()
    assert raw and digest
    assert digest == security.hash_management_token(raw)
    assert digest != raw
    assert len(raw) >= 32
