# Personal Scheduling — Functional and Integration Specification

Version: 1.0  
Prepared: 21 September 2026  
Audience: coding agents implementing a personal Calendly replacement  
Scope: one host, one connected calendar, multiple booking links  
Implementation policy: technology-neutral; no framework, database, hosting platform, or programming language is prescribed.

## 1. Objective and interpretation of the request

Build an independently operated scheduling application that replaces the host's core Calendly workflow. Invitees open a link, see bookable times, select a slot, enter their details, and receive a calendar invitation. The host manages meeting types, availability, existing bookings, and integrations through a private administration interface.

The essential requirements are one connected calendar; shared conflict checking across all links; separate fixed-duration meeting links; one additional link with selectable durations of 30, 60, 90, 120, 150, or 180 minutes; and external-provider credentials obtained through Dapier rather than managed by this application.

A long meeting must occupy **one uninterrupted interval**. Three hours must produce one three-hour booking and one calendar event, not six independent half-hour bookings.

“MUST” indicates a release requirement. “Proposed default” indicates an editable starting configuration, not a setting recovered from Calendly. Optional features are explicitly identified. Do not expand this into a multi-tenant scheduling platform.

### 1.1 Evidence and limits of the existing-page inspection

The supplied reference pages were:

- `https://calendly.com/dtc-alexey/30min`
- `https://calendly.com/dtc-alexey/`

Both were requested. The root page exposed the title “Calendly - Alexey Grigorev,” but their booking-page contents were not available in the accessible page output. Existing titles, descriptions, questions, availability hours, conferencing settings, and the complete event-type inventory could not be verified. No booking was made and no account setting was changed. [S1]

This specification therefore reconstructs the intended workflow from the request rather than claiming an exact migration of inaccessible settings. The initial interpretation is three fixed-duration links—30, 30, and 60 minutes—plus a fourth, variable-duration link. Names and slugs below are editable seed data.

The Dapier repository `DataTalksClub/dapier` was inspected. Its README describes the shared-auth OAuth token factory as proposed; the inspected provider registry contains YouTube and Dropbox, and its token-factory document specifies a broader connection/agent model. Calendar support and the headless token-consumption path must be verified or added in Dapier before production launch. The existence of a specification is not evidence that its API is deployed. [S2–S4]

### 1.2 Explicit assumptions

Use “DataTalks.Club” and “AI Shipping Labs” as working display names, subject to host editing. Use Google Calendar as the proposed first provider, not as a verified fact about the current Calendly connection. Use `Europe/Berlin` as the proposed host availability timezone. The design must not hard-code these assumptions into booking logic.

## 2. Product boundary

The first release MUST include the public booking flow, a private single-host admin interface, the four seeded meeting types, fixed and selectable durations, recurring availability and date overrides, calendar conflict detection and event lifecycle management, Dapier credential integration, calendar invitations, transactional confirmations, rescheduling, cancellation, configurable email reminders, and recovery from interrupted operations.

The following are outside the first release: multiple hosts, round-robin assignment, collective availability, group events with seat capacity, paid bookings, subscriptions, CRM integrations, lead routing, SMS or WhatsApp, meeting polls, AI assistants, recordings, transcription, recurring booking series, automatic community-membership verification, multiple calendar providers, and a general-purpose workflow builder.

The host can create additional event types later without application code changes. All still belong to the same host and use the same selected calendar.

## 3. Initial meeting types and link behavior

| Seed key | Proposed display name | Proposed path | Duration | Proposed visibility |
|---|---|---|---|---|
| community-30 | AI Shipping Labs — Community Call | `/community` | Fixed: 30 minutes | Unlisted |
| dtc-30 | DataTalks.Club — General Chat | `/dtc` | Fixed: 30 minutes | Listed |
| general-60 | Meeting with Alexey | `/60min` | Fixed: 60 minutes | Listed |
| flexible | Extended Meeting / Podcast | `/extended` | Selectable: 30, 60, 90, 120, 150, 180 minutes | Unlisted |

All four types MUST share one conflict domain. A booking through any link removes overlapping availability from every other link, even when they use different schedules or durations.

Keep the generic DataTalks.Club link fixed at 30 minutes. The variable-duration behavior belongs to a separate link. Its proposed default selection is 30 minutes; the visitor explicitly changes it for longer sessions. Arbitrary numbers, zero, negative durations, and values above 180 minutes MUST be rejected server-side.

Visibility has three states: **listed**, shown on the public landing page; **unlisted**, accessible by its direct link but omitted from the landing page, navigation, and sitemap; and **disabled**, unavailable for new bookings. Unlisted is not access control: anyone with the link may book. Do not imply that it verifies community membership.

An event type has a stable internal ID and an editable public slug. Support retaining old application-owned slugs as aliases. A `/30min` alias may point to the intended replacement after the host confirms the mapping. Never infer that this application can redirect URLs on Calendly's domain.

Disabling or editing an event type MUST NOT cancel or silently modify existing bookings. Existing booking-management links continue to work subject to the applicable booking policy.

## 4. Public interface

### 4.1 Public landing page

Show the host name, optional avatar, short introduction, and cards for enabled listed meeting types. Each card shows the title, short description, duration or duration range, and a clear booking action. Do not require an invitee account or a calendar connection.

When no listed types are enabled, show a deliberate empty state rather than an error. A direct unlisted link must still work.

### 4.2 Booking page layout

Use a familiar scheduling layout rather than a pixel-for-pixel Calendly clone. On desktop, present meeting information on the left, a month/date picker in the middle, and available times on the right. On mobile, stack the same information in the booking sequence without horizontal scrolling.

The meeting-information panel shows host, title, description, duration, location or conferencing mode, and any booking instructions. The scheduling area shows timezone selection, month navigation, selectable days, available start/end times, and a concise summary of the current selection.

For the flexible type, place the duration selector **before** the date and time choices. Offer exactly “30 min,” “1 hour,” “1.5 hours,” “2 hours,” “2.5 hours,” and “3 hours.” Changing duration recomputes both available days and start times; clear the selected time when it is no longer valid. Do not show a 30-minute slot as a bookable three-hour session merely because its start is free.

Highlight dates with at least one valid slot for the selected duration. Disable dates without one. Times should show their end as well as their start, especially for longer bookings. Provide an action to move to the next available day without searching beyond the booking horizon.

