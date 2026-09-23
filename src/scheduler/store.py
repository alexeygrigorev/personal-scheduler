"""DynamoDB access. One table, one partition per aggregate.

Every booking-adjacent record for this single host lives in one table so a
booking loads with point reads and time ranges resolve through GSI1; ranking
and interval math happen in memory — one host holds hundreds of bookings,
not millions.

Layout (PK / SK):
- HOST#main / META — host profile, allowlist, defaults, pause state
- EVENTTYPE#<id> / META — event type (GSI1PK=EVENTTYPES for ordered listing)
- SLUG#<slug> / META — pointer {event_type_id} for slugs and retained aliases
- SCHEDULE#<id> / META — availability schedule
- BLOCK#<id> / META — manual blocks and closures (GSI1PK=BLOCKS)
- CALENDAR / META — calendar connection, non-secret metadata only
- BOOKING#<id> / META — booking (GSI1PK=BOOKING, GSI1SK=start#id)
- BOOKREF#<ref> / META — pointer {booking_id} for public references
- RESERVATION#<id> / META — active protected intervals (GSI1PK=RESERVATIONS)
- OPERATION#<id> / META — booking operations (GSI1PK=OP_<state>)
- IDEMPOTENCY#<key> / META — pointer {operation_id, fingerprint}
- MCAP#<hash> / META — invitee management capability (GSI1PK=MCAP#<booking>)
- NOTIFICATION#<id> / META — deliveries (GSI1PK=NOTIF_DUE while queued)
- HOSTLOCK / META — host-level booking mutex {owner, expires_at}
- AUDIT#<yyyy-mm> / <ts>#<rand> — append-only audit trail
- SEED#v1 / META — seed marker

Provider OAuth credentials are never stored here — Dapier owns them. Only
non-secret connection references, account identity, and health metadata.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import config
from .models import EventType, seed_event_types, seed_schedule, utcnow

_table = None


class ConditionalFailed(Exception):
    """A conditional write lost its race: the caller re-reads and decides."""


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(config.TABLE_NAME)
    return _table


def now_epoch() -> int:
    return int(time.time())


def now_iso() -> str:
    return utcnow().isoformat()


def _to_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _item(pk, sk, entity, data, *, gsi1pk=None, gsi1sk=None, ttl=None):
    item = {"PK": pk, "SK": sk, "entity": entity}
    item.update(_to_dynamo(data))
    if gsi1pk is not None:
        item["GSI1PK"] = gsi1pk
    if gsi1sk is not None:
        item["GSI1SK"] = gsi1sk
    if ttl is not None:
        item["ttl"] = int(ttl)
    return item


def _clean(item):
    return _from_dynamo(dict(item)) if item else None


def _conditional(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConditionalFailed() from exc
        raise


def _get(pk, sk):
    item = table().get_item(Key={"PK": pk, "SK": sk}).get("Item")
    return _clean(item)


def _query_gsi(gsi1pk, lo=None, hi=None, limit=None):
    condition = Key("GSI1PK").eq(gsi1pk)
    if lo is not None and hi is not None:
        condition = condition & Key("GSI1SK").between(lo, hi)
    elif hi is not None:
        # No lower bound: DynamoDB key attributes cannot be empty strings,
        # so an open-ended "everything up to hi" uses lte alone.
        condition = condition & Key("GSI1SK").lte(hi)
    elif lo is not None:
        condition = condition & Key("GSI1SK").gte(lo)
    params: dict = {"IndexName": "GSI1", "KeyConditionExpression": condition}
    if limit is not None:
        params["Limit"] = limit
    items, last = [], None
    while True:
        if last:
            params["ExclusiveStartKey"] = last
        page = table().query(**params)
        items.extend(_clean(i) for i in page.get("Items", []))
        last = page.get("LastEvaluatedKey")
        if not last or (limit is not None and len(items) >= limit):
            break
    return items[:limit] if limit is not None else items


# --- host ---------------------------------------------------------------

HOST_DEFAULTS = {
    "display_name": "Alexey Grigorev",
    "admin_emails": [],
    "timezone": "Europe/Berlin",
    "public_base_url": "",
    "contact_fallback": "",
    "email_sender": "",
    "host_notification_email": "",
    "pause_new_bookings": False,
    "default_grid_min": 30,
    "default_notice_hours": 12.0,
    "default_horizon_days": 60,
    "global_daily_count_limit": None,
    "global_daily_minutes_limit": None,
    "retention_days": 730,
}


def get_host() -> dict:
    item = _get("HOST#main", "META") or {}
    host = dict(HOST_DEFAULTS)
    host.update({k: v for k, v in item.items() if k in HOST_DEFAULTS})
    return host


def update_host(fields: dict):
    allowed = {k: v for k, v in fields.items() if k in HOST_DEFAULTS}
    if not allowed:
        return
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    table().update_item(
        Key={"PK": "HOST#main", "SK": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames={f"#{k}": k for k in allowed},
        ExpressionAttributeValues={f":v{i}": _to_dynamo(v) for i, v in enumerate(allowed.values())},
    )


def is_admin(email: str) -> bool:
    """Shared-auth identity proves who is calling; the host allowlist plus the
    bootstrap root admin decides whether that identity may administer. An
    ordinary community account signs in successfully and is still refused."""
    if not email:
        return False
    lowered = email.lower()
    if lowered in config.ROOT_ADMINS:
        return True
    return lowered in {e.lower() for e in (get_host().get("admin_emails") or [])}


# --- event types ----------------------------------------------------------

def _event_pointers(event_id, slug, aliases):
    return [f"SLUG#{slug}", *[f"SLUG#{a}" for a in (aliases or []) if a != slug]]


def put_event_type(data: dict):
    """Create or replace an event type. Slug and alias pointers move with it;
    stale pointers from a previous slug are removed. Existing bookings keep
    working: they reference the stable type id, never the slug."""
    et = EventType.from_item({**data, "id": data["id"]})
    errors = et.validate()
    if errors:
        raise ValueError("; ".join(errors))
    # A slug pointer maps one public address to one type, and writes go
    # through unguarded for every other field: without this check a save
    # carrying a colliding slug would silently repoint another type's page.
    previous = get_event_type(et.id)
    for pointer in _event_pointers(et.id, et.slug, et.aliases):
        holder = _get(pointer, "META")
        if holder and holder.get("event_type_id") not in (None, et.id):
            address = pointer[len("SLUG#"):]
            raise ValueError(
                f"The address /{address} already belongs to another meeting type.")
    writes = [
        {"Put": {
            "TableName": config.TABLE_NAME,
            "Item": _item(f"EVENTTYPE#{et.id}", "META", "event_type", et.to_item(),
                          gsi1pk="EVENTTYPES", gsi1sk=f"{et.position:06d}#{et.id}"),
        }}
    ]
    for pointer in _event_pointers(et.id, et.slug, et.aliases):
        writes.append({"Put": {
            "TableName": config.TABLE_NAME,
            "Item": _item(pointer, "META", "slug_pointer",
                          {"event_type_id": et.id, "slug": pointer[len("SLUG#"):]}, ttl=None),
        }})
    if previous:
        stale = set(_event_pointers(previous["id"], previous.get("slug", ""),
                                    previous.get("aliases", [])))
        for pointer in stale - set(_event_pointers(et.id, et.slug, et.aliases)):
            current = _get(pointer, "META")
            if current and current.get("event_type_id") == et.id:
                writes.append({"Delete": {
                    "TableName": config.TABLE_NAME,
                    "Key": {"PK": pointer, "SK": "META"},
                }})
    client = table().meta.client
    for i in range(0, len(writes), 100):
        client.transact_write_items(TransactItems=writes[i:i + 100])


def get_event_type(event_id: str) -> dict | None:
    item = _get(f"EVENTTYPE#{event_id}", "META")
    if not item or item.get("entity") != "event_type":
        return None
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}


def resolve_slug(slug: str) -> dict | None:
    pointer = _get(f"SLUG#{slug}", "META")
    if not pointer or pointer.get("entity") != "slug_pointer":
        return None
    return get_event_type(pointer.get("event_type_id", ""))


def list_event_types() -> list[dict]:
    items = _query_gsi("EVENTTYPES")
    return [{k: v for k, v in i.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}
            for i in items]


def ensure_seed():
    """Idempotent first-run seed: four event types plus the default schedule.
    Safe under concurrent cold starts — the marker claim decides one winner
    and every seed write is conditional."""
    try:
        table().put_item(
            Item=_item("SEED#v1", "META", "seed_marker", {"version": 1}),
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    for et in seed_event_types():
        try:
            put_event_type(et.to_item())
        except ConditionalFailed:
            pass
    schedule = seed_schedule()
    table().put_item(
        Item=_item("SCHEDULE#default", "META", "schedule", schedule),
        ConditionExpression="attribute_not_exists(PK)",
    )
    return True


# --- schedules and blocks ---------------------------------------------------

def get_schedule(schedule_id: str) -> dict | None:
    item = _get(f"SCHEDULE#{schedule_id}", "META")
    if not item or item.get("entity") != "schedule":
        return None
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "entity")}


def put_schedule(data: dict):
    item = {"id": data["id"], "name": data.get("name", data["id"]),
            "timezone": data.get("timezone", "Europe/Berlin"),
            "weekly_windows": data.get("weekly_windows", {}),
            "overrides": data.get("overrides", {}),
            "version": int(data.get("version", 1))}
    table().put_item(Item=_item(f"SCHEDULE#{item['id']}", "META", "schedule", item))


def list_blocks() -> list[dict]:
    items = _query_gsi("BLOCKS")
    return [{k: v for k, v in i.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}
            for i in items]


def add_block(block: dict) -> dict:
    from . import security as _security
    item = {"id": block.get("id") or _security.new_id("blk_"),
            "kind": block.get("kind", "interval"),
            "start_iso": block.get("start_iso", ""),
            "end_iso": block.get("end_iso", ""),
            "full_day_date": block.get("full_day_date", ""),
            "reason": block.get("reason", ""),
            "created_at": now_iso()}
    table().put_item(Item=_item(f"BLOCK#{item['id']}", "META", "block", item,
                                gsi1pk="BLOCKS", gsi1sk=item["created_at"]))
    return item


def delete_block(block_id: str):
    table().delete_item(Key={"PK": f"BLOCK#{block_id}", "SK": "META"})


# --- calendar connection (non-secret metadata only) --------------------------

CALENDAR_DEFAULTS = {
    "dapier_connection_ref": "calendar-alexey",
    "expected_provider": "google",
    "expected_account": "",
    "selected_calendar_id": "",
    "capabilities": {},
    "health": {},
    "last_check": "",
}


def get_calendar_connection() -> dict:
    item = _get("CALENDAR", "META") or {}
    connection = dict(CALENDAR_DEFAULTS)
    connection.update({k: v for k, v in item.items() if k in CALENDAR_DEFAULTS})
    return connection


def update_calendar_connection(fields: dict):
    allowed = {k: v for k, v in fields.items() if k in CALENDAR_DEFAULTS}
    if not allowed:
        return
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    table().update_item(
        Key={"PK": "CALENDAR", "SK": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames={f"#{k}": k for k in allowed},
        ExpressionAttributeValues={f":v{i}": _to_dynamo(v) for i, v in enumerate(allowed.values())},
    )


# --- bookings ---------------------------------------------------------------

def put_booking(data: dict):
    item = dict(data)
    table().meta.client.transact_write_items(TransactItems=[
        {"Put": {
            "TableName": config.TABLE_NAME,
            "Item": _item(f"BOOKING#{item['id']}", "META", "booking", item,
                          gsi1pk="BOOKING", gsi1sk=f"{item['start_iso']}#{item['id']}"),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
        {"Put": {
            "TableName": config.TABLE_NAME,
            "Item": _item(f"BOOKREF#{item['reference']}", "META", "booking_pointer",
                          {"booking_id": item["id"]}),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ])


def get_booking(booking_id: str) -> dict | None:
    item = _get(f"BOOKING#{booking_id}", "META")
    if not item or item.get("entity") != "booking":
        return None
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}


def get_booking_by_reference(reference: str) -> dict | None:
    pointer = _get(f"BOOKREF#{reference}", "META")
    if not pointer or pointer.get("entity") != "booking_pointer":
        return None
    return get_booking(pointer.get("booking_id", ""))


def update_booking(booking_id: str, fields: dict, expected_revision: int | None = None):
    """Optimistic revision guard: a stale browser cannot overwrite a newer
    cancellation or reschedule."""
    allowed = {k: v for k, v in fields.items() if k != "id"}
    allowed["updated_at"] = now_iso()
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    params: dict = {
        "Key": {"PK": f"BOOKING#{booking_id}", "SK": "META"},
        "UpdateExpression": f"SET {expr}",
        "ExpressionAttributeNames": {f"#{k}": k for k in allowed},
        "ExpressionAttributeValues": {f":v{i}": _to_dynamo(v)
                                      for i, v in enumerate(allowed.values())},
    }
    if expected_revision is not None:
        params["ConditionExpression"] = "revision = :rev"
        params["ExpressionAttributeValues"][":rev"] = expected_revision
    gsi_moves = "start_iso" in allowed
    if gsi_moves:
        # start_iso feeds the time index; a reschedule moves it.
        params["UpdateExpression"] += ", GSI1SK = :gsi"
        params["ExpressionAttributeValues"][":gsi"] = \
            f"{allowed['start_iso']}#{booking_id}"
    try:
        table().update_item(**params)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConditionalFailed() from exc
        raise


def query_bookings(start_lo: str, start_hi: str, *, status: str | None = None,
                   event_type_id: str | None = None, limit: int | None = None) -> list[dict]:
    items = _query_gsi("BOOKING", start_lo, start_hi, limit=limit)
    out = []
    for item in items:
        data = {k: v for k, v in item.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}
        if status and data.get("status") != status:
            continue
        if event_type_id and data.get("event_type_id") != event_type_id:
            continue
        out.append(data)
    return out


def active_protection(window_lo: str, window_hi: str,
                      exclude_booking_id: str = "") -> list[tuple[str, str, str]]:
    """Protected intervals (bookings plus active reservations) intersecting a
    window. Local protection holds even before a new event appears in the
    provider's next busy response."""
    from .models import parse_iso
    out = []
    for booking in query_bookings(window_lo, window_hi):
        if booking.get("status") not in ("pending_confirmation", "confirmed"):
            continue
        if exclude_booking_id and booking.get("id") == exclude_booking_id:
            continue
        start = parse_iso(booking["start_iso"]).timestamp()
        end = parse_iso(booking["end_iso"]).timestamp()
        protected_start = datetime.fromtimestamp(
            start - int(booking.get("pre_buffer_min") or 0) * 60, timezone.utc)
        protected_end = datetime.fromtimestamp(
            end + int(booking.get("post_buffer_min") or 0) * 60, timezone.utc)
        out.append((protected_start.isoformat(), protected_end.isoformat(), booking["id"]))
    for reservation in _query_gsi("RESERVATIONS"):
        if reservation.get("state") != "active":
            continue
        if exclude_booking_id and reservation.get("booking_id") == exclude_booking_id:
            continue
        if reservation.get("protected_end", "") >= window_lo \
                and reservation.get("protected_start", "") <= window_hi:
            out.append((reservation["protected_start"], reservation["protected_end"],
                        reservation.get("id", "")))
    return out


