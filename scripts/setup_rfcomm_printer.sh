#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: sudo $0 <bluetooth-mac> [rfcomm-index]" >&2
  exit 2
fi

MAC="$1"
INDEX="${2:-0}"
DEVICE="/dev/rfcomm${INDEX}"

rfcomm release "${INDEX}" >/dev/null 2>&1 || true
rfcomm bind "${INDEX}" "${MAC}" 1

if [[ ! -e "${DEVICE}" ]]; then
  echo "ERROR: ${DEVICE} was not created" >&2
  exit 1
fi

chgrp lp "${DEVICE}"
chmod 660 "${DEVICE}"

echo "RFCOMM device ready: ${DEVICE}"
ls -l "${DEVICE}"
