"""The scheduler→Dapier contract, pinned against the deployed agent API
(DataTalksClub/dapier src/agent_api.py): POST /api/agent/token with a DTC
ID-token bearer, plus the enrolled machine-identity refresh exchange."""

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scheduler.dapier import (DapierError, DapierNotConfigured, DapierUnavailable,
                              DtcRefreshIdentity, GrantDenied, HttpDapierClient,
                              InsufficientScope)


class _AgentAPI(BaseHTTPRequestHandler):
    """Scriptable stand-in for dapier's agent API and the DTC token endpoint."""

    def log_message(self, *_args):
        pass

    def _reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) or b"{}"
        if self.path == "/oauth2/token":
            # The DTC token endpoint speaks form-encoded OAuth grants.
            from urllib.parse import parse_qs
            grant = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        else:
            grant = json.loads(raw)
        self.server.requests.append({
            "path": self.path,
            "auth": self.headers.get("authorization", ""),
            "body": grant,
        })
        last = self.server.requests[-1]
        if self.path == "/oauth2/token":
            grant = last["body"]
            if grant.get("refresh_token") != "machine-refresh":
                return self._reply(400, {"error": "invalid_grant"})
            return self._reply(200, {"id_token": self.server.id_token,
                                     "expires_in": 3600})
        if self.path != "/api/agent/token":
            return self._reply(404, {"error": "Not found"})
        if last["auth"] != f"Bearer {self.server.id_token}":
            return self._reply(401, {"error": "Invalid DTC identity"})
        if self.server.mode != "ok":
            return self._reply(self.server.mode, {"error": "denied"})
        return self._reply(200, {
            "connection_id": last["body"]["connection_id"],
            "provider": "google",
            "access_token": "provider-token-1",
            "expires_at": int(time.time()) + 600,
            "scope": "https://www.googleapis.com/auth/calendar.freebusy "
                     "https://www.googleapis.com/auth/calendar.events.owned",
            "provider_account_id": "host@example.com",
            "refreshed": False,
        })


@pytest.fixture
def agent_api():
    server = HTTPServer(("127.0.0.1", 0), _AgentAPI)
    server.requests = []
    server.mode = "ok"
    server.id_token = _id_token(expires_in=3600)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _id_token(expires_in):
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": "machine-sub", "exp": int(time.time()) + expires_in,
    }).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


class _StaticSecret:
    """Secrets Manager stand-in: the enrolled machine credential."""

    def __init__(self, client_id="sched-cli", refresh_token="machine-refresh"):
        self.payload = json.dumps({"client_id": client_id,
                                   "refresh_token": refresh_token})
        self.reads = 0

    def get_secret_value(self, SecretId):
        self.reads += 1
        return {"SecretString": self.payload}


def _client(server, scopes=("calendar.freebusy", "calendar.events.owned")):
    identity = DtcRefreshIdentity(f"http://127.0.0.1:{server.server_port}", "arn:secret")
    identity._load_secret = lambda: None  # secret already loaded below
    identity._refresh_token = "machine-refresh"
    identity._client_id = "sched-cli"
    return HttpDapierClient(f"http://127.0.0.1:{server.server_port}",
                            "personal-scheduler", identity=identity), scopes


def test_token_request_matches_the_agent_api_contract(agent_api):
    client, scopes = _client(agent_api)
    access = client.get_access("calendar-alexey", list(scopes))
    token_calls = [r for r in agent_api.requests if r["path"] == "/api/agent/token"]
    assert len(token_calls) == 1
    assert token_calls[0]["body"] == {"connection_id": "calendar-alexey",
                                      "agent": "personal-scheduler"}
    assert access.provider == "google"
    assert access.account == "host@example.com"
    assert access.token == "provider-token-1"
    assert access.usable()
    # Abbreviated scheduler scope names match the tail of the granted URLs.
    assert any(g.endswith("calendar.freebusy") for g in access.scopes)


