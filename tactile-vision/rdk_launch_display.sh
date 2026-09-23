#!/usr/bin/env bash
set -eu

# V4 RDK is a read-only digital twin. It never starts a model, compiler, or
# synthetic frame generator. The default URL is supplied by the PC reverse
# SSH tunnel so the board always reads the PC's authoritative runtime.
DISPLAY_URL="${1:-http://127.0.0.1:18765/display.html}"
RUNTIME_URL="${DISPLAY_URL%/display.html}/api/runtime/frame"
LOG_FILE="${HOME}/.cache/tactile-v4-browser.log"
FIREFOX_PROFILE="${HOME}/.mozilla/tactile-v4-kiosk"

mkdir -p "${HOME}/.cache"
mkdir -p "${FIREFOX_PROFILE}"
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-${HOME}/.Xauthority}"

echo "[1/3] Waiting for the authoritative PC runtime..."
until curl --fail --silent --show-error --max-time 3 "${RUNTIME_URL}" >/dev/null 2>&1; do
  echo "Runtime unavailable; waiting to reconnect: ${RUNTIME_URL}"
  sleep 2
done

echo "[2/3] Selecting kiosk browser..."
BROWSER=""
for candidate in chromium-browser chromium google-chrome firefox; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    BROWSER="${candidate}"
    break
  fi
done

if [ -z "${BROWSER}" ]; then
  echo "No supported browser is installed." >&2
  echo "Open manually: ${DISPLAY_URL}" >&2
  exit 1
fi

echo "[3/3] Opening ${DISPLAY_URL} with ${BROWSER}..."
pkill -TERM -x firefox 2>/dev/null || true
pkill -f "chromium.*${DISPLAY_URL}" 2>/dev/null || true
for _ in 1 2 3 4 5; do
  pgrep -x firefox >/dev/null 2>&1 || break
  sleep 1
done

case "${BROWSER}" in
  chromium-browser|chromium|google-chrome)
    nohup "${BROWSER}" --kiosk --disable-session-crashed-bubble --no-first-run "${DISPLAY_URL}" \
      >"${LOG_FILE}" 2>&1 &
    ;;
  firefox)
    nohup "${BROWSER}" --no-remote --new-instance --profile "${FIREFOX_PROFILE}" \
      --kiosk "${DISPLAY_URL}" >"${LOG_FILE}" 2>&1 &
    ;;
esac

sleep 3
if command -v xdotool >/dev/null 2>&1; then
  xdotool search --onlyvisible --name "Tactile Surface" 2>/dev/null \
    | head -n 1 \
    | xargs -r xdotool windowactivate
fi

echo "RDK display started."
echo "Authoritative runtime: ${RUNTIME_URL}"
echo "HDMI display: ${DISPLAY_URL}"
echo "Browser log: ${LOG_FILE}"