Default to showing bookable slots only. An optional “Show unavailable times” display may show disabled intervals labeled “Unavailable,” but must not reveal whether they are unavailable because of another appointment, working hours, or a private scheduling rule. The host's admin view can distinguish those reasons.

### 4.3 Timezone interaction

Detect the invitee's browser timezone, show it prominently, and allow manual override. Preserve that choice throughout the flow. Show the selected date, start, end, timezone name, and applicable UTC offset in the final review. Offer a 12/24-hour display preference.

An availability query for a viewer-local date must include every intersecting host-local date; do not assume the visitor and host share a calendar day. Changing timezone must not silently change an already-selected absolute instant. Re-render it and ask the visitor to review the resulting date/time.

### 4.4 Invitee details and confirmation

After time selection, show a form with required name and email and an optional discussion topic or notes field. Make “Purpose / agenda” required by default for the extended/podcast type. The host can configure short text, long text, and single-choice questions, their required status, order, and length limits. File uploads are outside scope.

Keep the selected meeting, duration, date, start/end, and timezone visible above or beside the form. Include a back action that preserves valid form entries. Display field-level validation messages. Booking is automatic: there is no approval queue in this release.

The submit action MUST clearly distinguish “Confirm booking,” “Confirming…,” successful confirmation, a slot conflict, and an unresolved provider operation. Disable repeated clicks for usability, but do not rely on this as the duplicate-prevention mechanism. A selected slot is not reserved while the visitor fills in the form; explain that availability is confirmed on submission.

After success, show the booking reference, meeting summary, invitee email, joining information or its pending status, and secure cancellation/rescheduling actions. Offer a calendar-file fallback without creating a second invitation or a second host-calendar event.

### 4.5 Required public states and accessibility

Handle loading, no slots on a date, no slots in the current horizon, disabled type, invalid link, expired management link, lost slot, validation error, provider disconnection, temporary service failure, and an operation still being reconciled. Preserve entered details after recoverable errors. Error messages must not expose provider responses, tokens, or private calendar metadata.

The complete flow MUST work with keyboard navigation and a screen reader. Use visible labels, clear focus states, accessible date/time controls, adequate contrast, and text or icons in addition to color for availability. Avoid requiring hover, drag-and-drop, or precision tapping.

## 5. Host administration interface

Admin access is restricted to the configured host identity. A public visitor or ordinary community account must not gain administration rights.

| Area | Required controls and information |
|---|---|
| Overview | Upcoming bookings, integration health, pending operations, actionable failures, and a global pause-new-bookings control. |
| Event types | Create, duplicate, edit, enable/disable, list/unlist, reorder cards, preview, and copy links. Edit titles, descriptions, slugs, durations, questions, location, schedule, buffers, limits, and notification settings. |
| Availability | Reusable weekly schedules; multiple windows per day; date overrides; holidays and full-day closures; manual time blocks; host timezone. |
| Bookings | Upcoming, past, canceled, and pending views; filters by type/date/status; search by invitee; booking detail, notes, calendar reference, and operation history. |
| Booking actions | Cancel, reschedule, and resend a confirmation; show pending or failed integration work. Do not allow editing or deleting unrelated calendar appointments. |
| Calendar | Calendar account identity, explicit selected calendar, read/write health, last successful check, and synchronization status. |
| Dapier | Connection reference and non-secret health metadata; connect/reconnect action leading to Dapier, not a raw-token form. |
| Settings | Host profile, public base URL, contact fallback, timezone, email sender, defaults, and data-retention choices. |

Provide a day/week availability preview for a selected event type and duration. Show busy intervals, open intervals, and non-bookable intervals. An owner-only “Why is this time unavailable?” explanation must identify conflicts, insufficient uninterrupted time, buffers, notice, horizon, limits, closures, or integration failure. Prefer “Busy” for unrelated events; their titles are not needed for this product.

Configuration edits affect future offers. Store the relevant configuration snapshot with each booking so later title, duration, buffer, or notification edits do not rewrite existing appointments. Warn the host when a configuration change makes an existing booking fall outside newly configured hours; do not move it automatically.

## 6. Availability and slot-generation rules

### 6.1 Schedule configuration

A schedule consists of a named IANA timezone, recurring weekly time windows, and date-specific replacements. Allow split days, such as 09:00–12:00 and 14:00–17:00. Event types can share a schedule or use different schedules.

A date override replaces that schedule's weekly hours for that date. Global host closures and manual blocks always subtract availability across all types. A per-type schedule cannot reopen a globally closed date.

Support minimum scheduling notice, maximum booking horizon, start-time increment, pre-meeting buffer, post-meeting buffer, optional daily booking-count limit, and optional daily booked-minutes limit. Apply global limits across types, with optional additional per-type limits. Count the meeting's actual duration, not buffers, toward booked-minute limits. Attribute a booking to its host-local start date. Pending reservations count toward limits until safely resolved.

Proposed initial policy: a 30-minute start grid, 12 hours' minimum notice, a 60-day booking horizon, zero buffers, and no daily limits. These are suggestions, not recovered preferences. Do not publish bookable working hours until the host explicitly configures them.

Interpret the horizon as a cutoff at the end of the host-local date N days after today. The entire meeting must end before or at that cutoff. Enforce minimum notice against the start instant, using server time, including when a page was opened earlier.

### 6.2 Exact interval semantics

Represent time intervals as half-open intervals `[start, end)`. A meeting that ends at 11:00 does not overlap one that starts at 11:00 when buffers are zero.

For a candidate meeting with start `s`, duration `d`, pre-buffer `p`, and post-buffer `q`:

```text
meeting interval   = [s, s + d)
protected interval = [s - p, s + d + q)
```

A valid offer MUST have its entire protected interval inside one continuous availability window, within applicable date/horizon boundaries, and free of external busy intervals, other bookings' protected intervals, and active reservations. It must satisfy notice, grid, duration, and booking-limit rules.

Existing application bookings retain their own buffer snapshots. External events block their provider-reported busy interval; do not automatically add an extra global buffer to every external event. Candidate buffers still protect time around the proposed meeting.

Protected intervals are exclusive. If one booking has a 15-minute post-buffer and the next has a 15-minute pre-buffer, the required gap between their meeting times is 30 minutes. This is an explicit product rule, not a claim about Calendly's exact buffer-combination behavior. Buffer time does not lengthen the invitee's calendar event.

