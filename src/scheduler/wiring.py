"""Lambda wiring seam: production ports built from the environment, with a
test override so handlers stay thin and hermetic under moto."""

from . import config

_test_wiring = None


def set_test(dapier=None, provider=None, email=None, queue=None, zoom=None):
    global _test_wiring
    _test_wiring = (dapier, provider, email, queue, zoom)


def reset():
    global _test_wiring
    _test_wiring = None


def get():
    """Return (dapier_client, calendar_provider, email_port, queue_sender)."""
    if _test_wiring is not None:
        return _test_wiring[:4]
    from .calendar import RestGoogleCalendarProvider
    from .dapier import DtcRefreshIdentity, HttpDapierClient, StaticTokenIdentity
    from .emailer import SesEmailPort
    from . import store as store_mod

    identity = None
    if config.DAPIER_API_TOKEN_SECRET_ARN:
        identity = StaticTokenIdentity(config.DAPIER_API_TOKEN_SECRET_ARN)
    elif config.DAPIER_MACHINE_SECRET_ARN:
        identity = DtcRefreshIdentity(config.AUTH_BASE_URL,
                                      config.DAPIER_MACHINE_SECRET_ARN)
    dapier = HttpDapierClient(config.DAPIER_BASE_URL, config.DAPIER_AGENT,
                              identity=identity)

    def supplier():
        connection = store_mod.get_calendar_connection()
        access = dapier.get_access(connection["dapier_connection_ref"],
                                   ["calendar.freebusy", "calendar.events.owned"])
        return access.token, access

    provider = RestGoogleCalendarProvider(supplier)
    try:
        email = SesEmailPort(config.EMAIL_SENDER)
    except Exception:
        email = None
    return dapier, provider, email, SqsSender()


def get_zoom():
    """Return the Zoom meetings port, or None when Dapier access is not
    configured at all. Zoom is an optional integration: only auto_zoom event
    types require it, and they fail closed on their own."""
    if _test_wiring is not None:
        return _test_wiring[4]
    if not config.DAPIER_BASE_URL or not config.DAPIER_AGENT or (
            not config.DAPIER_API_TOKEN_SECRET_ARN
            and not config.DAPIER_MACHINE_SECRET_ARN):
        return None
    from .dapier import ZOOM_CONNECTION_ID
    from .zoom import RestZoomMeetings, REQUIRED_ZOOM_SCOPES

    dapier = get()[0]

    def supplier():
        return dapier.get_access(ZOOM_CONNECTION_ID, REQUIRED_ZOOM_SCOPES).token

    return RestZoomMeetings(supplier)


class SqsSender:
    def send(self, payload: dict):
        url = config.WORK_QUEUE_URL
        if not url:
            return False
        try:
            import boto3
            boto3.client("sqs").send_message(
                QueueUrl=url,
                MessageBody=__import__("json").dumps(payload),
            )
            return True
        except Exception:
            return False
