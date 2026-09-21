"""Worker Lambda: email delivery, reminders, and booking reconciliation.

Triggered by SQS work items and by a 5-minute schedule tick. The tick keeps
reminders and pending-operation recovery running even when no new work
arrives; every unit of work tolerates duplicate execution, restarts, and
overlapping deployments.
"""

import json
import logging

from scheduler import service, store, wiring

logging.getLogger().setLevel(logging.INFO)


def _mark_health(provider, dapier_client):
    """Non-secret integration health for the admin overview. Failures mark
    degraded/unavailable; they never raise out of maintenance."""
    connection = store.get_calendar_connection()
    health = dict(connection.get("health") or {})
    calendar_id = connection.get("selected_calendar_id", "")
    if provider is not None and calendar_id:
        try:
            provider.check_writable(calendar_id)
            health["status"] = "ok"
        except Exception:
            health["status"] = "degraded"
    else:
        health["status"] = "not_configured"
    if dapier_client is not None and connection.get("dapier_connection_ref"):
        try:
            dapier_client.get_access(connection["dapier_connection_ref"], [])
            health["dapier"] = "ok"
        except Exception:
            health["dapier"] = "unavailable"
    else:
        health["dapier"] = "not_configured"
    try:
        from scheduler.store import now_iso
        store.update_calendar_connection({"health": health, "last_check": now_iso()})
    except Exception:
        logging.exception("health mark failed")


def handle_tick():
    dapier_client, provider, email_port, _queue = wiring.get()
    outcome = {}
    if email_port is not None:
        outcome["notifications"] = service.process_due_notifications(email_port)
    outcome["reconciliation"] = service.reconcile_pending_operations(provider=provider)
    outcome["sync"] = service.periodic_sync(provider=provider)
    _mark_health(provider, dapier_client)
    return {"ok": True, **outcome}


def handle_record(record):
    try:
        payload = json.loads(record.get("body") or "{}")
    except ValueError:
        return {"ok": False, "reason": "invalid_json"}
    kind = payload.get("kind", "notify")
    dapier_client, provider, email_port, _queue = wiring.get()
    if kind == "notify" and email_port is not None:
        return {"ok": True, **service.process_due_notifications(email_port)}
    if kind == "reconcile":
        return {"ok": True, **service.reconcile_pending_operations(provider=provider)}
    if kind == "sync":
        return {"ok": True, **service.periodic_sync(provider=provider)}
    return {"ok": True, "kind": kind}


def lambda_handler(event, _context):
    records = event.get("Records") or []
    if not records:
        try:
            return handle_tick()
        except Exception:
            logging.exception("worker tick failed")
            raise
    failures = []
    for record in records:
        try:
            handle_record(record)
        except Exception:
            logging.exception("work item failed id=%s", record.get("messageId", "?"))
            if record.get("messageId"):
                failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
