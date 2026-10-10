#!/bin/bash
# simulate_concurrent_admins.sh
# This script simulates two administrators trying to edit the exact same timetable slot
# at the exact same millisecond to test our 5-layer concurrency protection.

set -e

BASE_URL="http://127.0.0.1:8000/api"
ADMIN_EMAIL="admin@college.edu"
ADMIN_PASS="123456789"

echo "=================================================="
echo " TIMETABLE CONCURRENCY TEST (Ubuntu)"
echo "=================================================="
echo "Checking dependencies..."
if ! command -v jq &> /dev/null; then
    echo "Error: 'jq' is not installed. Please install it using 'sudo apt install jq'"
    exit 1
fi

if ! command -v curl &> /dev/null; then
    echo "Error: 'curl' is not installed."
    exit 1
fi

echo "[1/4] Logging in as Admin to get an API Token..."
LOGIN_RESP=$(curl -s -X POST "$BASE_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}")

TOKEN=$(echo $LOGIN_RESP | jq -r .access_token)

if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
    echo "Failed to login. Response:"
    echo $LOGIN_RESP
    exit 1
fi
echo " -> Authentication successful!"

echo ""
echo "[2/4] Generating a blank base version to act as our 'head'..."
# We send an empty schedule to get a valid version 
GEN_RESP=$(curl -s -X POST "$BASE_URL/timetable/generate" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"change_summary":"Base version for concurrent test"}')

VERSION_ID=$(echo $GEN_RESP | jq -r .version_id)
ETAG="\"tt-$VERSION_ID\""

if [ "$VERSION_ID" == "null" ] || [ -z "$VERSION_ID" ]; then
    echo "Failed to generate initial timetable. Response:"
    echo $GEN_RESP
    exit 1
fi
echo " -> Created Base Version ID: $VERSION_ID"
echo " -> ETag to use for simulated edits: $ETAG"

echo ""
echo "[3/4] Preparing two colliding edit operations..."
echo " - Admin Alice will try to book SK for Computer Networks in Room R#207B at Monday 10:00"
echo " - Admin Bob will try to book SK for Computer Networks in Room R#207B at Monday 10:00 (Exactly the same)"

PAYLOAD_ALICE=$(cat <<EOF
{
  "version_id": $VERSION_ID,
  "partial_ok": true,
  "actions": [
    {
      "action": "ADD_CLASS",
      "spec": {
        "program": "B.Tech",
        "semester": "5th",
        "day": "Monday",
        "start_time": "10:00",
        "end_time": "12:00",
        "subject_code": "cn",
        "subject_name": "Computer Networks",
        "teacher": "SK",
        "entry_type": "Theory",
        "room": "R#207B"
      }
    }
  ]
}
EOF
)

# For Bob, we use the exact same payload but we simulate it firing simultaneously
PAYLOAD_BOB=$PAYLOAD_ALICE

echo ""
echo "[4/4] 🚀 FIRING CONCURRENT REQUESTS..."
echo "  Note: Both requests are being sent simultaneously in the background."

# We use subshells and the & operator to run them in parallel
(
    curl -s -X POST "$BASE_URL/actions/execute" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -H "If-Match: $ETAG" \
        -d "$PAYLOAD_ALICE" > /tmp/alice_response.json
    echo " -> Alice's request completed."
) &

(
    curl -s -X POST "$BASE_URL/actions/execute" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -H "If-Match: $ETAG" \
        -d "$PAYLOAD_BOB" > /tmp/bob_response.json
    echo " -> Bob's request completed."
) &

# Wait for both background curl jobs to finish
wait

echo ""
echo "=================================================="
echo " TEST RESULTS"
echo "=================================================="

# Print out Alice's result
echo "[ALICE RESPONSE]:"
jq '.' /tmp/alice_response.json

echo ""
# Print out Bob's result
echo "[BOB RESPONSE]:"
jq '.' /tmp/bob_response.json

echo ""
echo "ANALYSIS:"
echo "If concurrency is working perfectly, you should see:"
echo "1. One response gets a successful 'new_version_id'."
echo "2. The other response gets a 409 'entry_conflict' (or 'stale_version'), indicating the lock/ETag cleanly intercepted the double-booking before data corruption occurred."
