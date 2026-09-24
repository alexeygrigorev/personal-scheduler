#!/usr/bin/env bash
# Deploy via SAM, resolving the shared auth app client at deploy time
# instead of baking its ID into samconfig.toml. The shared-auth stack owns
# those values and may recreate them, so reading them from its outputs means
# this stack can't drift. Bootstrap note: the very first deploy must run with
# direct AWS credentials (the GitHubDeployRole this template creates does not
# exist yet); afterwards CI uses that role.
set -euo pipefail
cd "$(dirname "$0")/.."

AUTH_STACK="${AUTH_STACK:-dtcdev-shared-auth}"
auth_output() {
  aws cloudformation describe-stacks --region us-east-1 --stack-name "$AUTH_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
AUTH_CLIENT_ID="${AUTH_CLIENT_ID:-$(auth_output PersonalSchedulerClientId)}"
AUTH_ISSUER="${AUTH_ISSUER:-$(auth_output IssuerUrl)}"
AUTH_JWKS_URL="${AUTH_JWKS_URL:-$(auth_output JwksUrl)}"
CERT_ARN="${CERT_ARN:-arn:aws:acm:eu-west-1:817685572750:certificate/c13e30c0-f10d-43f2-9bb3-d9b3f60d0b45}"
# The dapier machine credential is enrolled out-of-band into Secrets Manager;
# when its secret exists, point the stack at it so the calendar port comes
# alive, otherwise omit the parameter and the stack keeps failing closed.
MACHINE_SECRET_ID="${MACHINE_SECRET_ID:-personal-scheduler/dapier-machine}"
DAPIER_MACHINE_SECRET_ARN="${DAPIER_MACHINE_SECRET_ARN:-$(aws secretsmanager describe-secret \
  --secret-id "$MACHINE_SECRET_ID" --query ARN --output text 2>/dev/null || true)}"

overrides=(
  DomainName=scheduler.dtcdev.click
  DomainCertificateArn="$CERT_ARN"
  HostedZoneId=Z05963572WVWFHDQZH5NE
  AuthBaseUrl=https://auth.dtcdev.click
  AuthClientId="$AUTH_CLIENT_ID"
  AuthIssuer="$AUTH_ISSUER"
  AuthJwksUrl="$AUTH_JWKS_URL"
  RootAdmin=alexey@datatalks.club
)
# SAM rejects empty values in --parameter-overrides; omitting the parameter
# keeps CloudFormation's previous value, which is '' until first enrolled.
if [ -n "$DAPIER_MACHINE_SECRET_ARN" ] && [ "$DAPIER_MACHINE_SECRET_ARN" != "None" ]; then
  overrides+=(DapierMachineSecretArn="$DAPIER_MACHINE_SECRET_ARN")
fi

sam deploy --config-env sandbox --parameter-overrides "${overrides[@]}" "$@"