Align proposed starts to the configured grid in the schedule's timezone. A 30-minute grid means local `:00` and `:30` starts, not a grid that shifts because a busy event ended at `:07`. Merge adjacent available windows only when there is genuinely no gap.

### 6.3 Busy-time interpretation

Use the provider's effective busy/free semantics. Busy all-day events, recurring occurrences, modified exceptions, busy out-of-office/focus blocks, and multi-day events must block correctly. Events explicitly marked free must not block. Deleted or canceled instances must not block. Treat uncertain or unanswered invitations conservatively when the provider reports them busy.

Do not reconstruct availability from event titles, such as guessing that an event called “Vacation” must be busy. Do not infer that an empty response means free if the provider returned an authorization, pagination, or per-calendar error. Any incomplete busy-time result makes that range unavailable for new confirmations. Google provides a free/busy query API; individual event records are additionally needed for ownership-aware reconciliation and rescheduling. [S5, S6]

Compute availability from the union of provider busy intervals and local protected reservations/bookings. Local protection is necessary even before a new event appears in the provider's next response.

### 6.4 Long-duration examples

Given a single free window 09:00–12:00, a 30-minute start grid, and no buffers:

| Duration | Valid starts |
|---|---|
| 30 minutes | 09:00, 09:30, 10:00, 10:30, 11:00, 11:30 |
| 60 minutes | 09:00, 09:30, 10:00, 10:30, 11:00 |
| 90 minutes | 09:00, 09:30, 10:00, 10:30 |
| 120 minutes | 09:00, 09:30, 10:00 |
| 150 minutes | 09:00, 09:30 |
| 180 minutes | 09:00 |

Separate free windows 09:00–10:30 and 11:00–12:30 do **not** support a three-hour booking. Neither may a meeting cross a lunch break. A three-hour booking with 15-minute buffers on both sides needs a continuous 3.5-hour availability window.

### 6.5 Timezone and daylight-saving correctness

Store booked start/end instants unambiguously and retain the host schedule timezone and invitee display timezone. Weekly hours recur in their configured timezone, not at a fixed UTC offset. A timezone setting change affects future offers, never silently shifts existing bookings.

Handle daylight-saving transitions: do not offer nonexistent local times; distinguish repeated local times by offset; use elapsed minutes for meeting duration. Calculate all-day event boundaries in the event/calendar timezone before converting to instants. A date transition in another timezone must not truncate a busy interval. Google event fields expose explicit timezone information and exclusive end times. [S6]

## 7. Booking integrity and concurrent requests

### 7.1 Creation flow

On submission, the application MUST perform the following logical sequence:

1. Validate the event type, its enabled state, selected duration, name/email/questions, start instant, and current policy. Derive the host, calendar, and end time server-side; do not accept them as authoritative client inputs.
2. Register an idempotent booking operation. Reusing its key with the same payload returns the same operation or booking; reusing it with different data is rejected.
3. Serialize overlapping changes across the host's complete booking inventory, check local protected intervals and limits, and create a durable reservation for the complete protected interval.
4. Obtain usable provider access through Dapier and perform a fresh, complete calendar conflict check. Revalidate relevant configuration before the write. A cached offer is not permission to book.
5. Create exactly one calendar event, bound to the booking operation through a stable provider identifier or equivalent retry-safe correlation.
6. Persist the successful event identity and confirmed booking durably; retain its protected interval without a gap between pending and confirmed protection.
7. Invalidate affected availability across every event type, make the confirmation receipt available, and dispatch notifications through retry-safe delivery work.

An unrelated calendar event must never be edited to make space. A losing concurrent request receives a conflict response and refreshed alternatives while keeping its form data.

Two requests with identical starts but different durations, or different starts whose intervals overlap, MUST be treated as conflicts. Locking only by event-type ID, start time, browser session, or a single application process is insufficient. Configuration changes and cancellation/rescheduling operations must coordinate with the same host-level conflict protection.

### 7.2 Interrupted operations and recovery

Use a bounded processing lease to detect stalled work; a proposed initial value is two minutes. However, **lease expiry is not proof that a calendar write failed**. After an unknown provider outcome, keep the interval protected until reconciliation establishes whether the event exists. A maintenance process must recover stalled work and alert the host if it remains unresolved.

A provider timeout after a successful remote write must not create another event on retry. Google supports caller-supplied event IDs and documents their use for avoiding duplicate creation after interrupted operations. Use that capability or an equally reliable correlation mechanism; do not generate a new unrelated event ID for every retry. [S7]

If the provider write succeeds but local confirmation fails, recover the booking from its pre-existing operation and provider event. If the write definitely fails, release the reservation and report failure. If email delivery fails after calendar creation, the booking remains confirmed; retry the email rather than canceling the meeting.

Keep a stable, access-controlled operation-status view for requests whose outcome is still unknown. Do not tell an invitee that a booking failed and encourage a new attempt while the first attempt may already exist.

### 7.3 Limits of the concurrency guarantee

The application MUST prevent overlapping bookings created through its own links. The calendar provider is an independent system: a free/busy read followed by an event write is not a transaction that prevents the host or another application from simultaneously inserting a conflicting event.

Perform a fresh pre-write check and a prompt post-write verification. If a new external conflict is detected, surface it immediately in the host interface and notify the host; do not silently delete the external event or arbitrarily cancel one meeting. This is an integration-boundary limitation, not permission to omit application-side concurrency protection.

## 8. Cancellation and rescheduling

### 8.1 Secure management links

Invitees do not need an account. Provide high-entropy, booking-scoped management tokens delivered in their confirmation message and receipt. These are application capabilities, not calendar-provider OAuth tokens. Store only a verifier/hash or use an equivalent revocable verification mechanism. Limit them to viewing/managing their own booking, with an explicit expiry and revocation policy.

Opening a link MUST be read-only. Cancellation or rescheduling requires an explicit confirmation action, protecting against email-link scanners and accidental visits. An invalid token must not reveal whether an arbitrary booking reference exists. Do not put management tokens in analytics, logs, referrers, or public calendar metadata.

