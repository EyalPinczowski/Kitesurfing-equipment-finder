#!/data/data/com.termux/files/usr/bin/bash
# Installs kitefinder in Termux and runs it as a background service.
#   bash install_termux.sh            install / update
#   bash install_termux.sh --no-service   install only, no background service
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE=1
[[ "${1:-}" == "--no-service" ]] && SERVICE=0

if [[ -z "${PREFIX:-}" || ! -d "$PREFIX" ]]; then
  echo "This script is for Termux on Android." >&2
  exit 1
fi

echo "==> Termux packages"
pkg update -y
# python-pillow: photo checks (prebuilt, nothing to compile); cloudflared: the Mini App tunnel;
# termux-services: keeps the agent running; termux-api: phone notifications (optional)
pkg install -y python python-pillow cloudflared termux-services termux-api

echo "==> Python packages"
# (no `pip install --upgrade pip`: Termux forbids it — pip comes with the python package)
python -m pip install -e "$APP_DIR"

echo "==> Settings"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env — fill in GEMINI_API_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
fi
mkdir -p "$APP_DIR/secrets" "$APP_DIR/data"
chmod 700 "$APP_DIR/secrets"
chmod 600 "$APP_DIR/.env"

echo "==> Share to Termux: sharing a link to Termux adds that listing"
mkdir -p "$HOME/bin"
install -m 755 "$APP_DIR/bin/termux-url-opener" "$HOME/bin/termux-url-opener"

if [[ "$SERVICE" == 1 ]]; then
  echo "==> Background service"
  SV="$PREFIX/var/service/kitefinder"
  mkdir -p "$SV/log"
  cat > "$SV/run" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# keeps the CPU awake so scheduled searches run with the screen off
termux-wake-lock
cd "$APP_DIR"
exec kitefinder daemon 2>&1
EOF
  chmod 755 "$SV/run"
  ln -sf "$PREFIX/share/termux-services/svlogger" "$SV/log/run"
  # Termux:Boot (optional app): start again after the phone restarts
  mkdir -p "$HOME/.termux/boot"
  cat > "$HOME/.termux/boot/kitefinder" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
. "$PREFIX/etc/profile.d/start-services.sh"
EOF
  chmod 755 "$HOME/.termux/boot/kitefinder"
  echo "Service installed. After you fill in .env:"
  echo "  sv-enable kitefinder        # start now and keep running"
  echo "  tail -f $PREFIX/var/log/sv/kitefinder/current   # the log"
  echo "(If sv-enable is not found, close and reopen Termux once — termux-services starts then.)"
fi

echo "==> Check"
kitefinder llm status || true
echo "Done. Next: kitefinder profile set ... or /setup in your Telegram bot. See README.md."
