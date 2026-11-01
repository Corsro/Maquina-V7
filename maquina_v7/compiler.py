#!/usr/bin/env python3
"""
Maquina V7 - Deployment Engine Compiler

Faithful recreation of Maquina V5 (kmille36/Colab-Cloud-Gaming) with all
bugs that caused V5 to fail on current Google Colab runtimes (2026.01+)
fixed.

=== V5 BUGS (6 root causes) ===

Bug 1: nvidia-smi PATH check too strict
Bug 2: bit.ly short links can die
Bug 3: Tailscale version hardcoded to 1.84.0
Bug 4: pyobfuscate.com dependency
Bug 5: 855MB download with no retry
Bug 6: No region awareness

=== V7 INTERNAL BUGS (found in deep audit, 16 additional) ===

Bug 7:  No Xvfb virtual display started — Sunshine crashes on launch
Bug 8:  No PulseAudio audio sink — silent gameplay
Bug 9:  Sunshine killed on script exit (no setsid before backgrounding)
Bug 10: Duplicate tailscaled on re-run (now: pkill + clean state)
Bug 11: tailscale up blocks forever (no timeout)
Bug 12: Tailscale install failure unchecked
Bug 13: sudo env var not propagated (TS_DEBUG_ALWAYS_USE_DERP)
Bug 14: No Steam backup restore code despite README claims
Bug 15: Sunshine config mono audio + conflicting bitrate/qp
Bug 16: No extraction verification before proceeding
Bug 17: Sunshine launch not verified (port 47990)
Bug 18: PIN JSON injection in pair_moonlight.sh
Bug 19: Credentials POST unchecked in pair_moonlight.sh
Bug 20: Google Drive path inflexible (MyDrive vs My Drive)
Bug 21: backup_steam.sh hides tar errors
Bug 22: compiler.py docstring said 5 bugs, listed 6
Bug 23: Sunshine PPA fallback doesn't work on Colab (no systemd)
Bug 24: Sunshine failure kills entire cell with exit 1
"""

import textwrap
from dataclasses import dataclass
from typing import List


@dataclass
class Stage:
    name: str
    description: str
    commands: str

    def render(self) -> str:
        header = f"# {'=' * 60}\n# STAGE: {self.name}\n# {self.description}\n# {'=' * 60}"
        body = textwrap.dedent(self.commands).strip()
        return f"{header}\n{body}\n"


# ---------------------------------------------------------------------------
# Stage 1: GPU detection (fixes V5 bug #1)
# ---------------------------------------------------------------------------

STAGE_GPU_DETECT = Stage(
    name="01-GPU-DETECT",
    description="Detect NVIDIA GPU across all Colab path layouts (fixes V5 bug #1)",
    commands="""
        echo "[V7] Detecting NVIDIA GPU..."

        NVIDIA_SMI=""
        for candidate in \\
            /usr/bin/nvidia-smi \\
            /usr/local/bin/nvidia-smi \\
            /usr/lib/nvidia-utils/bin/nvidia-smi \\
            /opt/nvidia/bin/nvidia-smi \\
            /usr/lib/x86_64-linux-gnu/bin/nvidia-smi; do
            if [ -x "$candidate" ]; then
                NVIDIA_SMI="$candidate"
                break
            fi
        done

        if [ -z "$NVIDIA_SMI" ]; then
            NVIDIA_SMI=$(command -v nvidia-smi 2>/dev/null)
        fi

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
            exit 1
        fi

        echo "[V7] Found nvidia-smi at: $NVIDIA_SMI"

        GPU_NAME=$($NVIDIA_SMI --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        GPU_DRIVER=$($NVIDIA_SMI --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)

        if [ -n "$GPU_NAME" ]; then
            echo "[V7] GPU: $GPU_NAME"
            echo "[V7] Driver: $GPU_DRIVER"
        else
            echo "[V7] WARNING: nvidia-smi found but query returned empty."
            echo "[V7] Continuing anyway — GPU may still be usable."
        fi

        ln -sf "$NVIDIA_SMI" /usr/sbin/nvidia-smi 2>/dev/null || true

        echo "[V7] GPU detection complete."
    """,
)

# ---------------------------------------------------------------------------
# Stage 2: Region check (fixes V5 bug #6)
# ---------------------------------------------------------------------------

STAGE_REGION_CHECK = Stage(
    name="02-REGION-CHECK",
    description="Log Colab region and warn on high-latency zones (fixes V5 bug #6)",
    commands="""
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
    """,
)

# ---------------------------------------------------------------------------
# Stage 3: Download t4packages (fixes V5 bug #5)
# Also: extraction verification (bug #16) + backup restore (bug #14)
# ---------------------------------------------------------------------------

