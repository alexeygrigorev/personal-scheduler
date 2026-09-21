"""Outbound email port (spec section 12).

Two separated responsibilities: the calendar provider sends the actual
invitation and meeting updates; this capability sends the application
confirmation, secure management links, and reminders. A failed email never
cancels a confirmed meeting — delivery retries while the booking stands.
"""

from __future__ import annotations


class EmailError(Exception):
    pass


class EmailPort:
    def send(self, *, to: str, subject: str, text: str, reply_to: str = "") -> str:
        """Deliver one message; return a provider message id. Raises
        EmailError on transient or permanent failure (the worker retries)."""
        raise NotImplementedError


class InMemoryEmailPort(EmailPort):
    """Test double: an inspectable outbox with failure injection."""

    def __init__(self):
        self.outbox: list[dict] = []
        self.fail_next: int = 0
        self.fail_recipients: set[str] = set()

    def send(self, *, to: str, subject: str, text: str, reply_to: str = "") -> str:
        if self.fail_next > 0 or to in self.fail_recipients:
            if self.fail_next > 0:
                self.fail_next -= 1
            raise EmailError(f"injected delivery failure for {to}")
        message_id = f"msg-{len(self.outbox) + 1}"
        self.outbox.append({"to": to, "subject": subject, "text": text,
                            "reply_to": reply_to, "id": message_id})
        return message_id


class SesEmailPort(EmailPort):
    def __init__(self, sender: str, region: str = "eu-west-1"):
        import boto3
        self.sender = sender
        self.client = boto3.client("ses", region_name=region)

    def send(self, *, to: str, subject: str, text: str, reply_to: str = "") -> str:
        try:
            response = self.client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": [to]},
                Message={"Subject": {"Data": subject, "Charset": "UTF-8"},
                         "Body": {"Text": {"Data": text, "Charset": "UTF-8"}}},
                ReplyToAddresses=[reply_to] if reply_to else [],
            )
            return response["MessageId"]
        except Exception as exc:
            raise EmailError(f"ses delivery failed: {exc}") from exc


def confirmation_subject(event_title: str) -> str:
    return f"Confirmed: {event_title}"


def render_confirmation(*, event_title: str, start_human: str, duration_min: int,
                        timezone_name: str, joining: str, manage_url: str,
                        reference: str) -> str:
    return (
        f"Your booking is confirmed.\n\n"
        f"Event: {event_title}\n"
        f"When: {start_human} ({timezone_name}), {duration_min} minutes\n"
        f"Joining: {joining}\n"
        f"Reference: {reference}\n\n"
        f"Manage or cancel your booking here:\n{manage_url}\n"
    )


def render_host_notice(*, event_title: str, start_human: str, invitee_name: str,
                       invitee_email: str, agenda: str) -> str:
    lines = [f"New booking: {event_title}", f"When: {start_human}",
             f"Invitee: {invitee_name} <{invitee_email}>"]
    if agenda:
        lines.append(f"Agenda:\n{agenda}")
    return "\n".join(lines) + "\n"