# --- reservations -------------------------------------------------------------

def put_reservation(data: dict):
    item = dict(data)
    lease = int(data.get("lease_expires_epoch") or (now_epoch() + 120))
    table().put_item(Item=_item(f"RESERVATION#{item['id']}", "META", "reservation", item,
                                gsi1pk="RESERVATIONS", gsi1sk=item["protected_start"], ttl=lease + 86400))


def list_active_reservations() -> list[dict]:
    """Full active reservation items (limits accounting needs duration/type,
    not just intervals)."""
    return [{k: v for k, v in i.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}
            for i in _query_gsi("RESERVATIONS") if i.get("state") == "active"]


def update_reservation(reservation_id: str, fields: dict):
    allowed = {k: v for k, v in fields.items() if k != "id"}
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    names = {f"#{k}": k for k in allowed}
    values = {f":v{i}": _to_dynamo(v) for i, v in enumerate(allowed.values())}
    if allowed.get("state") != "active":
        expr += ", GSI1PK = :gone"
        values[":gone"] = "RESERVATIONS_DONE"
    table().update_item(
        Key={"PK": f"RESERVATION#{reservation_id}", "SK": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# --- operations and idempotency -----------------------------------------------

def _op_gsi(state: str) -> tuple[str, str]:
    return f"OP_{state}", now_iso()


def claim_operation(operation: dict):
    """Register an idempotent booking operation. Reusing its key with the same
    payload returns the same operation; reusing it with different data is a
    conflict, never a silent change of intent."""
    op = dict(operation)
    gsi1pk, gsi1sk = _op_gsi(op.get("state", "in_progress"))
    try:
        table().meta.client.transact_write_items(TransactItems=[
            {"Put": {
                "TableName": config.TABLE_NAME,
                "Item": _item(f"OPERATION#{op['id']}", "META", "operation", op,
                              gsi1pk=gsi1pk, gsi1sk=gsi1sk,
                              ttl=now_epoch() + 90 * 86400),
                "ConditionExpression": "attribute_not_exists(PK)",
            }},
            {"Put": {
                "TableName": config.TABLE_NAME,
                "Item": _item(f"IDEMPOTENCY#{op['idempotency_key']}", "META",
                              "idempotency_pointer",
                              {"operation_id": op["id"],
                               "payload_fingerprint": op.get("payload_fingerprint", "")}),
                "ConditionExpression": "attribute_not_exists(PK)",
            }},
        ])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            raise ConditionalFailed() from exc
        raise
    return op


def get_operation(operation_id: str) -> dict | None:
    item = _get(f"OPERATION#{operation_id}", "META")
    if not item or item.get("entity") != "operation":
        return None
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}


def get_operation_by_key(idempotency_key: str) -> dict | None:
    pointer = _get(f"IDEMPOTENCY#{idempotency_key}", "META")
    if not pointer or pointer.get("entity") != "idempotency_pointer":
        return None
    operation = get_operation(pointer.get("operation_id", ""))
    if operation:
        operation["_payload_fingerprint"] = pointer.get("payload_fingerprint", "")
    return operation


def update_operation(operation_id: str, fields: dict):
    allowed = {k: v for k, v in fields.items() if k != "id"}
    allowed["updated_at"] = now_iso()
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    names = {f"#{k}": k for k in allowed}
    values = {f":v{i}": _to_dynamo(v) for i, v in enumerate(allowed.values())}
    if "state" in allowed:
        expr += ", GSI1PK = :gsi, GSI1SK = :gsk"
        values[":gsi"], values[":gsk"] = f"OP_{allowed['state']}", now_iso()
    table().update_item(
        Key={"PK": f"OPERATION#{operation_id}", "SK": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def list_pending_operations() -> list[dict]:
    out = []
    for state in ("in_progress", "unknown"):
        for item in _query_gsi(f"OP_{state}"):
            out.append({k: v for k, v in item.items()
                        if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")})
    return out


# --- host-level booking mutex ---------------------------------------------------

def acquire_host_lock(owner: str, ttl_seconds: int = 60) -> bool:
    """Serialize overlapping changes across the host's complete booking
    inventory. Locking by event type, start time, or session is insufficient:
    two requests with different starts, types, or durations still conflict."""
    now = now_epoch()
    try:
        _conditional(
            table().put_item,
            Item=_item("HOSTLOCK", "META", "host_lock",
                       {"owner": owner, "expires_at": now + ttl_seconds}),
            ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ConditionalFailed:
        return False


def release_host_lock(owner: str):
    try:
        _conditional(
            table().delete_item,
            Key={"PK": "HOSTLOCK", "SK": "META"},
            ConditionExpression="#o = :owner",
            ExpressionAttributeNames={"#o": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )
    except ConditionalFailed:
        pass


# --- invitee management capabilities --------------------------------------------

def put_capability(token_hash: str, data: dict):
    item = dict(data)
    item["token_hash"] = token_hash
    table().put_item(Item=_item(f"MCAP#{token_hash}", "META", "management_capability", item,
                                gsi1pk=f"MCAP#{item['booking_id']}", gsi1sk=item["created_at"],
                                ttl=int(item.get("expires_epoch") or 0) or None))


def get_capability(token_hash: str) -> dict | None:
    item = _get(f"MCAP#{token_hash}", "META")
    if not item or item.get("entity") != "management_capability":
        return None
    if item.get("revoked"):
        return None
    try:
        if item.get("expires_at") and datetime.fromisoformat(item["expires_at"]) \
                <= datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}


def revoke_booking_capabilities(booking_id: str, except_hash: str = ""):
    for item in _query_gsi(f"MCAP#{booking_id}"):
        token_hash = item.get("token_hash", "")
        if except_hash and token_hash == except_hash:
            continue
        table().update_item(
            Key={"PK": f"MCAP#{token_hash}", "SK": "META"},
            UpdateExpression="SET revoked = :one",
            ExpressionAttributeValues={":one": True},
        )


# --- notifications ---------------------------------------------------------------

def queue_notification(data: dict) -> dict:
    from . import security as _security
    from datetime import timedelta
    item = dict(data)
    item.setdefault("id", _security.new_id("ntf_"))
    item.setdefault("status", "queued")
    item.setdefault("attempts", 0)
    try:
        scheduled = datetime.fromisoformat(item.get("scheduled_for", ""))
    except ValueError:
        scheduled = datetime.now(timezone.utc)
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    ttl = int((scheduled + timedelta(days=30)).timestamp())
    due = item["status"] == "queued"
    table().put_item(Item=_item(f"NOTIFICATION#{item['id']}", "META", "notification", item,
                                gsi1pk="NOTIF_DUE" if due else "NOTIF_OTHER",
                                gsi1sk=item.get("scheduled_for", ""), ttl=ttl))
    return item


def due_notifications(now_iso_str: str, limit: int = 25) -> list[dict]:
    items = _query_gsi("NOTIF_DUE", hi=now_iso_str + "#~", limit=limit)
    return [{k: v for k, v in i.items() if k not in ("PK", "SK", "entity", "GSI1PK", "GSI1SK")}
            for i in items if i.get("status") == "queued"]


def update_notification(notification_id: str, fields: dict):
    allowed = {k: v for k, v in fields.items() if k != "id"}
    expr = ", ".join(f"#{k}=:v{i}" for i, k in enumerate(allowed))
    names = {f"#{k}": k for k in allowed}
    values = {f":v{i}": _to_dynamo(v) for i, v in enumerate(allowed.values())}
    if allowed.get("status") and allowed["status"] != "queued":
        expr += ", GSI1PK = :gone"
        values[":gone"] = "NOTIF_OTHER"
    table().update_item(
        Key={"PK": f"NOTIFICATION#{notification_id}", "SK": "META"},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def suppress_booking_notifications(booking_id: str, kinds=("reminder",)):
    """A canceled or rescheduled booking must never send a stale reminder."""
    for item in _query_gsi("NOTIF_DUE", limit=100):
        if item.get("booking_id") != booking_id or item.get("kind") not in kinds:
            continue
        if item.get("status") == "queued":
            update_notification(item["id"], {"status": "suppressed"})


# --- audit -----------------------------------------------------------------------

def audit(actor: str, action: str, entity: str, entity_id: str,
          actor_ref: str = "", result: str = ""):
    """Append-only audit trail. Callers sanitize first: no provider tokens,
    no management-token values, no personal data beyond references."""
    day = datetime.now(timezone.utc).strftime("%Y-%m")
    stamp = now_iso()
    table().put_item(Item={
        "PK": f"AUDIT#{day}", "SK": f"{stamp}#{entity}#{entity_id}",
        "entity": "audit", "actor": actor[:200], "actor_ref": actor_ref[:200],
        "action": action[:200], "affected_entity": entity[:100],
        "affected_id": entity_id[:200], "at": stamp, "result": result[:2000],
        "ttl": now_epoch() + 5 * 365 * 86400,
    })