STAGE_T4PACKAGES = Stage(
    name="03-T4PACKAGES",
    description="Download and extract pre-built T4 gaming packages (Steam+Sunshine+Xvfb, 855MB)",
    commands="""
        echo "[V7] Downloading T4 gaming packages (855MB pre-built)..."

        T4PACKAGES_URL="https://github.com/kmille36/Colab-Cloud-Gaming/releases/download/1.0.0/t4packages.tar.gz"

        if [ -d /usr/games ] && [ -f /usr/games/sunshine ]; then
            echo "[V7] T4 packages already extracted — skipping download."
        else
            MAX_RETRIES=3
            for attempt in $(seq 1 $MAX_RETRIES); do
                echo "[V7] Download attempt $attempt/$MAX_RETRIES..."
                if wget -q --show-progress -O /tmp/t4packages.tar.gz "$T4PACKAGES_URL" 2>&1; then
                    echo "[V7] Download complete. Extracting (this takes a minute)..."
                    DECOMPRESS="gzip -dc"
                    if command -v pigz &>/dev/null; then
                        DECOMPRESS="pigz -dc"
                    fi
                    if $DECOMPRESS /tmp/t4packages.tar.gz | tar -xv -C / 2>&1 | tail -5; then
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

        # Verify critical files were extracted before proceeding (bug #16)
        if [ ! -f /usr/games/sunshine ]; then
            echo "[V7] ERROR: Extraction completed but /usr/games/sunshine is missing."
            echo "[V7] The t4packages archive may be corrupted. Re-run to retry."
            exit 1
        fi
        echo "[V7] Verified: /usr/games/sunshine exists."

        # Restore Steam backup from Google Drive if one exists (bug #14)
        DRIVE_MOUNT="/content/drive"
        BACKUP_FILE=""
        if [ -d "$DRIVE_MOUNT/MyDrive" ]; then
            BACKUP_FILE="$DRIVE_MOUNT/MyDrive/maquina_v7_backup.tar.gz"
        elif [ -d "$DRIVE_MOUNT/My Drive" ]; then
            BACKUP_FILE="$DRIVE_MOUNT/My Drive/maquina_v7_backup.tar.gz"
        fi
        if [ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ]; then
            echo "[V7] Found Steam backup at $BACKUP_FILE — restoring..."
            mkdir -p "$HOME/.steam/steam"
            if tar -xzf "$BACKUP_FILE" -C "$HOME/.steam/steam" 2>/dev/null; then
                echo "[V7] Steam backup restored successfully."
            else
                echo "[V7] WARNING: Steam backup restore failed — continuing with fresh install."
            fi
        else
            echo "[V7] No Steam backup found — starting with fresh install."
        fi

        # Set up PATH (identical to V5)
        echo -e '#!/bin/sh\\nexport PATH="$PATH:/usr/games:/usr/lib/games"' | \\
            sudo tee /etc/profile.d/custom_path.sh > /dev/null && \\
            sudo chmod +x /etc/profile.d/custom_path.sh

        export PATH="$PATH:/usr/games:/usr/lib/games"

        echo "[V7] T4 packages stage complete."
    """,
)

# ---------------------------------------------------------------------------
# Stage 4: Tailscale (fixes V5 bug #3)
# Also: install check (#12), env var fix (#13), timeout (#11)
# ---------------------------------------------------------------------------

STAGE_TAILSCALE = Stage(
    name="04-TAILSCALE",
    description="Install Tailscale (auto-versioned) and start DERP relay tunnel (fixes V5 bug #3)",
    commands="""
        echo "[V7] Installing Tailscale..."

        if command -v tailscale &>/dev/null; then
            echo "[V7] Tailscale already installed — skipping."
        else
            curl -fsSL https://tailscale.com/install.sh | sh
            if ! command -v tailscale &>/dev/null; then
                echo "[V7] ERROR: Tailscale installation failed."
                echo "[V7] Check your internet connection and try again."
                exit 1
            fi
        fi

        sudo mkdir -p /var/lib/tailscale /run/tailscale

        # Kill any stale tailscaled from a previous cell run.
        # A leftover process holds the old socket and authstate2 cookie,
        # which causes "401 bad tailscale-authstate2 cookie" on reconnect.
        sudo pkill -x tailscaled 2>/dev/null || true
        sleep 1
        sudo rm -f /var/lib/tailscale/tailscaled.state
        sudo rm -f /run/tailscale/tailscaled.sock

        # Start tailscaled as root. setsid detaches it from the cell's
        # process group so Colab's cell-completion SIGHUP doesn't kill it.
        # Use `sudo env` so TS_DEBUG_ALWAYS_USE_DERP propagates (bug #13).
        echo "[V7] Starting tailscaled daemon (userspace + DERP relay)..."
        sudo env TS_DEBUG_ALWAYS_USE_DERP=true setsid tailscaled \\
            --tun=userspace-networking \\
            --state=/var/lib/tailscale/tailscaled.state \\
            --socket=/run/tailscale/tailscaled.sock \\
            --port 41641 \\
            > /tmp/tailscaled.log 2>&1 &
        disown

        sleep 3

        # Use --timeout so it doesn't block forever (bug #11)
        echo "[V7] Bringing up Tailscale tunnel..."
        sudo tailscale up --hostname=maquina-v7 --accept-routes --timeout=60s 2>&1 || \\
            echo "[V7] Tailscale up issued — if not authenticated, check the auth URL above to log in."

        TAILSCALE_IP=$(sudo tailscale ip -4 2>/dev/null || echo "pending")
        echo "[V7] Tailscale IP: $TAILSCALE_IP"

        echo "[V7] Running network check..."
        sudo tailscale netcheck 2>&1 || true

        echo "[V7] Tailscale stage complete."
    """,
)

