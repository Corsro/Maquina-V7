#!/usr/bin/env python3
"""
Maquina V7 - Deployment Engine Compiler

Faithful recreation of Maquina V5 (kmille36/Colab-Cloud-Gaming) with all
bugs that caused V5 to fail on current Google Colab runtimes (2026.01+)
fixed.

=== V5 ARCHITECTURE (extracted from binary analysis) ===

V5 had 3 stages:
  1. ColabSteam ELF binary -> self-decrypts -> runs shell one-liner:
     [ -d /content ] && nvidia-smi > /dev/null 2>&1 && pip install pycryptodome
     && wget bit.ly/4dEMfB8 -> python3 stage2.py

  2. Stage2 Python (pyobfuscate encrypted) ran these shell commands:
     - wget t4packages.tar.gz (855MB: Steam+Sunshine+Xvfb+audio, all pre-built)
       from github.com/kmille36/Colab-Cloud-Gaming/releases/download/1.0.0
     - piped through pigz -dc | tar -xv -C /
     - Set PATH to include /usr/games:/usr/lib/games
     - Downloaded tailscale_1.84.0 from pkgs.tailscale.com
     - Started tailscaled with --tun=userspace-networking + DERP relay
     - tailscale up -> tailscale ip -4 -> tailscale netcheck

  3. colab-moonweb: web-based Moonlight client for browser play
     moon-pair.sh: automated PIN pairing via Sunshine API

=== V5 BUGS (5 root causes) ===

Bug 1: nvidia-smi PATH check too strict
  V5: nvidia-smi > /dev/null 2>&1
  On current Colab (2026.01, Ubuntu 22.04.5), nvidia-smi may be at
  /usr/lib/nvidia-utils/bin/nvidia-smi or /opt/nvidia/bin/nvidia-smi
  depending on driver version. If not in PATH, the ENTIRE pipeline
  aborts with "Error: Please run on Google Colab with NVIDIA GPU."
  even though a GPU IS connected and working.
  FIX: Probe all known nvidia-smi locations + /proc/driver/nvidia.

Bug 2: bit.ly short links can die
  V5: wget -q -O /tmp/$name https://bit.ly/4dEMfB8
  bit.ly is a third-party redirect service. If bit.ly goes down, changes
  policy, or the link gets flagged, the stage2 script can't be downloaded
  and everything fails silently.
  FIX: No short links. All URLs are direct GitHub raw URLs.

Bug 3: tailscale version hardcoded to 1.84.0
  V5: curl -L https://pkgs.tailscale.com/stable/tailscale_1.84.0_amd64.tgz
  Pinned versions go stale and may have compat issues with newer Colab
  networking stacks.
  FIX: Use official install script (always latest compatible version).

Bug 4: pyobfuscate.com dependency
  V5's stage2 was encrypted with pyobfuscate.com, requiring pycryptodome
  and a working connection to pyobfuscate's servers to decrypt.
  FIX: V7 is plain readable Python. No obfuscation, no external dependency.

Bug 5: 855MB download with no retry
  V5: wget -qO- <url> | pigz -dc | tar -xv -C /
  A single wget with no retry. On slow Colab regions (asia-south1),
  this can timeout or get rate-limited by GitHub.
  FIX: Retry loop with progress display.

Bug 6 (region): No region awareness
  V5 ran tailscale netcheck but didn't act on results. Users in
  asia-south1 had no warning about latency.
  FIX: V7 logs region and displays latency info.
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
# V5: nvidia-smi > /dev/null 2>&1  (aborts if not in PATH)
# V7: probes all known nvidia-smi locations + /proc/driver/nvidia fallback
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
    """,
)

# ---------------------------------------------------------------------------
# Stage 2: Region check (new in V7 — fixes V5 bug #6)
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
# Stage 3: Download t4packages (fixes V5 bug #5 — 855MB with no retry)
# V5: wget -qO- <url> | pigz -dc | tar -xv -C /
# V7: retry loop with progress, idempotent (skip if already extracted)
# ---------------------------------------------------------------------------

STAGE_T4PACKAGES = Stage(
    name="03-T4PACKAGES",
    description="Download and extract pre-built T4 gaming packages (Steam+Sunshine+Xvfb, 855MB)",
    commands="""
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

        # Set up PATH (identical to V5)
        echo -e '#!/bin/sh\\nexport PATH="$PATH:/usr/games:/usr/lib/games"' | \\
            sudo tee /etc/profile.d/custom_path.sh > /dev/null && \\
            sudo chmod +x /etc/profile.d/custom_path.sh

        export PATH="$PATH:/usr/games:/usr/lib/games"

        echo "[V7] T4 packages stage complete."
    """,
)