def test_machine_identity_exchanges_refresh_for_id_token(agent_api):
    client, scopes = _client(agent_api)
    client.get_access("calendar-alexey", list(scopes))
    refresh_calls = [r for r in agent_api.requests if r["path"] == "/oauth2/token"]
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["body"] == {"grant_type": "refresh_token",
                                        "refresh_token": "machine-refresh",
                                        "client_id": "sched-cli"}
    # A still-valid cached ID token is reused: the second calendar operation
    # costs one token call, not another refresh round-trip.
    client.get_access("calendar-alexey", list(scopes))
    refresh_calls = [r for r in agent_api.requests if r["path"] == "/oauth2/token"]
    assert len(refresh_calls) == 1
    assert len([r for r in agent_api.requests if r["path"] == "/api/agent/token"]) == 2


def test_expired_id_token_triggers_a_fresh_exchange(agent_api):
    identity = DtcRefreshIdentity(f"http://127.0.0.1:{agent_api.server_port}", "arn:secret")
    identity._refresh_token = "machine-refresh"
    identity._client_id = "sched-cli"
    identity._id_token = _id_token(expires_in=-10)
    identity._expires_at_epoch = int(time.time()) - 10
    client = HttpDapierClient(f"http://127.0.0.1:{agent_api.server_port}",
                              "personal-scheduler", identity=identity)
    client.get_access("calendar-alexey", [])
    assert any(r["path"] == "/oauth2/token" for r in agent_api.requests)


def test_denied_grants_and_rate_limits_map_to_port_errors(agent_api):
    client, scopes = _client(agent_api)
    for mode, expected in ((403, GrantDenied), (404, GrantDenied), (401, GrantDenied),
                           (429, DapierUnavailable), (502, DapierUnavailable)):
        agent_api.mode = mode
        with pytest.raises(expected):
            client.get_access("calendar-alexey", [])
    agent_api.mode = "ok"


def test_missing_scope_is_refused_before_any_calendar_call(agent_api):
    client, _scopes = _client(agent_api)
    with pytest.raises(InsufficientScope):
        client.get_access("calendar-alexey", ["calendar.acl"])


def test_malformed_token_response_raises_dapier_error(agent_api):
    client, scopes = _client(agent_api)
    original = _AgentAPI._reply
    def truncated(self, status, body):
        original(self, 200, {k: v for k, v in body.items() if k != "access_token"})
    _AgentAPI._reply = truncated
    agent_api.mode = "ok"
    try:
        with pytest.raises(DapierError):
            client.get_access("calendar-alexey", [])
    finally:
        _AgentAPI._reply = original


def test_unconfigured_client_fails_closed():
    client = HttpDapierClient("", "personal-scheduler")
    with pytest.raises(DapierNotConfigured):
        client.get_access("calendar-alexey", [])
    no_identity = HttpDapierClient("https://dapier.example", "personal-scheduler")
    with pytest.raises(DapierNotConfigured):
        no_identity.get_access("calendar-alexey", [])


def test_identity_loads_the_enrolled_secret_once(monkeypatch):
    """The refresh credential is read from Secrets Manager once per
    container and kept in memory; the ARN itself carries no secret."""
    import sys
    import types
    secret = _StaticSecret()
    monkeypatch.setitem(sys.modules, "boto3",
                        types.SimpleNamespace(client=lambda name: secret))
    identity = DtcRefreshIdentity("https://auth.example", "arn:secret")
    identity._load_secret()
    identity._load_secret()  # second call must hit the in-memory copy
    assert secret.reads == 1
    assert identity._refresh_token == "machine-refresh"
    assert identity._client_id == "sched-cli"


def test_expiry_parse_survives_a_malformed_token():
    assert DtcRefreshIdentity._expiry("not-a-jwt") == 0
    assert DtcRefreshIdentity._expiry("a.b.c") == 0