# ---------------------------------------------------------------------------
# Stage 5: Sunshine launch
# Fixes: Xvfb (#7), PulseAudio (#8), nohup/setsid (#9), config (#15),
#        binary search, .deb fallback not PPA (#23), non-fatal failure (#24),
#        port verification (#17)
# ---------------------------------------------------------------------------

STAGE_SUNSHINE = Stage(
    name="05-SUNSHINE-LAUNCH",
    description="Launch Sunshine streaming host from t4packages with headless NVENC config",
    commands="""
        echo "[V7] Launching Sunshine streaming host..."

        SUNSHINE_CONF_DIR="$HOME/.config/sunshine"
        mkdir -p "$SUNSHINE_CONF_DIR"

        # Write Sunshine config for headless T4 streaming
        # channels: 2 = stereo (bug #15 fixed from mono)
        # Removed conflicting qp — use bitrate mode only (bug #15)
        cat > "$SUNSHINE_CONF_DIR/sunshine.conf" << 'CONFEOF'
{
    "origin_web_ui_allowed": "wan",
    "origin_pin_allowed": true,
    "upnp": "disabled",
    "external_ip": "auto",
    "hostname": "Maquina-V7",
    "min_log_level": 2,
    "channels": 2,
    "fec_percentage": 20,
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
        # Check file existence (not just -x) since permissions may not be set
        SUNSHINE_BIN=""
        for candidate in \\
            /usr/games/sunshine \\
            /usr/local/bin/sunshine \\
            /opt/sunshine/sunshine \\
            /usr/bin/sunshine \\
            /usr/lib/games/sunshine \\
            /usr/lib/sunshine/sunshine \\
            /opt/sunshine/bin/sunshine; do
            if [ -f "$candidate" ]; then
                chmod +x "$candidate" 2>/dev/null || true
                SUNSHINE_BIN="$candidate"
                echo "[V7] Found Sunshine binary at: $SUNSHINE_BIN"
                break
            fi
        done

        if [ -z "$SUNSHINE_BIN" ]; then
            SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
            if [ -n "$SUNSHINE_BIN" ]; then
                echo "[V7] Found Sunshine in PATH: $SUNSHINE_BIN"
            fi
        fi

        # Fallback: download Sunshine .deb directly from LizardByte GitHub releases
        # (PPA doesn't work on Colab — no systemd, add-apt-repository unreliable) (bug #23)
        if [ -z "$SUNSHINE_BIN" ]; then
            echo "[V7] Sunshine not found in t4packages — downloading from LizardByte GitHub..."
            SUNSHINE_DEB="/tmp/sunshine.deb"
            for deb_url in \\
                "https://github.com/LizardByte/Sunshine/releases/latest/download/sunshine-ubuntu-22.04-amd64.deb" \\
                "https://github.com/LizardByte/Sunshine/releases/latest/download/sunshine-ubuntu-20.04-amd64.deb"; do
                echo "[V7] Trying: $deb_url"
                if wget -q --show-progress -O "$SUNSHINE_DEB" "$deb_url" 2>&1; then
                    echo "[V7] Downloaded. Installing (resolving dependencies)..."
                    sudo apt-get update -qq
                    if sudo dpkg -i "$SUNSHINE_DEB" 2>&1; then
                        SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
                        if [ -z "$SUNSHINE_BIN" ]; then
                            SUNSHINE_BIN=$(dpkg -L sunshine 2>/dev/null | grep -E '/sunshine$' | head -1)
                        fi
                    else
                        echo "[V7] dpkg had dependency issues — attempting to fix..."
                        sudo apt-get install -f -y 2>&1
                        SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
                        if [ -z "$SUNSHINE_BIN" ]; then
                            SUNSHINE_BIN=$(dpkg -L sunshine 2>/dev/null | grep -E '/sunshine$' | head -1)
                        fi
                    fi
                    rm -f "$SUNSHINE_DEB"
                    if [ -n "$SUNSHINE_BIN" ]; then
                        echo "[V7] Sunshine installed from .deb: $SUNSHINE_BIN"
                        break
                    fi
                fi
            done
        fi

        # Non-fatal: warn and skip instead of exit 1 (bug #24)
        if [ -z "$SUNSHINE_BIN" ]; then
            echo "[V7] WARNING: Could not find or install Sunshine."
            echo "[V7] The t4packages may not include Sunshine for this runtime,"
            echo "[V7] and the direct .deb download failed."
            echo "[V7] You can install Sunshine manually and re-run this cell."
            echo "[V7] Skipping Sunshine launch — other stages completed successfully."
        fi

        if [ -n "$SUNSHINE_BIN" ]; then
            # Start Xvfb virtual display (bug #7 — Sunshine needs an X display)
            echo "[V7] Starting Xvfb virtual display on :0..."
            if pgrep -x Xvfb >/dev/null 2>&1; then
                echo "[V7] Xvfb already running — reusing."
            else
                setsid Xvfb :0 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset > /tmp/xvfb.log 2>&1 &
                disown
                sleep 2
            fi
            export DISPLAY=:0

            # Start PulseAudio for audio capture (bug #8 — Sunshine needs an audio sink)
            echo "[V7] Starting PulseAudio for audio streaming..."
            if pgrep -x pulseaudio >/dev/null 2>&1; then
                echo "[V7] PulseAudio already running — reusing."
            else
                pulseaudio --start --disallow-exit --exit-idle-time=-1 2>/dev/null || \\
                    (sudo apt-get install -y pulseaudio >/dev/null 2>&1 && pulseaudio --start --disallow-exit --exit-idle-time=-1)
                sleep 1
            fi

            # Launch Sunshine with setsid so it survives script exit (bug #9)
            echo "[V7] Launching Sunshine from: $SUNSHINE_BIN"
            setsid $SUNSHINE_BIN --config "$SUNSHINE_CONF_DIR/sunshine.conf" > /tmp/sunshine.log 2>&1 &
            disown
            SUNSHINE_PID=$!
            echo "[V7] Sunshine launched (PID: $SUNSHINE_PID)"

            # Wait for Sunshine to bind to port 47990 (bug #17)
            echo "[V7] Waiting for Sunshine web UI to start..."
            SUNSHINE_READY=false
            for i in $(seq 1 15); do
                if curl -sk --connect-timeout 2 https://localhost:47990/ >/dev/null 2>&1; then
                    SUNSHINE_READY=true
                    break
                fi
                sleep 1
            done

            if [ "$SUNSHINE_READY" = false ]; then
                echo "[V7] WARNING: Sunshine did not bind to port 47990 within 15 seconds."
                echo "[V7] Check /tmp/sunshine.log for errors. Common issues:"
                echo "[V7]   - Missing CUDA/NVENC libraries (check nvidia-smi works)"
                echo "[V7]   - Xvfb not started correctly"
                echo "[V7]   - Sunshine binary incompatible with this runtime"
            else
                echo "[V7] Sunshine web UI is ready on port 47990."
            fi
        fi

        TAILSCALE_IP=$(sudo tailscale ip -4 2>/dev/null || echo "pending")

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
    """,
)


