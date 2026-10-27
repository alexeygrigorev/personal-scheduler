# personal-scheduler
Calendly replacement for a single host: public booking links with
duration-aware availability, an admin console behind the shared DTC auth, and
calendar access consumed through Dapier. Python on AWS serverless (SAM HttpApi
+ Lambda + DynamoDB single table + SQS worker), deployed like `dataqna`.

## Specification

See the [Personal Scheduling — Functional and Integration Specification](docs/personal-scheduling-specification.md) for the complete English specification, including interface requirements, calendar and Dapier integrations, booking rules, implementation work packages, and 47 acceptance tests.

## Develop

```bash
make install   # uv sync
make test      # pytest (moto-backed DynamoDB, no AWS needed)
make build     # sam build --config-env sandbox
make validate  # sam validate --lint
```

Pushing to `main` runs Test, and a green Test triggers Deploy, which runs
`scripts/deploy.sh` (resolves the shared-auth client from the shared-auth
stack's outputs, so the client id can't drift) and then the smoke check in
`scripts/verify_deployment.py`. The very first deploy must run with direct AWS
credentials, since the `GitHubDeployRole` this stack creates does not exist
yet; afterwards CI uses that role.

## Configure before publishing links

The stack seeds four meeting types and an empty schedule on first request.
Nothing is bookable until the host configures weekly hours. The launch
checklist in the specification (section 17) applies: verify the Dapier
token-factory machine-access path and calendar capability, bind connection
`google-calendar` to the real Google account (the console's **Authorize with
Google** button runs this through Dapier's agent connect flow), pick the
writable calendar in the same console card, set the canonical base URL and
sender identity, and exercise the full flow against a test calendar first.
Provider OAuth credentials are never stored here — Dapier owns them.

The `dtc-30` type seeds the three invitee questions asked on the Calendly
30-minute page this replaces (topic choice with "Other", a required
discussion note, optional company); edit them per meeting type in the admin
console under Event types → Questions.

## Dapier API-token identity

Calendar access uses Dapier's agent API (`POST /api/agent/token`) with a
revocable Dapier API token. `DapierApiTokenSecretName` selects its team-store
record, whose `value` field contains JSON with an `api_token` key. The Lambda
roles may decrypt only the session and API-token records. Without a configured
API-token record the calendar port fails closed. Provider refresh tokens stay
in Dapier; access tokens are used transiently in memory.

Afterwards the admin console's Overview card does the rest: **Authorize with
Google** sends the API-token identity to Dapier's connect endpoint and
opens Google's consent screen directly (no Dapier sign-in), and the card's
picker stores which of the account's writable calendars bookings go to.