Proposed policy: invitees may cancel or reschedule until the meeting begins; both deadlines are configurable per type. Rescheduling must also obey minimum notice for the new slot. Display the policy before confirmation and on the management page. The host can override deadlines deliberately, but still receives conflict warnings.

### 8.2 Cancellation

Show the current meeting and an optional cancellation-reason field. On confirmation, record the operation durably, cancel/delete the application's corresponding calendar event with the appropriate attendee notification, and then mark the booking canceled and release its protection. Preserve an audit record.

A repeated cancellation is safe and produces the same final state. If the provider is unavailable, show “Cancellation pending,” retry safely, and do not imply that the invitee's calendar has already been updated. Preserve local protection until provider removal is known or the host explicitly resolves the situation.

Stop all future reminders immediately once cancellation is requested. If cancellation definitively fails, surface that state and recompute any remaining valid reminders. Existing calendar events that belong to other applications must never be deleted.

### 8.3 Rescheduling

Show the existing meeting first. Keep its event type, invitee details, and duration by default. For the flexible type, allow a new selection from the same six allowed durations. A fixed type cannot become variable during rescheduling.

The old booking remains effective until the update succeeds. Protect the new candidate interval while updating, retaining the old interval as well. Prefer updating the existing calendar event so there remains one event identity. On definitive failure, keep the original booking; on unknown outcome, reconcile before releasing either affected interval. Compare event/booking versions before changing data so a stale browser cannot overwrite a newer cancellation or manual calendar edit.

When calculating replacement slots, exclude **only this booking's own event and protected reservation**, never all busy time in its original interval. A free/busy response may combine multiple overlapping events; subtracting the entire old interval could erase another event's conflict. Use event-aware conflict data for this operation.

After success, update the booking version, release the old protection, send the update, and rebuild reminders for the new time. Rotate or otherwise invalidate superseded mutation capabilities. The old receipt may show current status, but must not allow a stale request to repeat a change.

For disabled event types, existing invitees may still cancel. Rescheduling is allowed under the booking's saved type policy and current host calendar/schedule constraints; disabling new bookings alone must not unexpectedly strand existing appointments.

## 9. Calendar integration

### 9.1 One explicit connection and calendar

Connect one host account and explicitly select one calendar used both for conflict checking and writing bookings. The chosen calendar must be writable. Display its identity in admin and store its stable identifier; do not rely on an implicit “last-used account.”

Connecting an account may display its calendars for selection, but only the chosen calendar participates in this release. Be explicit that events existing solely on other calendars will not block availability. Changing the selected calendar must not migrate or recreate existing bookings automatically; retain each existing booking's original calendar reference for its lifecycle.

### 9.2 Required provider capabilities

The integration MUST support account/calendar identification and permission checks; busy-time reads; reading individual booking events; creating, updating, and canceling events with attendees; detecting manual changes and deletions; and retry-safe event correlation. It must handle recurring-event instances and exceptions in conflict data.

Every created event contains the correct title, exact start/end, timezone metadata, host organizer, primary invitee, joining/location details, and a private application booking reference where supported. Use a clear title such as “DataTalks.Club — General Chat: {Invitee name}.” Include only appropriate invitee-provided context in shared descriptions. Do not include private admin notes or bearer management tokens.

Create the event as busy. Do not force the invitee's RSVP to “accepted,” and do not enable guest modification of the appointment by default. Request the provider's invitation/update delivery; do not assume the event will automatically appear in every recipient's calendar regardless of their invitation settings. Google exposes attendee notifications and event identifiers through its event APIs. [S6, S7]

### 9.3 Google Calendar permission profile, if selected

Use narrowly scoped access sufficient for the actual operations. For a calendar the host owns, the candidate profile is `calendar.freebusy`, `calendar.events.owned`, and read-only calendar-list access when a picker is used. These are abbreviated Google scope names, not literal full scope strings. Verify the final set against the chosen endpoints and account/calendar ownership. Shared writable calendars may require a different events scope. Do not request calendar-sharing/ACL administration or deletion of entire calendars. [S8]

Dapier owns the provider-consent setup and any additional identity-verification scopes. Verify offline renewal and the chosen OAuth application's operating mode before launch. Do not assume an already-connected YouTube account authorizes Calendar access or reuse its connection implicitly.

### 9.4 Freshness and external calendar edits

Public availability may use results up to 60 seconds old under healthy conditions; each final confirmation requires a fresh check. Invalidate local results immediately after application-originated booking changes. Show an unavailable/service-error state rather than stale selectable availability when a required refresh fails.

Reconcile application-created events at least every five minutes under healthy conditions, with faster invalidation where available. A periodic refresh implementation is acceptable for this single-host service; push notifications are optional, not a requirement to build extra infrastructure.

If Google push notifications are used, validate the registered channel, renew it before expiry, tolerate repeated/out-of-order signals, and fetch actual changes after a signal. Google notifications do not contain changed event records, and channels require explicit renewal. If incremental synchronization is used, an invalid sync token requires rebuilding the provider-event cache, **not deleting application bookings or unresolved operations**. [S9, S10]

Manual external changes are handled as follows:

| External change | Required behavior |
|---|---|
| An unrelated busy event is added, moved, or deleted | Recompute availability without importing it as an application booking. |
| An application booking is moved or its duration is edited | Reflect its actual provider time, adjust protection/reminders, and flag policy conflicts; do not silently revert the edit. |
| An application booking is deleted/canceled | Mark it canceled after verifying the deletion, stop reminders, and release protection. |
| Invitee declines the invitation | Show RSVP state to the host; do not automatically cancel the booking or free time. |
| Title or joining details change | Reflect relevant details without re-sending an unrelated creation notification. |
| Calendar authorization is lost | Fail closed for new bookings, retain existing records, and request reconnection through Dapier. |

Do not overwrite manual provider changes with stale local snapshots. If an externally edited duration no longer matches allowed choices, preserve the actual appointment and require a valid choice for any subsequent app-driven reschedule.

## 10. Dapier integration and credential ownership

### 10.1 Required boundary

The application is a consumer of Dapier, not another OAuth token manager. Align with `DataTalksClub/dapier/docs/oauth-token-factory-spec.md`, which describes named connections, explicit grants, verified provider identities, and short-lived access-token delivery. [S2–S4]

