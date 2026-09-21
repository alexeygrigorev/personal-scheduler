"""Lambda wiring seam: production ports built from the environment, with a
test override so handlers stay thin and hermetic under moto."""

import os

from . import config

_test_wiring = None


def set_test(dapier=None, provider=None, email=None, queue=None):
    global _test_wiring
    _test_wiring = (dapier, provider, email, queue)


def reset():
    global _test_wiring
    _test_wiring = None


def get():
    """Return (dapier_client, calendar_provider, email_port, queue_sender)."""
    if _test_wiring is not None:
        return _test_wiring
    from .calendar import RestGoogleCalendarProvider
    from .dapier import HttpDapierClient
    from .emailer import SesEmailPort
    from . import store as store_mod

    dapier = HttpDapierClient(
        config.DAPIER_BASE_URL,
        os.environ.get("DAPIER_WORKLOAD_IDENTITY", ""),
        os.environ.get("DAPIER_TOKEN_PATH", ""),
    )

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
