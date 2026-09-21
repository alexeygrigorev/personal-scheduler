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

sam deploy --config-env sandbox --parameter-overrides \
  DomainName=scheduler.dtcdev.click \
  DomainCertificateArn=arn:aws:acm:eu-west-1:817685572750:certificate/da5101db-666f-49da-a76b-e2c781afdd6b \
  HostedZoneId=Z05963572WVWFHDQZH5NE \
  AuthBaseUrl=https://auth.dtcdev.click \
  AuthClientId="$AUTH_CLIENT_ID" \
  AuthIssuer="$AUTH_ISSUER" \
  AuthJwksUrl="$AUTH_JWKS_URL" \
  RootAdmin=alexey@datatalks.club \
  "$@"