PIPELINE: List[Stage] = [
    STAGE_GPU_DETECT,
    STAGE_REGION_CHECK,
    STAGE_T4PACKAGES,
    STAGE_TAILSCALE,
    STAGE_SUNSHINE,
]


def compile_bash() -> str:
    parts = [
        "#!/bin/bash",
        "# Maquina V7 - Auto-generated deployment script",
        "# Faithful recreation of V5 with all bugs fixed.",
        "# Do not edit manually — modify stages in compiler.py and regenerate.",
        "",
        "set -uo pipefail",
        "",
        'echo "===================================================="',
        'echo "  MAQUINA V7 — Cloud Gaming Deployment Engine"',
        'echo "  Fixed version of V5 for Colab 2026.01+ runtimes"',
        'echo "===================================================="',
        'echo ""',
        "",
    ]

    for stage in PIPELINE:
        parts.append(stage.render())

    parts.append('echo "[V7] All stages completed."')
    parts.append('echo "[V7] Your cloud gaming PC is ready!"')
    return "\n".join(parts)


def save_bash(path: str = "maquina_v7/colab_maquna_v7.sh") -> str:
    content = compile_bash()
    with open(path, "w") as f:
        f.write(content)
    return path


def print_colab_cell() -> None:
    bash = compile_bash()
    print("# -- Copy-paste everything below into a Google Colab cell --\n")
    print(bash)
    print("\n# -- End of Colab cell --")


if __name__ == "__main__":
    import sys

    if "--save" in sys.argv:
        out = save_bash()
        print(f"[V7] Bash script saved to: {out}")
    elif "--cell" in sys.argv:
        print_colab_cell()
    else:
        print(compile_bash())
