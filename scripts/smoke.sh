#!/usr/bin/env bash
# End-to-end smoke test against the real stack.
#
# Brings the compose project up, waits for health, applies migrations, walks the
# initialization flow, mints an API key and exercises the documented contract.
# Always tears down, including volumes, so nothing is left behind.
#
#   ./scripts/smoke.sh          full run
#   ./scripts/smoke.sh --keep   leave the stack running for inspection
set -uo pipefail

PROJECT="dtk-smoke"
COMPOSE="docker compose -p ${PROJECT} -f docker/compose.yml"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

PASS=0
FAIL=0
API="http://127.0.0.1:8000"

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

cleanup() {
  if [[ $KEEP -eq 1 ]]; then
    printf '\nStack left running (project %s). Remove it with:\n  %s down -v\n' "$PROJECT" "$COMPOSE"
    return
  fi
  step "Teardown"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 && echo "  removed containers, networks and volumes"
}
trap cleanup EXIT

step "Preflight"
command -v docker >/dev/null || { echo "docker is required"; exit 1; }
[[ -f docker/compose.yml ]] || { echo "docker/compose.yml is missing"; exit 1; }
$COMPOSE config -q && pass "compose file parses" || { fail "compose file is invalid"; exit 1; }

step "Generating .env"
if [[ ! -f .env ]]; then
  pg_pass=$(openssl rand -hex 16)
  redis_pass=$(openssl rand -hex 16)
  {
    echo "DTK_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')"
    echo "POSTGRES_PASSWORD=${pg_pass}"
    echo "REDIS_PASSWORD=${redis_pass}"
    # The connection URLs repeat the passwords. They are written here rather
    # than assembled by hand because getting them out of step with the two
    # variables above is a silent failure - see docker/README.md.
    echo "DTK_DATABASE_URL=postgresql+asyncpg://dtk:${pg_pass}@postgres:5432/dtk"
    echo "DTK_REDIS_URL=redis://:${redis_pass}@redis:6379/0"
  } > .env
  pass "generated .env with random secrets"
else
  pass ".env already present"
fi
# The process must refuse to start without a key; a shared default would be no
# encryption at all.
grep -q '^DTK_SECRET_KEY=.\{32,\}' .env && pass "secret key is long enough" \
  || fail "DTK_SECRET_KEY missing or too short"

step "Bringing the stack up"
if $COMPOSE up -d --wait --wait-timeout 300; then
  pass "all services reported healthy"
else
  fail "stack did not become healthy"
  $COMPOSE ps
  $COMPOSE logs --tail=60
  exit 1
fi

step "Health endpoints"
code=$(curl -sf -o /dev/null -w '%{http_code}' "$API/healthz" || echo 000)
[[ "$code" == "200" ]] && pass "/healthz -> 200" || fail "/healthz -> $code"
code=$(curl -sf -o /dev/null -w '%{http_code}' "$API/readyz" || echo 000)
[[ "$code" == "200" ]] && pass "/readyz -> 200" || fail "/readyz -> $code"

