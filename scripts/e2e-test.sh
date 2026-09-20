#!/usr/bin/env bash
# End-to-end harness runner: fake Kidde endpoint -> collector -> InfluxDB, no hardware.
# Brings up the e2e PROFILE of compose.yml, waits for the collector to authenticate, poll the location/
# device REST API, and write device data to InfluxDB, then asserts it landed. Always
# tears the stack down. Driven by `make test-e2e`, which builds both images OUTSIDE
# compose (build-local + harness-build) — compose only runs the tags.
set -euo pipefail

DC="docker compose --env-file .env.e2e --profile e2e"
TOKEN="kidde-e2e-token"
MEASUREMENT="kidde_collector_device"
# Device labels the fake serves — must reach the kidde_collector_device measurement.
EXPECTED_LABELS=("Basement" "Living Room" "Hallway")
TIMEOUT="${E2E_TIMEOUT:-120}"

cleanup() { $DC down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "▶ starting e2e stack (images built outside compose)…"
$DC up -d

# Query InfluxDB (InfluxQL over the v1-compat API) from inside the influx container.
influx_query() {
  $DC exec -T kidde_influxdb curl -s -G "http://localhost:8086/query" \
    --data-urlencode "db=kidde" \
    --data-urlencode "q=$1" \
    -H "Authorization: Token ${TOKEN}" 2>/dev/null || true
}

echo "▶ waiting up to ${TIMEOUT}s for device data to appear in InfluxDB…"
deadline=$(( SECONDS + TIMEOUT ))
labels_resp=""
while [ "$SECONDS" -lt "$deadline" ]; do
  resp="$(influx_query "SHOW TAG VALUES FROM \"${MEASUREMENT}\" WITH KEY = \"label\"")"
  if echo "$resp" | grep -q "Basement"; then
    labels_resp="$resp"
    break
  fi
  sleep 5
done

if [ -z "$labels_resp" ]; then
  echo "✗ FAIL: no ${MEASUREMENT} data within ${TIMEOUT}s"
  echo "---- collector logs ----"; $DC logs --tail=80 kidde-collector || true
  echo "---- fake logs ----"; $DC logs --tail=20 kidde_fake || true
  exit 1
fi
echo "✓ PASS: ${MEASUREMENT} has data"

# Every fake device label must reach the measurement.
missing=()
for label in "${EXPECTED_LABELS[@]}"; do
  echo "$labels_resp" | grep -q "$label" || missing+=("$label")
done
if [ "${#missing[@]}" -ne 0 ]; then
  echo "✗ FAIL: ${MEASUREMENT} missing expected device label(s): ${missing[*]}"
  echo "   got: $labels_resp"
  echo "---- collector logs ----"; $DC logs --tail=80 kidde-collector || true
  exit 1
fi
echo "✓ PASS: ${MEASUREMENT} contains all device labels: ${EXPECTED_LABELS[*]}"

# The nested air-quality metrics must land (iaq detectors write co2_value etc).
co2="$(influx_query "SELECT COUNT(\"co2_value\") FROM \"${MEASUREMENT}\"")"
if ! echo "$co2" | grep -q '"values"'; then
  echo "✗ FAIL: no nested co2_value data in ${MEASUREMENT}"
  echo "   got: $co2"
  echo "---- collector logs ----"; $DC logs --tail=80 kidde-collector || true
  exit 1
fi
echo "✓ PASS: nested air-quality metrics present (co2_value)"

# The collector must still be running (didn't crash on any message).
if ! $DC ps --status running --services | grep -q '^kidde-collector$'; then
  echo "✗ FAIL: collector is not running (may have crashed)"
  $DC logs --tail=80 kidde-collector || true
  exit 1
fi

count="$(influx_query "SELECT COUNT(\"temperature\") FROM \"${MEASUREMENT}\"")"
echo "✓ collector healthy. influx ${MEASUREMENT} temperature count: ${count}"
echo "✓ e2e PASSED"
