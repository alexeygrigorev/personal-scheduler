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
`calendar-alexey` to the real account, select one writable calendar, set the
canonical base URL and sender identity, and exercise the full flow against a
test calendar first. Provider OAuth credentials are never stored here —
Dapier owns them.