| Responsibility | Owner |
|---|---|
| Provider OAuth client configuration, consent, and callbacks | Dapier |
| Provider refresh tokens, client secrets, token renewal/rotation/revocation | Dapier |
| Verified account binding and connection-use authorization | Dapier |
| Booking policies, availability, reservations, invitee records, event lifecycle | Scheduling application |
| Temporary use of a valid provider access token to perform an authorized operation | Scheduling backend only |
| Scheduling admin sessions and invitee management-link capabilities | Scheduling application; separate from provider OAuth credentials |

The application MUST NOT persist provider access tokens, refresh tokens, or client secrets in its database, files, deployed environment configuration, browser storage, backups, analytics, or logs. A provider access token may exist transiently in backend memory for its allowed lifetime and operation, but must never be sent to the invitee or admin browser.

Store only non-secret connection references, expected provider/account identity, selected calendar ID, necessary capability/scope metadata, and connection-health information. Do not read Dapier's underlying secret storage directly.

### 10.2 Contract to implement against

Use an explicitly named calendar connection; proposed ID: `calendar-alexey`. Register the deployed scheduler as a distinct service consumer with permission to use only that connection. This ID is proposed configuration, not a claim that it exists.

Dapier must authenticate the scheduler's enrolled machine identity, authorize the connection grant, return or provide use of a valid access token, and identify its provider, verified account, granted scopes, and expiry. The scheduler verifies the returned account and required capabilities before making a calendar request. Wrong-account responses are fatal, not a reason to fall back to another connection.

Dapier remains responsible for refresh-token concurrency and rotation. On an expired/rejected provider access token, the scheduler can request renewed access once through Dapier and retry an appropriate idempotent operation. It must not implement its own provider-refresh flow or enter an unlimited refresh/retry loop.

On a revoked connection, missing grant, account mismatch, insufficient scope, or Dapier outage, fail closed for new confirmations and expose a non-secret operator error. Never ask the host to paste a refresh token into the scheduling application as a fallback.

If Dapier offers a provider-request proxy rather than raw access-token delivery, using it is acceptable as long as the same identity, authorization, account-binding, error, and idempotency requirements hold. No exact Dapier endpoint path is invented in this specification.

### 10.3 Work required in Dapier versus this application

Verify or complete the token-factory machine-access capability in Dapier. Add the calendar provider capability, consent scopes, verified account binding, connection lifecycle, and least-privilege scheduler grant there if missing. Keep the existing YouTube and Dropbox connections unchanged. The inspected registry does not establish Calendar support. [S3, S4]

Use an enrolled workload/machine identity for unattended runtime. Do not copy an operator's browser session, human refresh credential, or interactive CLI login into the server. Its bootstrap trust must be provisioned independently; Dapier cannot be the sole means of obtaining the credential required to contact Dapier itself. Prefer a platform-issued workload identity when available, without prescribing an implementation platform.

Reusing DTC shared authentication for the scheduling admin is the preferred integration assumption. Apply an explicit single-host allowlist/subject check: successful DTC authentication is not sufficient authorization to administer bookings or credentials. The public booking flow remains anonymous.

Production readiness depends on an end-to-end demonstration that the deployed scheduler can obtain access, read the intended calendar, create/update/cancel a test event, and continue working after provider-token expiry without human intervention. Use a dedicated test calendar; do not make unsolicited bookings on the host's real calendar.

## 11. Conferencing and meeting location

Each event type must have an explicit location mode. Support host-entered location/instructions or a fixed meeting URL in the first release. Automatic Google Meet generation is the proposed additional mode when Google Calendar and the selected calendar support it; Zoom and other provider-specific meeting APIs are outside scope.

For automatic conferencing, generate a unique conference per booking and preserve it on a simple time reschedule when appropriate. Google conference creation is asynchronous and exposes pending/success/failure states; do not assume a link is available immediately with the event response. [S6]

Store conference status separately from booking status. If the calendar event exists but its conference is pending, the booking remains confirmed and the receipt says “Joining details are being prepared.” Retry and deliver the link when ready. On persistent failure, alert the host and allow explicit replacement instructions. Do not create another calendar event or silently insert an unrelated permanent meeting room.

For podcast sessions, allow the host to specify “Joining details will be provided separately” and later add the external recording link. An invitee's link can be collected as ordinary text when needed, but must not be fetched by the server or automatically treated as trusted host instructions.

## 12. Invitations, confirmations, and reminders

Use two clearly separated responsibilities: the calendar provider sends the actual invitation and meeting updates; an outbound transactional-email capability sends the application's confirmation, secure management links, and optional reminders. Reuse an existing outbound mail service only after verifying that it actually supports sending; an inbound email-processing integration is not enough. Its external-service credentials follow the same Dapier boundary.

For a confirmed booking, send the invitee a summary with event type, date, start/end, duration, timezone, joining information/status, and secure management actions. Send the host the invitee details and agenda. Send corresponding cancellation and rescheduling notices. If provider emails and application emails both arrive, their roles and wording must be distinct; never generate duplicate meeting invitations.

Support configurable reminder offsets. Proposed defaults are 24 hours and one hour before the start. Skip any reminder whose scheduled time has already passed when the booking is created. Recompute after a reschedule and suppress on cancellation. Check the latest booking version and status immediately before delivery so an old queued reminder cannot announce the wrong time.

Deduplicate notification work by booking, version, recipient, message type, and reminder offset. Persist delivery status, retry transient failures, and expose permanent failures to the host. Do not promise exactly-once delivery from an email provider lacking idempotency or delivery-status lookup; handle ambiguous sends without indiscriminate retries.

A fallback calendar file must represent the same meeting, not create another host event. Use the provider's calendar-event UID where available and consistent revisions. Make clear that manually importing a file is a snapshot and is not a replacement for receiving organizer updates.

## 13. Logical data model

This is a behavioral data contract, not a prescribed database schema.

