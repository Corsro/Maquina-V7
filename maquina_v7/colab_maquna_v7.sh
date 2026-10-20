#!/bin/bash
# Maquina V7 - Auto-generated deployment script
# Faithful recreation of V5 with all 6 bugs fixed.
# Do not edit manually — modify stages in compiler.py and regenerate.

set -uo pipefail

echo "===================================================="
echo "  MAQUINA V7 — Cloud Gaming Deployment Engine"
echo "  Fixed version of V5 for Colab 2026.01+ runtimes"
echo "===================================================="
echo ""

# ============================================================
# STAGE: 01-GPU-DETECT
# Detect NVIDIA GPU across all Colab path layouts (fixes V5 bug #1)
# ============================================================
echo "[V7] Detecting NVIDIA GPU..."

NVIDIA_SMI=""
for candidate in \
    /usr/bin/nvidia-smi \
    /usr/local/bin/nvidia-smi \
    /usr/lib/nvidia-utils/bin/nvidia-smi \
    /opt/nvidia/bin/nvidia-smi \
    /usr/lib/x86_64-linux-gnu/bin/nvidia-smi; do
    if [ -x "$candidate" ]; then
        NVIDIA_SMI="$candidate"
        break
    fi
done

# Fall back to PATH lookup
if [ -z "$NVIDIA_SMI" ]; then
    NVIDIA_SMI=$(command -v nvidia-smi 2>/dev/null)
fi

# V5 skipped this: check /proc/driver/nvidia as last resort
if [ -z "$NVIDIA_SMI" ] && [ -d /proc/driver/nvidia ]; then
    echo "[V7] nvidia-smi binary not found, but /proc/driver/nvidia exists"
    echo "[V7] GPU driver is loaded — attempting modprobe to expose utilities"
    sudo modprobe nvidia 2>/dev/null || true
    sudo ldconfig 2>/dev/null || true
    NVIDIA_SMI=$(command -v nvidia-smi 2>/dev/null)
fi

if [ -z "$NVIDIA_SMI" ]; then
    echo "[V7] ERROR: No NVIDIA GPU detected."
    echo "[V7] Please verify your Colab runtime is set to GPU (T4)."
    echo "[V7] Menu: Runtime -> Change runtime type -> T4 GPU"
    echo "[V7] If you already have a GPU, this is V5 bug #1 —"
    echo "[V7] the binary was at a path V5 didn't check."
    exit 1
fi

echo "[V7] Found nvidia-smi at: $NVIDIA_SMI"

# Query GPU info (V5 just checked exit code; V7 shows the user)
GPU_NAME=$($NVIDIA_SMI --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
GPU_DRIVER=$($NVIDIA_SMI --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)

if [ -n "$GPU_NAME" ]; then
    echo "[V7] GPU: $GPU_NAME"
    echo "[V7] Driver: $GPU_DRIVER"
else
    echo "[V7] WARNING: nvidia-smi found but query returned empty."
    echo "[V7] Continuing anyway — GPU may still be usable."
fi

# Link to /usr/sbin for tools in t4packages that expect it there
ln -sf "$NVIDIA_SMI" /usr/sbin/nvidia-smi 2>/dev/null || true

echo "[V7] GPU detection complete."

# ============================================================
# STAGE: 02-REGION-CHECK
# Log Colab region and warn on high-latency zones (fixes V5 bug #6)
# ============================================================
echo "[V7] Checking Colab region..."

        COLAB_REGION=$(curl -s ipinfo.io 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('region', d.get('city', 'unknown')))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

        echo "[V7] Colab region: $COLAB_REGION"

        case "$COLAB_REGION" in
            *"Singapore"*|*"Taiwan"*|*"Hong Kong"*|*"Tokyo"*)
                echo "[V7] Asia Pacific region — good for low latency."
                ;;
            *"Virginia"*|*"Oregon"*|*"Iowa"*|*"California"*)
                echo "[V7] US region — latency may vary depending on your location."
                ;;
            *)
                echo "[V7] Region detected: $COLAB_REGION"
                ;;
        esac

        echo "[V7] Region check complete."

# ============================================================
# STAGE: 03-T4PACKAGES
# Download and extract pre-built T4 gaming packages (Steam+Sunshine+Xvfb, 855MB)
# ============================================================
echo "[V7] Downloading T4 gaming packages (855MB pre-built)..."

# V5 used a single wget with no retry. On asia-south1 this can
# timeout or get rate-limited by GitHub. V7 retries up to 3 times.

T4PACKAGES_URL="https://github.com/kmille36/Colab-Cloud-Gaming/releases/download/1.0.0/t4packages.tar.gz"

# Skip if already extracted (idempotent re-runs)
if [ -d /usr/games ] && [ -f /usr/games/sunshine ]; then
    echo "[V7] T4 packages already extracted — skipping download."
else
    MAX_RETRIES=3
    for attempt in $(seq 1 $MAX_RETRIES); do
        echo "[V7] Download attempt $attempt/$MAX_RETRIES..."
        if wget -q --show-progress -O /tmp/t4packages.tar.gz "$T4PACKAGES_URL" 2>&1; then
            echo "[V7] Download complete. Extracting (this takes a minute)..."
            if pigz -dc /tmp/t4packages.tar.gz | tar -xv -C / 2>&1 | tail -5; then
                echo "[V7] T4 packages extracted successfully."
                rm -f /tmp/t4packages.tar.gz
                break
            else
                echo "[V7] WARNING: Extraction had issues on attempt $attempt."
            fi
        else
            echo "[V7] Download failed on attempt $attempt."
            if [ "$attempt" -eq "$MAX_RETRIES" ]; then
                echo "[V7] ERROR: Could not download T4 packages after $MAX_RETRIES attempts."
                echo "[V7] Check your internet connection and Colab region."
                exit 1
            fi
        fi
    done
