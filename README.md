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
`calendar-alexey` to the real Google account (the console's **Authorize with
Google** button runs this through Dapier's agent connect flow), pick the
writable calendar in the same console card, set the canonical base URL and
sender identity, and exercise the full flow against a test calendar first.
Provider OAuth credentials are never stored here — Dapier owns them.

The `dtc-30` type seeds the three invitee questions asked on the Calendly
30-minute page this replaces (topic choice with "Other", a required
discussion note, optional company); edit them per meeting type in the admin
console under Event types → Questions.

## Dapier machine enrollment (one-time, per deployment)

Calendar access flows through Dapier's agent API (`POST /api/agent/token`),
which requires a DTC ID token from an enrolled machine identity. To enroll:

1. In Dapier, create the `calendar-alexey` connection with the **Google
   Calendar** provider and scopes `calendar.freebusy`,
   `calendar.events.owned`, and `userinfo.email` (the console dialog
   prefills all three), verify the provider account, and grant the
   `personal-scheduler` agent both `use` and `connect` on it.
2. Sign in once with the dedicated scheduler CLI client and store the
   resulting credential in Secrets Manager as
   `{ "client_id": "<cli-client-id>", "refresh_token": "..." }`.
3. Deploy with `DapierMachineSecretArn` set to that secret's ARN. The stack
   grants the functions read access to exactly that secret; with no ARN the
   calendar port fails closed. The scheduler never stores provider tokens —
   Dapier keeps every refresh token, and calendar access tokens live only
   transiently in memory.

Afterwards the admin console's Overview card does the rest: **Authorize with
Google** sends the enrolled machine identity to Dapier's connect endpoint and
opens Google's consent screen directly (no Dapier sign-in), and the card's
picker stores which of the account's writable calendars bookings go to.