| Entity | Required information |
|---|---|
| Host | Stable ID, authorized admin subject, display profile, timezone, public base URL, global pause state, default policy, contact fallback. |
| Event type | Stable ID, unique slug and aliases, display content, fixed/selectable duration configuration, visibility, enabled state, schedule reference, buffers, limits, questions, location mode, notification policy, version. |
| Availability schedule | Stable ID, timezone, weekly windows, date replacements, and version. |
| Host block | Absolute interval or host-local full-day closure, owner-only reason, creation/update metadata. |
| Calendar connection | Dapier connection reference, expected provider/account ID, selected calendar ID, permissions/capabilities, non-secret health status, freshness timestamps. No provider credentials. |
| Booking | Stable ID/reference, event-type ID and snapshot, invitee details/answers, start/end instants, selected duration, display timezone, buffer snapshot, original calendar reference, provider event ID/UID/version, location/conference status, booking status and revision, timestamps. |
| Reservation | Host, protected interval, owning operation, lifecycle state, processing lease, and resolution status; sufficient to block cross-type conflicts. |
| Booking operation | Type (create/reschedule/cancel), idempotency key and payload fingerprint, booking revision, intended change, provider correlation, pending/unknown/success/failure state, attempts, sanitized failure details. |
| Management capability | Booking reference, permitted actions, expiry/revocation, and verifier or equivalent non-plaintext validation data. |
| Notification | Booking/revision, recipient, kind, scheduled time, delivery identity/status, and retry history. |
| Audit record | Actor category and identity/reference, action, affected entity, time, sanitized result; no provider credentials or management-token values. |

Use separate booking, operation, delivery, and conference states. A confirmed meeting with an unsent email is not a failed booking. A confirmed meeting with a pending reschedule still has an effective original time until the new provider state is established.

Minimum booking states are `pending_confirmation`, `confirmed`, `canceled`, and `failed`. “Past” is derived from time, not a mandatory terminal mutation. Operation states must distinguish “not attempted,” “in progress,” “outcome unknown,” “succeeded,” and “definitively failed.” Preserve enough information to recover after a restart without reconstructing intent from logs.

The application is authoritative for scheduling policy, event-type configuration, invitee form data, and in-flight protection. The provider event is authoritative for its actual external appointment state after manual edits. Reconciliation must preserve both rather than blindly treating either entire system as the sole source of truth.

## 14. Logical service contracts

Exact endpoint URLs and protocols are implementation choices. Keep public and admin capabilities separate.

| Capability | Input | Output / guarantees |
|---|---|---|
| List public types | Host/public page | Enabled listed types only; never internal connection metadata. |
| Read event type | Public slug | Public configuration; unlisted types work by direct link; disabled/invalid states are explicit. |
| Query availability | Type, allowed duration, bounded date range, display timezone | Valid start/end instants, display metadata, availability generation/freshness marker; no private busy-event records. |
| Create booking | Type, duration, selected start, invitee fields, idempotency key | Confirmed receipt, recoverable pending operation, validation error, conflict, or service-unavailable result. |
| Read booking/operation | Appropriate scoped receipt/management access | Only that booking or operation's state and safe details. |
| Reschedule booking | Management/admin authorization, expected revision, new valid time/duration, idempotency key | One updated booking or a safe unchanged/pending result. |
| Cancel booking | Management/admin authorization, expected revision, idempotency key, optional reason | Canceled or pending state; repeated calls are safe. |
| Admin configuration | Authorized host session and expected configuration version | Validated, versioned settings; no silent overwrite of newer changes. |
| Admin bookings/health | Authorized host session and bounded filters | Private booking details, safe operational states, integration health. |

Use structured errors distinguishable by the client: invalid input, unsupported duration, slot no longer available, type disabled, policy violation, expired management permission, stale revision, connection unavailable, and operation pending. Do not collapse a provider failure into an empty successful availability response.

Bound public date-range size and request rates. The server must recheck all scheduling rules even if the client sends a previously issued slot identifier. A cached, signed, or opaque slot offer is not a lock on the calendar.

## 15. Privacy, security, and operational requirements

### 15.1 Access and data minimization

Public responses MUST NOT expose calendar event titles, descriptions, locations, attendee lists, private notes, account identifiers, provider event IDs, Dapier grants, or credentials. Availability is the only public calendar-derived information. Keep private booking responses uncached and prevent management-link referrer leakage.

Require secure transport. Protect admin sessions, authorize every admin operation, defend state-changing browser requests against cross-site request forgery, and validate all inputs. Sanitize rendered text and calendar-file content; do not treat an invitee's notes as executable HTML or instructions to the system. Do not fetch arbitrary invitee-supplied URLs.

Use an explicitly configured canonical public base URL for emailed links, not an untrusted incoming host header. Mask personal data in operational logs. Rate-limit availability, booking submissions, management-token failures, and confirmation resends. Avoid public email-existence checks. Enable an anti-bot challenge only if abuse warrants it; anonymous booking need not require signup or mandatory email verification in the first release.

Provide a brief privacy notice, host-configurable retention, and an owner-operated export/delete path for booking data. Scope deletion explicitly: deleting an application record must not implicitly delete unrelated calendar events or credentials. Retain non-personal operation/tombstone information long enough to avoid replaying old writes, including after a restore. Do not claim legal compliance solely from implementing these controls.

### 15.2 Health and recovery

Expose distinct health states for Dapier access, calendar read/write permission, freshness, pending provider operations, conferencing, and email delivery. Alert the host on revoked authorization, unresolved writes, stale synchronization, and persistent delivery failures. Do not rely on an email alert alone when email itself is failing; the admin interface must show the issue.

Schedule runtime maintenance for pending-operation reconciliation, reminder delivery, and provider synchronization. These are capabilities of the delivered application, not manual steps the host must remember. Work must tolerate duplicate execution, process restarts, transient outages, and deployment overlap.

Back up application configuration, booking identities, and recoverable operation state without provider credentials. A restore must reconcile provider events before reissuing any pending create operation. Never replay calendar invitations merely because a backup was restored.

Measure availability latency and booking-confirmation latency separately from provider health. Keep the UI responsive with explicit pending states for slow calls. Correctness and truthful status take priority over displaying instant but unverified confirmation.

### 15.3 Graceful degradation

| Failure | User-visible and system behavior |
|---|---|
| Dapier cannot authorize calendar access | Pause new confirmations; show temporary unavailability publicly and actionable details privately. |
| Busy-time result is stale, partial, or errored | Do not offer/confirm uncertain slots as free. |
| Calendar create outcome is unknown | Keep protection, show a pending operation, and reconcile; no blind duplicate. |
| Confirmation email fails | Keep the meeting confirmed; show receipt and retry/alert. |
| Conference generation fails | Keep confirmed calendar time; show pending/failed joining details and alert host. |
| Cancel/reschedule outcome is unknown | Preserve appropriate protection and previous known state until reconciled. |
| Notification signal is missed | Periodic reconciliation repairs state. |
| Global booking pause is enabled | Stop new bookings, but retain receipts, lifecycle actions, reminders, and recovery work for existing meetings. |