fi

# Set up PATH (identical to V5)
echo -e '#!/bin/sh\nexport PATH="$PATH:/usr/games:/usr/lib/games"' | \
    sudo tee /etc/profile.d/custom_path.sh > /dev/null && \
    sudo chmod +x /etc/profile.d/custom_path.sh

export PATH="$PATH:/usr/games:/usr/lib/games"

echo "[V7] T4 packages stage complete."

# ============================================================
# STAGE: 04-TAILSCALE
# Install Tailscale (auto-versioned) and start DERP relay tunnel (fixes V5 bug #3)
# ============================================================
echo "[V7] Installing Tailscale..."

# V5 hardcoded tailscale_1.84.0. V7 uses the official install script
# which always fetches the latest version compatible with the runtime.
if command -v tailscale &>/dev/null; then
    echo "[V7] Tailscale already installed — skipping."
else
    curl -fsSL https://tailscale.com/install.sh | sh
fi

# Create state directory (same as V5)
sudo mkdir -p /var/lib/tailscale

# Start tailscaled — V5 used TS_DEBUG_ALWAYS_USE_DERP=true with
# --tun=userspace-networking. This is critical for Colab because:
#   - userspace-networking avoids needing /dev/net/tun
#   - DERP relay works through Colab's restrictive NAT
echo "[V7] Starting tailscaled daemon (userspace + DERP relay)..."
nohup bash -c "while true; do \
    TS_DEBUG_ALWAYS_USE_DERP=true tailscaled \
        --tun=userspace-networking \
        --socket=/run/tailscale/tailscaled.sock \
        --port 41641; \
    sleep 1; \
done" > /tmp/tailscaled.log 2>&1 &

sleep 3

# Bring up the tunnel
echo "[V7] Bringing up Tailscale tunnel..."
sudo tailscale up --hostname=maquina-v7 --accept-routes 2>&1 || \
    echo "[V7] Tailscale up issued — check the auth URL above to log in."

# Get the Tailscale IP (same as V5)
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "pending")
echo "[V7] Tailscale IP: $TAILSCALE_IP"

# Run netcheck (same as V5 — shows region latency info)
echo "[V7] Running network check..."
tailscale netcheck 2>&1 || true

echo "[V7] Tailscale stage complete."

# ============================================================
# STAGE: 05-SUNSHINE-LAUNCH
# Launch Sunshine streaming host from t4packages with headless NVENC config
# ============================================================
echo "[V7] Launching Sunshine streaming host..."

        SUNSHINE_CONF_DIR="$HOME/.config/sunshine"
        mkdir -p "$SUNSHINE_CONF_DIR"

        # Write Sunshine config for headless T4 streaming
        cat > "$SUNSHINE_CONF_DIR/sunshine.conf" << 'CONFEOF'
{
    "origin_web_ui_allowed": "wan",
    "origin_pin_allowed": true,
    "upnp": "disabled",
    "external_ip": "auto",
    "hostname": "Maquina-V7",
    "min_log_level": 2,
    "channels": 1,
    "fec_percentage": 20,
    "qp": 28,
    "bitrate": 10000,
    "resolutions": [
        {"width": 1920, "height": 1080, "refreshRate": 60},
        {"width": 1280, "height": 720, "refreshRate": 60}
    ],
    "encoder": "nvenc",
    "app_id": []
}
CONFEOF

        # Locate Sunshine binary from t4packages
        SUNSHINE_BIN=""
        for candidate in \
            /usr/games/sunshine \
            /usr/local/bin/sunshine \
            /opt/sunshine/sunshine \
            /usr/bin/sunshine; do
            if [ -x "$candidate" ]; then
                SUNSHINE_BIN="$candidate"
                break
            fi
        done

        if [ -z "$SUNSHINE_BIN" ]; then
            SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
        fi

        if [ -z "$SUNSHINE_BIN" ]; then
            echo "[V7] WARNING: Sunshine not found in t4packages."
            echo "[V7] Installing from LizardByte PPA as fallback..."
            sudo add-apt-repository -y ppa:lizardbyte/stable
            sudo apt-get update -qq
            sudo apt-get install -y sunshine
            SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
        fi

        if [ -n "$SUNSHINE_BIN" ]; then
            echo "[V7] Launching Sunshine from: $SUNSHINE_BIN"
            $SUNSHINE_BIN --config "$SUNSHINE_CONF_DIR/sunshine.conf" &
            SUNSHINE_PID=$!
            echo "[V7] Sunshine launched (PID: $SUNSHINE_PID)"
            sleep 3
        else
            echo "[V7] ERROR: Could not find or install Sunshine."
            exit 1
        fi

        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "pending")

        echo ""
        echo "[V7] ============================================"
        echo "[V7]  MAQUINA V7 — DEPLOYMENT COMPLETE"
        echo "[V7] ============================================"
        echo "[V7]  Sunshine web UI: https://$TAILSCALE_IP:47990"
        echo "[V7]  Tailscale IP:    $TAILSCALE_IP"
        echo "[V7] ============================================"
        echo "[V7]  Next: Pair Moonlight with the PIN script:"
        echo "[V7]    !bash pair_moonlight.sh YOUR_PIN"
        echo "[V7] ============================================"

echo "[V7] All stages completed."
echo "[V7] Your cloud gaming PC is ready!"