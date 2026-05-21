#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILTER_DIR="/usr/lib/cups/filter"
BACKEND_DIR="/usr/lib/cups/backend"
LIB_DIR="/usr/lib/cups/nimbot-driver"
MODEL_DIR="/usr/share/ppd/nimbot"

install -d "${FILTER_DIR}" "${BACKEND_DIR}" "${LIB_DIR}" "${MODEL_DIR}"
install -m 0755 "${ROOT_DIR}/bin/nimbot-render" "${FILTER_DIR}/nimbot-render"
install -o root -g root -m 0500 "${ROOT_DIR}/bin/nimbot-backend" "${BACKEND_DIR}/nimbot"
rm -rf "${LIB_DIR}/nimbot_cups"
cp -r "${ROOT_DIR}/nimbot_cups" "${LIB_DIR}/nimbot_cups"
install -m 0644 "${ROOT_DIR}/ppd/Nimbot-B1.ppd" "${MODEL_DIR}/Nimbot-B1.ppd"

python3 - <<'PY'
import sys
sys.path.insert(0, "/usr/lib/cups/nimbot-driver")
import nimbot_cups  # noqa: F401
print("Python module import check: OK")
PY

cat <<'EOF'
Driver installed.

Example queue creation:
  sudo lpadmin -p NimbotB1-BT -E \
    -v 'nimbot://bluetooth?address=XX%3AXX%3AXX%3AXX%3AXX%3AXX&model=b1' \
    -P /usr/share/ppd/nimbot/Nimbot-B1.ppd

  sudo lpadmin -p NimbotB1-USB -E \
    -v 'nimbot://usb?port=%2Fdev%2FttyACM0&model=b1' \
    -P /usr/share/ppd/nimbot/Nimbot-B1.ppd
EOF
