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


class HttpDapierClient(DapierClient):
    """Real Dapier consumer. The token-factory machine-access path and the
    calendar provider capability must be verified in Dapier before production
    launch; until the callable contract is confirmed this client stays
    disabled rather than guessing endpoint paths."""

    def __init__(self, base_url: str, workload_identity: str, token_path: str = "",
                 timeout: int = 10):
        self.base_url = (base_url or "").rstrip("/")
        self.workload_identity = workload_identity
        self.token_path = token_path
        self.timeout = timeout

    def get_access(self, connection_id: str, required_scopes: list[str]) -> CalendarAccess:
        if not self.base_url or not self.workload_identity or not self.token_path:
            raise DapierNotConfigured("dapier machine-access contract is not configured")
        body = json.dumps({"connection": connection_id,
                           "scopes": required_scopes}).encode()
        request = urllib.request.Request(
            self.base_url + self.token_path, data=body, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.workload_identity}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                payload = json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise GrantDenied(f"dapier refused connection {connection_id}") from exc
            raise DapierUnavailable(f"dapier error {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DapierUnavailable(f"dapier unreachable: {exc}") from exc
        return CalendarAccess(provider=payload["provider"], account=payload["account"],
                              scopes=payload.get("scopes", []),
                              expires_at_epoch=int(payload["expires_at"]),
                              token=payload["access_token"])
