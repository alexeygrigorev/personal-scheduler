"""The scheduler→Dapier contract, pinned against the deployed agent API
(DataTalksClub/dapier src/agent_api.py): POST /api/agent/token with an
API-token bearer."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scheduler.dapier import (DapierError, DapierNotConfigured, DapierUnavailable,
                              StaticTokenIdentity, GrantDenied, HttpDapierClient,
                              InsufficientScope, CALENDAR_CONNECTION_ID)


class _AgentAPI(BaseHTTPRequestHandler):
    """Scriptable stand-in for Dapier's agent API."""

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
        grant = json.loads(raw)
        self.server.requests.append({
            "path": self.path,
            "auth": self.headers.get("authorization", ""),
            "body": grant,
        })
        last = self.server.requests[-1]
        if self.path != "/api/agent/token":
            return self._reply(404, {"error": "Not found"})
        if last["auth"] != f"Bearer {self.server.api_token}":
            return self._reply(401, {"error": "Invalid identity"})
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
    server.api_token = "dap_sched-token-value"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _client(server, scopes=("calendar.freebusy", "calendar.events.owned")):
    identity = StaticTokenIdentity("test.api-token")
    identity._token = server.api_token
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


# --- StaticTokenIdentity: Dapier-issued API tokens --------------------------

class _ApiTokenSecret:
    """DataOps team store stand-in: the Dapier-issued API token."""

    def __init__(self, payload='{"api_token": "dap_sched-token-value"}'):
        self.payload = payload
        self.reads = 0

    def read_value(self, name):
        self.reads += 1
        return self.payload


def test_static_token_identity_bears_the_dapier_token(monkeypatch):
    from scheduler.dapier import StaticTokenIdentity

    secret = _ApiTokenSecret()
    monkeypatch.setattr("scheduler.secrets.read_value", lambda name: secret.read_value(name))
    identity = StaticTokenIdentity("arn:secret")
    assert identity.bearer() == "dap_sched-token-value"
    assert identity.bearer() == "dap_sched-token-value"
    assert secret.reads == 1  # cached in memory after the first read


def test_static_token_identity_rejects_a_non_dap_secret(monkeypatch):
    from scheduler.dapier import StaticTokenIdentity, DapierNotConfigured

    secret = _ApiTokenSecret(payload='{"api_token": "not-a-dapier-token"}')
    monkeypatch.setattr("scheduler.secrets.read_value", lambda name: secret.read_value(name))

    with pytest.raises(DapierNotConfigured):
        StaticTokenIdentity("arn:secret").bearer()


def test_http_client_accepts_a_static_api_token(agent_api, monkeypatch):
    """The API token is presented as the bearer on every agent-API call —
    no DTC exchange in between (contract: dapier /api/agent/token)."""
    from scheduler.dapier import HttpDapierClient, StaticTokenIdentity

    class _Secret:
        def read_value(self, name):
            return '{"api_token": "dap_sched-token-value"}'

    monkeypatch.setattr("scheduler.secrets.read_value", lambda name: _Secret().read_value(name))
    client = HttpDapierClient(
        f"http://127.0.0.1:{agent_api.server_port}", "personal-scheduler",
        identity=StaticTokenIdentity("arn:secret"),
    )

    access = client.get_access(CALENDAR_CONNECTION_ID,
                               ["calendar.freebusy", "calendar.events.owned"])

    assert access.usable()
    assert access.provider == "google"
    assert [r["path"] for r in agent_api.requests] == ["/api/agent/token"]


@pytest.mark.parametrize("configured", [True, False])
def test_production_wiring_requires_api_token(agent_api, monkeypatch, configured):
    from scheduler import config, wiring

    monkeypatch.setattr(wiring, "_test_wiring", None)
    monkeypatch.setattr(config, "DAPIER_BASE_URL", f"http://127.0.0.1:{agent_api.server_port}")
    monkeypatch.setattr(config, "DAPIER_AGENT", "personal-scheduler")
    monkeypatch.setattr(config, "DAPIER_API_TOKEN_SECRET_NAME",
                        "scheduler.api-token" if configured else "")
    monkeypatch.setattr("scheduler.emailer.SesEmailPort", lambda sender: None)
    reads = []
    def read(name):
        reads.append(name)
        return json.dumps({"api_token": agent_api.api_token})
    monkeypatch.setattr("scheduler.secrets.read_value", read)
    client = wiring.get()[0]
    if configured:
        assert client.get_access(CALENDAR_CONNECTION_ID, []).usable()
        assert reads == ["scheduler.api-token"]
        assert [r["path"] for r in agent_api.requests] == ["/api/agent/token"]
    else:
        with pytest.raises(DapierNotConfigured):
            client.get_access(CALENDAR_CONNECTION_ID, [])
        assert reads == []
        assert agent_api.requests == []
        assert wiring.get_zoom() is None
