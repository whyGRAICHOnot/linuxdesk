#!/usr/bin/env bash
# LinuxDesk installer - Kubuntu / KDE Plasma (Wayland)
# Run:  bash linuxdesk_wayland.sh        (keep this file next to linuxdesk_wayland.py)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_FILE="$SCRIPT_DIR/linuxdesk_wayland.py"

# Where linuxdesk_wayland.py is downloaded from if it is NOT next to this script.
# GitHub repo:  https://raw.githubusercontent.com/whyGRAICHOnot/linuxdesk/main
# GitHub gist:  https://gist.githubusercontent.com/whyGRAICHOnot/GISTID/raw
RAW_URL="https://raw.githubusercontent.com/whyGRAICHOnot/linuxdesk/main"

# Add any extra programs you want installed at the same time inside the brackets.
PACKAGES=(
  krfb
  python3-aiohttp python3-evdev python3-gi
  gstreamer1.0-tools gstreamer1.0-pipewire
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
)

# ---- checks ---------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this as your normal user, not with sudo (it asks for sudo itself when needed)."
  exit 1
fi
if ! command -v apt >/dev/null 2>&1; then
  echo "This installer needs apt (Ubuntu / Kubuntu / Debian based systems)."
  exit 1
fi
if [ ! -f "$PY_FILE" ]; then
  case "$RAW_URL" in
    *whyGRAICHOnot*)
      echo "Can't find linuxdesk_wayland.py next to this script (looked in: $SCRIPT_DIR)."
      echo "Download both files into the same folder and try again."
      exit 1 ;;
  esac
  if ! command -v curl >/dev/null 2>&1; then
    echo "==> Installing curl (needs your password)..."
    sudo apt update && sudo apt install -y curl
  fi
  echo "==> Downloading linuxdesk_wayland.py..."
  TMP_PY="$(mktemp)"
  trap 'rm -f "$TMP_PY"' EXIT
  curl -fsSL "$RAW_URL/linuxdesk_wayland.py" -o "$TMP_PY"
  PY_FILE="$TMP_PY"
fi
if [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
  echo "Note: you are not in a Wayland session. LinuxDesk (this version) needs KDE Plasma on Wayland."
fi
case "${XDG_CURRENT_DESKTOP:-}" in
  *KDE*) ;;
  *) echo "Note: this doesn't look like KDE Plasma. LinuxDesk relies on KDE's virtual monitor." ;;
esac

# ---- 1. packages ----------------------------------------------------------
echo "==> Installing packages (needs your password)..."
sudo apt update
sudo apt install -y "${PACKAGES[@]}"

# ---- 2. touch input permission (/dev/uinput) -------------------------------
echo "==> Allowing touch input (/dev/uinput)..."
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/70-uinput.rules >/dev/null
echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
sudo modprobe uinput || true
sudo udevadm control --reload-rules
sudo udevadm trigger

# ---- 3. the 'linuxdesk' command -------------------------------------------
echo "==> Installing the 'linuxdesk' command..."
mkdir -p "$HOME/.local/bin"
install -m 755 "$PY_FILE" "$HOME/.local/bin/linuxdesk"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Note: ~/.local/bin is not in your PATH yet. Logging out and back in usually fixes that." ;;
esac

cat <<'EOF'

All done.

  1. Log out and back in ONCE (this activates the touch permission).
  2. Run:  linuxdesk
  3. Pick the screen "Virtual-android" in the KDE dialog (first run only).
  4. Open the printed address in Chrome on your Android device (same Wi-Fi).

EOF