## 16. Acceptance tests

All mandatory tests must pass before replacing live links. Tests involving external providers must use controlled test data and authorized test recipients.

### 16.1 Links, interface, and availability

| ID | Scenario | Expected result |
|---|---|---|
| A01 | Open each of the four initial links | Correct title, policy, and duration behavior; no invitee login required. |
| A02 | Open the public landing page | Only listed enabled types appear; direct unlisted links still work. |
| A03 | Disable an event type | New bookings stop; existing booking receipts and permitted management actions still work. |
| A04 | Change a slug with alias retention | Previous application-owned link resolves to the same type without changing existing bookings. |
| A05 | Select each of the six extended durations | Days/start times recompute for that exact duration. |
| A06 | Submit 45, 181, 0, a negative value, or a manipulated end time | Server rejects unsupported duration or ignores untrusted end and validates its own result. |
| A07 | Free time is 09:00–12:00, no buffers, 30-minute grid | Start lists match the table in section 6.4. |
| A08 | Free time is 09:00–10:30 and 11:00–12:30 | A three-hour appointment is never offered. |
| A09 | A busy event starts partway through a proposed long booking | The start is rejected even if its first 30 minutes are free. |
| A10 | One meeting ends exactly when another starts, zero buffers | Back-to-back times are allowed. |
| A11 | Existing post-buffer and candidate pre-buffer are both 15 minutes | The required meeting-to-meeting gap is 30 minutes. |
| A12 | A proposed protected interval crosses working hours or lunch | It is unavailable, even if the meeting-only interval is free. |
| A13 | A date-specific schedule replaces weekly hours | Only the replacement hours apply; global closure still takes precedence. |
| A14 | A busy all-day or multi-day event intersects a viewer-local date | Every actual overlap is blocked, including timezone date shifts. |
| A15 | A recurring event has one moved occurrence and one canceled occurrence | Conflict data reflects those instances, not only the recurring template. |
| A16 | An event is explicitly marked free | It does not block scheduling. |
| A17 | Notice or horizon boundary is reached while the form is open | Submission uses current server time and rejects an expired offer. |
| A18 | Global daily minutes/count limit is reached through another event type | Remaining types enforce the same global limit. |
| A19 | Invitee changes timezone or crosses a daylight-saving transition | Absolute selection is preserved; nonexistent times are absent; repeated local times are distinguishable. |
| A20 | Use the public flow on mobile, keyboard-only, and a screen reader | All stages are usable with readable labels, focus, and errors. |

### 16.2 Booking lifecycle and races

| ID | Scenario | Expected result |
|---|---|---|
| B01 | Book one 180-minute session | One application booking and one 180-minute calendar event are created. |
| B02 | Book through one link, then check another link | Every overlapping offer disappears across all types. |
| B03 | Two users submit overlapping bookings simultaneously with different starts/types/durations | At most one overlapping app booking is confirmed. |
| B04 | Double-click or retry the identical submission | The same booking/operation is returned; no extra event or invitation. |
| B05 | Reuse an idempotency key with different input | Request is rejected rather than silently changing intent. |
| B06 | Calendar write succeeds but its response is lost | Reconciliation finds the original event; a retry does not create another. |
| B07 | Process dies after provider success but before local confirmation | Pending operation recovers to one confirmed booking. |
| B08 | Processing lease expires with unknown provider outcome | Time remains protected until reconciled; no unsafe auto-release. |
| B09 | A calendar busy event appears after the slot was displayed | Fresh pre-write check rejects the now-conflicting booking. |
| B10 | Another calendar writer inserts a conflict during the final read/write gap | Post-write/reconciliation detects and flags it; no arbitrary deletion occurs. |
| B11 | Reschedule to a new slot, then force a definitive provider update failure | Original booking remains valid; new protection is released safely. |
| B12 | Reschedule overlaps its old time and another external event | Only the booking's own event is excluded; the external conflict still blocks. |
| B13 | Cancel twice or open a cancellation URL with an email scanner | Explicit cancellation is idempotent; opening the URL alone changes nothing. |
| B14 | Submit a stale reschedule after a cancellation or newer edit | Revision check rejects it; no canceled event is recreated. |
| B15 | Host manually moves or deletes the provider event | App state and reminders reconcile without reverting or recreating it. |

### 16.3 Dapier, delivery, and privacy

| ID | Scenario | Expected result |
|---|---|---|
| C01 | Provider access token expires | Scheduler obtains usable access through Dapier without handling a refresh token. |
| C02 | Dapier returns a different account or missing scope | Operation fails before any calendar read/write using the wrong authorization. |
| C03 | Scheduler requests another Dapier connection without a grant | Request is denied. Existing unrelated connections remain unchanged. |
| C04 | Calendar or Dapier is unavailable, or free/busy includes a per-calendar error | Public flow reports unavailability; no all-free fallback. |
| C05 | Inspect browser requests, application storage, logs, exports, and backups | No provider OAuth credentials or private external event details appear. |
| C06 | Ordinary DTC member accesses admin or token-use functionality | Access is denied despite successful identity authentication. |
| C07 | Confirmation email fails after successful event creation | Booking stays confirmed and the message can be retried without another event. |
| C08 | Meeting is canceled or rescheduled before a queued reminder sends | No stale reminder is delivered; new valid reminders are scheduled. |
| C09 | Conference creation is pending or fails | Receipt reports its true status; no duplicate event or fabricated joining link. |
| C10 | Another invitee's booking reference or invalid/expired management token is used | No unauthorized details or mutations are exposed. |
| C11 | Push notification is duplicated/missed, or incremental sync token becomes invalid | State repairs safely; application bookings and operation records are retained. |
| C12 | Application is restored from backup | Existing provider events are reconciled; invitations/creates are not blindly replayed. |

## 17. Migration and launch configuration

Preserve already-booked appointments on the selected calendar; they must block new availability immediately. Do not duplicate them or re-send invitations. They need not be imported into this application's managed-booking history in the first release. Treat their lifecycle as legacy unless a deliberate, verified import is implemented separately.

