"""Dapier integration (spec section 10). This application consumes calendar
access through Dapier; it never manages provider OAuth itself.

Ownership boundary:
- Dapier: OAuth clients, consent, refresh tokens, rotation, revocation,
  verified account binding, connection grants, short-lived token delivery.
- Scheduler: booking policy, availability, reservations, invitee records,
  event lifecycle — plus transient in-memory use of a valid access token.

A provider access token may exist in backend memory for its allowed lifetime
and operation. It is never persisted, never logged, never sent to a browser,
and never stored in exports or backups.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass


PROPOSED_CONNECTION_ID = "calendar-alexey"


class DapierError(Exception):
    pass


class GrantDenied(DapierError):
    """No grant for this connection: wrong connection, revoked, or refused."""


class AccountMismatch(DapierError):
    """Dapier answered for a different account than expected. Fatal for the
    operation — never fall back to another connection."""


class InsufficientScope(DapierError):
    pass


class DapierUnavailable(DapierError):
    pass


class DapierNotConfigured(DapierError):
    pass


@dataclass
class CalendarAccess:
    provider: str
    account: str
    scopes: list
    expires_at_epoch: int
    token: str  # transient: backend memory only, never persisted or logged

    def usable(self) -> bool:
        return bool(self.token) and self.expires_at_epoch > int(time.time()) + 30


class DapierClient:
    def get_access(self, connection_id: str, required_scopes: list[str]) -> CalendarAccess:
        raise NotImplementedError

    def start_connect(self, connection_id: str) -> str:
        """Return the provider consent URL that binds this connection to its
        provider account — the agent-API connect flow."""
        raise NotImplementedError


class FakeDapierClient(DapierClient):
    """Scripted test double: renewal without refresh tokens, wrong-account
    and missing-scope refusals, and outage injection."""

    def __init__(self, *, provider="google", account="host@example.com",
                 scopes=("calendar.freebusy", "calendar.events.owned")):
        self.provider = provider
        self.account = account
        self.scopes = list(scopes)
        self.grants = {PROPOSED_CONNECTION_ID}
        self.outage = False
        self.wrong_account: str | None = None
        self.missing_scopes: set[str] = set()
        self.renewals = 0
        self.connect_url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=fake"

    def _check(self, connection_id, required):
        if self.outage:
            raise DapierUnavailable("dapier unreachable")
        if connection_id not in self.grants:
            raise GrantDenied(f"no grant for connection {connection_id}")
        account = self.wrong_account or self.account
        if account != self.account:
            raise AccountMismatch(f"expected {self.account}, got {account}")
        lacking = [s for s in required if s not in self.scopes
                   or s in self.missing_scopes]
        if lacking:
            raise InsufficientScope(f"missing scopes: {lacking}")

    def get_access(self, connection_id: str, required_scopes: list[str]) -> CalendarAccess:
        self._check(connection_id, required_scopes)
        self.renewals += 1  # every call yields fresh usable access: renewal
        return CalendarAccess(provider=self.provider, account=self.account,
                              scopes=list(required_scopes),
                              expires_at_epoch=int(time.time()) + 300,
                              token=f"fake-token-{self.renewals}")

    def start_connect(self, connection_id: str) -> str:
        self._check(connection_id, [])
        return self.connect_url


class HttpDapierClient(DapierClient):
    """Real Dapier consumer, speaking the deployed agent API
    (DataTalksClub/dapier ``src/agent_api.py``):

    ``POST {base}/api/agent/token`` with ``{"connection_id", "agent"}`` and
    ``Authorization: Bearer <DTC ID token>`` → ``{provider, access_token,
    expires_at, scope, provider_account_id}``. Dapier keeps every refresh
    token and client secret; this side holds only non-secret connection
    references and a short-lived token in memory."""

    def __init__(self, base_url: str, agent: str, identity=None,
                 token_path: str = "/api/agent/token", timeout: int = 10):
        self.base_url = (base_url or "").rstrip("/")
        self.agent = (agent or "").strip()
        self.identity = identity
        self.token_path = token_path
        self.timeout = timeout

    def get_access(self, connection_id: str, required_scopes: list[str]) -> CalendarAccess:
        if not self.base_url or not self.agent or self.identity is None:
            raise DapierNotConfigured(
                "dapier machine access is not configured: base URL, agent, "
                "and the enrolled identity secret are all required")
        body = json.dumps({"connection_id": connection_id,
                           "agent": self.agent}).encode()
        request = urllib.request.Request(
            self.base_url + self.token_path, data=body, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.identity.bearer()}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                payload = json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                # Identity refused, no grant, or unknown connection: none of
                # these heal by retrying, so the caller fails closed.
                raise GrantDenied(
                    f"dapier refused connection {connection_id} ({exc.code})") from exc
            raise DapierUnavailable(f"dapier error {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DapierUnavailable(f"dapier unreachable: {exc}") from exc
        try:
            scopes = payload.get("scope") or ""
            granted = scopes.split() if isinstance(scopes, str) else list(scopes)
            # Dapier reports what the connection was granted; abbreviated
            # scheduler names match the tail of full provider scope URLs.
            missing = [s for s in required_scopes
                       if granted and not any(g.endswith(s) for g in granted)]
            if missing:
                raise InsufficientScope(f"missing scopes: {missing}")
            return CalendarAccess(
                provider=payload["provider"],
                account=payload["provider_account_id"],
                scopes=granted,
                expires_at_epoch=int(payload["expires_at"]),
                token=payload["access_token"])
        except KeyError as exc:
            raise DapierError(f"dapier token response is missing {exc}") from exc

    def start_connect(self, connection_id: str) -> str:
        """Ask Dapier's agent API for this connection's provider consent URL.

        The request carries the enrolled machine identity, so the browser
        never signs into Dapier: the returned URL goes straight to the
        provider's consent screen, and Dapier's callback completes the
        account binding."""
        if not self.base_url or not self.agent or self.identity is None:
            raise DapierNotConfigured(
                "dapier machine access is not configured: base URL, agent, "
                "and the enrolled identity secret are all required")
        body = json.dumps({"connection_id": connection_id,
                           "agent": self.agent}).encode()
        request = urllib.request.Request(
            self.base_url + "/api/agent/connections/"
            + urllib.parse.quote(connection_id) + "/connect",
            data=body, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.identity.bearer()}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                payload = json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                # Identity refused, missing connect grant, or unknown
                # connection: none of these heal by retrying.
                raise GrantDenied(
                    f"dapier refused the connect start for {connection_id} "
                    f"({exc.code})") from exc
            raise DapierUnavailable(f"dapier error {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DapierUnavailable(f"dapier unreachable: {exc}") from exc
        authorize_url = payload.get("authorize_url")
        if not authorize_url:
            raise DapierError("dapier connect response had no authorize_url")
        return str(authorize_url)


class DtcRefreshIdentity:
    """Enrolled machine identity for unattended token requests.

    The operator provisions a dedicated shared-auth client's refresh token
    into Secrets Manager (one-time, out-of-band); at runtime this exchanges
    it at the DTC token endpoint for short-lived ID tokens, keeping the
    freshest one in memory until shortly before expiry. The scheduler's own
    DTC identity is the only durable credential involved — never a browser
    session and never a provider token."""

    def __init__(self, auth_base_url: str, secret_arn: str, timeout: int = 10):
        self.auth_base_url = (auth_base_url or "").rstrip("/")
        self.secret_arn = (secret_arn or "").strip()
        self.timeout = timeout
        self._refresh_token = ""
        self._client_id = ""
        self._id_token = ""
        self._expires_at_epoch = 0

    def _load_secret(self):
        if self._refresh_token:
            return
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(
            SecretId=self.secret_arn)["SecretString"]
        stored = json.loads(raw)
        self._refresh_token = stored["refresh_token"]
        self._client_id = stored["client_id"]

    @staticmethod
    def _expiry(token: str) -> int:
        # Unverified read of the exp claim, to decide whether the cached
        # bearer is stale before spending a refresh. Dapier verifies the
        # signature; this side only avoids sending a demonstrably dead one.
        try:
            payload = token.split(".")[1]
            padded = payload + "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(padded))["exp"])
        except (KeyError, ValueError, IndexError):
            return 0

    def _exchange(self):
        self._load_secret()
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
        }).encode()
        request = urllib.request.Request(
            f"{self.auth_base_url}/oauth2/token", data=body, method="POST",
            headers={"content-type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                data = json.loads(handle.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, ValueError) as exc:
            raise DapierUnavailable(
                f"machine identity refresh failed: {type(exc).__name__}") from exc
        token = data.get("id_token", "")
        if not token:
            raise DapierUnavailable("machine identity refresh returned no token")
        # The provider may rotate the refresh token; keep the newest so the
        # in-memory credential never goes stale mid-container-lifetime.
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        self._id_token = token
        self._expires_at_epoch = self._expiry(token)

    def bearer(self) -> str:
        if not self._id_token or self._expires_at_epoch <= int(time.time()) + 30:
            self._exchange()
        return self._id_token
