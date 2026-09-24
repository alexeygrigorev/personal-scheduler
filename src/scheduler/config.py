"""Runtime configuration, read once per container from the environment."""

import os


def _clean(name, default=""):
    return os.environ.get(name, default).strip()


TABLE_NAME = _clean("TABLE_NAME", "personal-scheduler")
SITE_URL = _clean("SITE_URL", "https://scheduler.dtcdev.click").rstrip("/")

AUTH_BASE_URL = _clean("AUTH_BASE_URL", "https://auth.dtcdev.click").rstrip("/")
AUTH_CLIENT_ID = _clean("AUTH_CLIENT_ID")
AUTH_ISSUER = _clean("AUTH_ISSUER").rstrip("/")
AUTH_JWKS_URL = _clean("AUTH_JWKS_URL") or (
    f"{AUTH_ISSUER}/.well-known/jwks.json" if AUTH_ISSUER else ""
)
AUTH_CALLBACK_URL = _clean("AUTH_CALLBACK_URL") or f"{SITE_URL}/auth/callback"

SECRET_ARN = _clean("SESSION_SECRET_ARN")

EMAIL_SENDER = _clean("EMAIL_SENDER", "scheduler@datatalks.club")
DAPIER_BASE_URL = _clean("DAPIER_BASE_URL", "https://dapier.dtcdev.click").rstrip("/")
# Agent identity presented to Dapier's token endpoint, and the Secrets
# Manager ARN holding this deployment's enrolled machine credential
# ({"client_id", "refresh_token"}). Without either the calendar port fails
# closed — no credential is ever guessed or stored here.
DAPIER_AGENT = _clean("DAPIER_AGENT", "personal-scheduler")
DAPIER_MACHINE_SECRET_ARN = _clean("DAPIER_MACHINE_SECRET_ARN")
# Preferred over the enrolled machine identity when both are set: a
# Dapier-issued API token ({"api_token": "dap_…"}) the operator controls —
# visible and revocable in Dapier's console.
DAPIER_API_TOKEN_SECRET_ARN = _clean("DAPIER_API_TOKEN_SECRET_ARN")
WORK_QUEUE_URL = _clean("WORK_QUEUE_URL")

SESSION_COOKIE = "sched_session"
OIDC_COOKIE = "sched_oidc"

SESSION_TTL_SECONDS = 12 * 60 * 60
OIDC_TTL_SECONDS = 600

# Bootstrap owner: administers the scheduler on a fresh stack before any other
# grant exists. Everyone else is authorized through the host allowlist stored
# in the table — a successful shared-auth sign-in alone grants nothing.
ROOT_ADMINS = frozenset(
    part.strip().lower()
    for part in _clean("ROOT_ADMIN", "alexey@datatalks.club").split(",")
    if part.strip()
)

# Invitee management links: high-entropy bearer capabilities, valid long
# enough to cover rescheduling close to the meeting, revocable at any time.
MANAGEMENT_TOKEN_TTL_SECONDS = 365 * 24 * 60 * 60
