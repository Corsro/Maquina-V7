#!/bin/bash
# Maquina V7 - Automated Moonlight PIN Pairing
# Usage: bash pair_moonlight.sh <PIN>
# Or:    bash pair_moonlight.sh  (will prompt for PIN)
#
# This is the same pairing flow as V5's moon-pair.sh:
#   1. Set Sunshine default credentials (admin:admin)
#   2. Submit the Moonlight PIN via Sunshine API
# The Sunshine web UI must be running (port 47990) before calling this.

pin="$1"

if [ -z "$pin" ]; then
    read -p "Enter Moonlight PIN: " pin
fi

if [ -z "$pin" ]; then
    echo "[V7] ERROR: No PIN provided."
    exit 1
fi

echo "[V7] Checking Sunshine is running on port 47990..."
if ! curl -sk --connect-timeout 5 https://localhost:47990/ >/dev/null 2>&1; then
    echo "[V7] ERROR: Sunshine is not responding on port 47990."
    echo "[V7] Make sure the deployment cell has finished and Sunshine is running."
    exit 1
fi

echo "[V7] Sunshine is running. Setting default credentials..."

curl -u admin:admin -X POST -k https://localhost:47990/api/password \
    -H "Content-Type: application/json" \
    -d '{"currentUsername":"admin","currentPassword":"admin","newUsername":"admin","newPassword":"admin","confirmNewPassword":"admin"}' \
    2>/dev/null

echo "[V7] Submitting PIN: $pin"

response=$(curl -u admin:admin -X POST -k https://localhost:47990/api/pin \
    -H "Content-Type: application/json" \
    -d "{\"pin\":\"$pin\",\"name\":\"moonlight\"}" \
    -w "\n%{http_code}" 2>/dev/null)

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | head -n -1)

case "$http_code" in
    200|201)
        echo "[V7] PIN submitted successfully."
        echo "[V7] You can now connect from Moonlight using your Tailscale IP."
        ;;
    *)
        echo "[V7] ERROR: Pairing failed (HTTP $http_code)."
        echo "[V7] Response: $body"
        echo "[V7] Check that the PIN is correct and hasn't expired."
        exit 1
        ;;
esac