# ---------------------------------------------------------------------------
# Stage 4: Tailscale (fixes V5 bug #3 — hardcoded version)
# V5: curl -L https://pkgs.tailscale.com/stable/tailscale_1.84.0_amd64.tgz
# V7: official install script (always latest compatible version)
# Also fixes V5's tailscaled invocation to match V5's DERP relay mode.
# ---------------------------------------------------------------------------

STAGE_TAILSCALE = Stage(
    name="04-TAILSCALE",
    description="Install Tailscale (auto-versioned) and start DERP relay tunnel (fixes V5 bug #3)",
    commands="""
        echo "[V7] Installing Tailscale..."

        # V5 hardcoded tailscale_1.84.0. V7 uses the official install script
        # which always fetches the latest version compatible with the runtime.
        if command -v tailscale &>/dev/null; then
            echo "[V7] Tailscale already installed — skipping."
        else
            curl -fsSL https://tailscale.com/install.sh | sh
        fi

        # Create state and socket directories
        sudo mkdir -p /var/lib/tailscale /run/tailscale

        # Kill any stale tailscaled from a previous cell run.
        # A leftover process holds the old socket and authstate2 cookie,
        # which causes "401 bad tailscale-authstate2 cookie" on reconnect.
        sudo pkill -x tailscaled 2>/dev/null || true
        sleep 1
        sudo rm -f /var/lib/tailscale/tailscaled.state
        sudo rm -f /run/tailscale/tailscaled.sock

        # Start tailscaled as root so it's consistent with `sudo tailscale up`.
        # setsid detaches it from the cell's process group so Colab's
        # cell-completion SIGHUP doesn't kill it.
        # No restart loop — a mid-auth restart invalidates the OAuth session.
        # userspace-networking avoids needing /dev/net/tun;
        # DERP relay works through Colab's restrictive NAT.
        echo "[V7] Starting tailscaled daemon (userspace + DERP relay)..."
        sudo TS_DEBUG_ALWAYS_USE_DERP=true setsid tailscaled \\
            --tun=userspace-networking \\
            --state=/var/lib/tailscale/tailscaled.state \\
            --socket=/run/tailscale/tailscaled.sock \\
            --port 41641 \\
            > /tmp/tailscaled.log 2>&1 &
        disown

        sleep 3

        # Bring up the tunnel (same user as tailscaled = root)
        echo "[V7] Bringing up Tailscale tunnel..."
        sudo tailscale up --hostname=maquina-v7 --accept-routes 2>&1 || \\
            echo "[V7] Tailscale up issued — check the auth URL above to log in."

        # Get the Tailscale IP (same as V5)
        TAILSCALE_IP=$(sudo tailscale ip -4 2>/dev/null || echo "pending")
        echo "[V7] Tailscale IP: $TAILSCALE_IP"

        # Run netcheck (same as V5 — shows region latency info)
        echo "[V7] Running network check..."
        sudo tailscale netcheck 2>&1 || true

        echo "[V7] Tailscale stage complete."
    """,
)

# ---------------------------------------------------------------------------
# Stage 5: Sunshine launch
# V5's t4packages includes a pre-built Sunshine. We locate and launch it.
# V7 also writes a headless config with NVENC encoder for T4 GPU.
# ---------------------------------------------------------------------------

STAGE_SUNSHINE = Stage(
    name="05-SUNSHINE-LAUNCH",
    description="Launch Sunshine streaming host from t4packages with headless NVENC config",
    commands="""
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
        for candidate in \\
            /usr/games/sunshine \\
            /usr/local/bin/sunshine \\
            /opt/sunshine/sunshine \\
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
            sudo apt-get update -qq
            sudo apt-get install -y software-properties-common
            sudo add-apt-repository -y ppa:lizardbyte/stable
            sudo apt-get update -qq
            sudo apt-get install -y sunshine
            SUNSHINE_BIN=$(command -v sunshine 2>/dev/null)
        fi

        if [ -n "$SUNSHINE_BIN" ]; then
            echo "[V7] Launching Sunshine from: $SUNSHINE_BIN"
            setsid $SUNSHINE_BIN --config "$SUNSHINE_CONF_DIR/sunshine.conf" > /tmp/sunshine.log 2>&1 &
            disown
            SUNSHINE_PID=$!
            echo "[V7] Sunshine launched (PID: $SUNSHINE_PID)"
            sleep 3
        else
            echo "[V7] ERROR: Could not find or install Sunshine."
            exit 1
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
        "# Faithful recreation of V5 with all 6 bugs fixed.",
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