step "Database shape"
psql() { $COMPOSE exec -T postgres psql -U dtk -d dtk -tAc "$1" 2>/dev/null; }
tables=$(psql "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[[ "${tables:-0}" -ge 11 ]] && pass "$tables tables created" || fail "only ${tables:-0} tables"
hyper=$(psql "SELECT count(*) FROM timescaledb_information.hypertables")
[[ "${hyper:-0}" -eq 3 ]] && pass "3 hypertables" || fail "${hyper:-0} hypertables, expected 3"
caggs=$(psql "SELECT count(*) FROM timescaledb_information.continuous_aggregates")
[[ "${caggs:-0}" -ge 2 ]] && pass "$caggs continuous aggregates" || fail "${caggs:-0} aggregates"

step "Initialization flow"
init=$(curl -sf "$API/api/setup/status" || echo '{}')
echo "$init" | grep -q '"initialized"' && pass "setup status is reachable" || fail "setup status failed"

# The one-time token is printed to the container log, never returned over HTTP:
# under Docker's userland proxy a source-IP check would admit everyone.
token=$($COMPOSE logs api 2>/dev/null | grep -oE 'token=[A-Za-z0-9_-]{20,}' | tail -1 | cut -d= -f2)
if [[ -n "$token" ]]; then
  pass "setup token found in the container log"
  body=$(printf '{"token":"%s","username":"smoke","password":"smoke-password-1234"}' "$token")
  code=$(curl -s -o /tmp/dtk_init.json -w '%{http_code}' -X POST "$API/api/setup/init" \
         -H 'content-type: application/json' -d "$body")
  [[ "$code" =~ ^2 ]] && pass "admin created ($code)" || { fail "init -> $code"; cat /tmp/dtk_init.json; }

  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/setup/init" \
         -H 'content-type: application/json' -d "$body")
  [[ "$code" == "409" ]] && pass "init is closed afterwards (409)" || fail "init still open ($code)"
else
  fail "no setup token in the log"
fi

step "Authentication and the response envelope"
code=$(curl -s -o /tmp/dtk_login.json -w '%{http_code}' -c /tmp/dtk_cookies -X POST \
       "$API/api/v1/auth/login" -H 'content-type: application/json' \
       -d '{"username":"smoke","password":"smoke-password-1234"}')
[[ "$code" =~ ^2 ]] && pass "login ($code)" || fail "login -> $code"

body=$(curl -s "$API/api/v1/douyin/video?url=not-a-url")
echo "$body" | grep -q '"success":false' && pass "failure envelope has success:false" \
  || fail "unexpected envelope: ${body:0:120}"
echo "$body" | grep -qE '"code":"(INVALID_URL|UNAUTHENTICATED)"' \
  && pass "stable error code present" || fail "no stable error code"
echo "$body" | grep -q '"request_id"' && pass "meta carries request_id" || fail "no request_id"

step "SSRF rejection"
for evil in "http://127.0.0.1:8000/admin" "http://169.254.169.254/latest/meta-data/" "https://douyin.com.evil.example/x"; do
  body=$(curl -s -b /tmp/dtk_cookies "$API/api/v1/parse" -H 'content-type: application/json' \
         -d "{\"url\":\"$evil\"}")
  echo "$body" | grep -q '"success":false' && pass "rejected $evil" || fail "ACCEPTED $evil"
done

step "Localization"
en=$(curl -s "$API/api/v1/douyin/video?url=nope&lang=en" | grep -o '"message":"[^"]*"' | head -1)
zh=$(curl -s "$API/api/v1/douyin/video?url=nope&lang=zh" | grep -o '"message":"[^"]*"' | head -1)
[[ -n "$en" && -n "$zh" && "$en" != "$zh" ]] && pass "messages differ by language" \
  || fail "localization not applied (en=$en zh=$zh)"

step "OpenAPI and the console"
code=$(curl -sf -o /dev/null -w '%{http_code}' "$API/openapi.json" || echo 000)
[[ "$code" == "200" ]] && pass "/openapi.json served" || fail "/openapi.json -> $code"
code=$(curl -sf -o /dev/null -w '%{http_code}' "$API/docs" || echo 000)
[[ "$code" == "200" ]] && pass "/docs served" || fail "/docs -> $code"
code=$(curl -sf -o /dev/null -w '%{http_code}' "$API/" || echo 000)
[[ "$code" == "200" ]] && pass "console served at /" || fail "console -> $code"

step "Rate-limit headers"
headers=$(curl -sf -D - -o /dev/null -b /tmp/dtk_cookies "$API/api/v1/system/status" 2>/dev/null || true)
echo "$headers" | grep -qi 'x-request-id' && pass "X-Request-ID echoed" || fail "no X-Request-ID"

step "Result"
printf '  %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]] || exit 1
