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

echo "[V7] Setting Sunshine default credentials..."

# Set default username/password (same as V5)
curl -u admin:admin -X POST -k https://localhost:47990/api/password \
    -H "Content-Type: application/json" \
    -d '{"currentUsername":"admin","currentPassword":"admin","newUsername":"admin","newPassword":"admin","confirmNewPassword":"admin"}' \
    2>/dev/null

echo "[V7] Submitting PIN: $pin"

# Submit the pairing PIN (same as V5)
curl -u admin:admin -X POST -k https://localhost:47990/api/pin \
    -H "Content-Type: application/json" \
    -d "{\"pin\":\"$pin\",\"name\":\"moonlight\"}" \
    2>/dev/null

echo ""
echo "[V7] PIN submitted. Pairing complete!"
echo "[V7] You can now connect from Moonlight using your Tailscale IP."