Publish new links on a domain the host controls and replace old links in community pages, email signatures, websites, and templates. Do not depend on Calendly remaining active. Keep a mapping from each old event-type purpose to the intended new type. Old Calendly cancellation/rescheduling links are not automatically converted into new application management links.

Before publishing availability, configure the following. Missing values may be represented as explicit setup tasks; do not invent credentials, account identifiers, or the host's working hours.

| Configuration decision | Current specification position |
|---|---|
| Calendar provider | Google Calendar proposed; confirm actual provider before implementing its adapter. |
| Dapier deployment and callable contract | Use the real deployment/API after verifying token-factory and machine-consumer readiness. No guessed endpoint path. |
| Calendar connection and account binding | `calendar-alexey` proposed; host provides/verifies the actual connection and expected account. |
| Selected calendar | One explicit writable calendar; no identifier inferred from the public Calendly URL. |
| Host identity | Alexey Grigorev as display name; explicit authorized shared-auth subject still required. |
| Community/type labels and mapping | Four seed types as specified; exact existing titles/descriptions and `/30min` mapping remain unverified. |
| Availability | Host supplies weekly hours, breaks, holidays, overrides, and any special long-session windows. |
| Timezone | `Europe/Berlin` proposed. |
| Scheduling defaults | 30-minute grid, 12-hour notice, 60-day horizon, zero buffers, unlimited daily count/minutes until configured. |
| Location/conferencing | Host chooses fixed instructions/link or automatic Meet where supported. |
| Email | Verify outbound capability, sender identity, host notification address, and reminder settings. |
| Public domain and privacy | Host supplies canonical base URL, contact fallback, retention policy, and final visibility choices. |

Validate the entire flow first with a test calendar and test invitee, including a three-hour booking, token renewal, rescheduling, cancellation, an email failure, and an unknown calendar-write outcome. Only then replace live public links. No deployment should require the application to receive or persist a provider refresh token.

## 18. Implementation work packages and definition of done

These packages describe functional ownership rather than technology choices. They can be distributed among agents once the shared entities, operation semantics, and Dapier contract are agreed.

| Package | Deliverable |
|---|---|
| P1 — Configuration and identity | Host-only administration, four editable types, link behavior, schedules, policies, and setup checklist. |
| P2 — Availability | Timezone-safe interval calculations, duration-aware offers, limits, public-safe responses, and owner diagnostics. |
| P3 — Dapier and calendar | Authorized machine consumer, exact account/calendar binding, calendar event lifecycle, retry-safe correlation, and external-state reconciliation. |
| P4 — Booking lifecycle | Durable reservations, concurrency protection, idempotency, pending-operation recovery, secure cancel/reschedule flows. |
| P5 — Invitee experience | Responsive landing/booking/form/receipt/management pages and all loading/error/accessibility states. |
| P6 — Delivery and operations | Calendar invitations, outbound confirmations, reminders, joining-detail status, health views, backups, and launch tests. |

Do not mark the application complete when only the booking UI works or when a happy-path event can be created. Release is complete when all four links operate on the same real conflict calendar, all six flexible durations require uninterrupted availability, existing events and edge cases are respected, booking mutations recover safely from retries/outages, invitees can manage their appointments, notifications reflect current state, admin settings work without code edits, and Dapier owns provider-token lifecycle throughout.

## 19. Source notes

Sources were inspected on 21 September 2026. External API behavior may evolve; verify integration-specific details during implementation. Product rules and proposed defaults in this document are requirements for the new application, not assertions that Calendly behaves identically.

**[S1] Supplied Calendly pages.** Requested successfully, but the accessible output did not expose booking configuration. The root title identified Alexey Grigorev.

`https://calendly.com/dtc-alexey/30min`  
`https://calendly.com/dtc-alexey/`

**[S2] Dapier README.** Describes the existing application and marks the shared-auth token factory as proposed. Inspected file SHA: `b63ae275b661a03b585adcd1c478eb9e4a418827`.

`https://github.com/DataTalksClub/dapier/blob/main/README.md`

**[S3] Dapier OAuth token-factory specification.** Defines named connections, DTC shared identity, connection/agent grants, headless identity enrollment requirements, account binding, and short-lived token delivery. This is a specification, not proof of deployment. Inspected file SHA: `1d9c6101f836c403507deee4bd2b027f766e9b3d`.

`https://github.com/DataTalksClub/dapier/blob/main/docs/oauth-token-factory-spec.md`

**[S4] Dapier admin source.** The inspected first 180 lines include the OAuth provider registry with Dropbox and YouTube. Inspected file SHA: `40d5189a53aa27e6f894a807d79b082125d63b8b`. Repository tree inspected at commit `db16655e53765792f50679019a641ca428f8aa17`.

`https://github.com/DataTalksClub/dapier/blob/main/src/admin.py`

**[S5] Google Calendar: Freebusy query.** Busy intervals and per-calendar error reporting.

`https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query`

**[S6] Google Calendar: Events resource.** Event metadata, timezones, exclusive end times, RSVP status, event identity, and asynchronous conference creation.

`https://developers.google.com/workspace/calendar/api/v3/reference/events`

**[S7] Google Calendar: Create events.** Event creation, writable-calendar requirements, caller-supplied event IDs, and attendee notifications.

`https://developers.google.com/workspace/calendar/api/guides/create-events`

**[S8] Google Calendar: Choose API scopes.** Scope meanings and least-privilege selection.

`https://developers.google.com/workspace/calendar/api/auth`

**[S9] Google Calendar: Push notifications.** Notification payload limits, channel verification and renewal.

`https://developers.google.com/workspace/calendar/api/guides/push`

**[S10] Google Calendar: Synchronize resources efficiently.** Incremental synchronization and invalid sync-token recovery.

`https://developers.google.com/workspace/calendar/api/guides/sync`

**Additional product references reviewed:** Calendly's calendar connection, availability settings, and buffer documentation. These informed the coverage of core scheduling concepts, not the unverified configuration of the supplied personal pages.

`https://calendly.com/help/connect-your-calendar-to-calendly`  
`https://calendly.com/help/how-to-fine-tune-your-availability-settings`  
`https://calendly.com/help/how-to-use-buffers`
