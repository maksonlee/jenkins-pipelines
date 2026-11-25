#!/bin/bash
# Decode the Play service account for one run of play_version_preflight.py and remove it afterwards.
set -euo pipefail
umask 077
: "${PLAY_SERVICE_JSON_B64:?ERROR: PLAY_SERVICE_JSON_B64 is empty or unset}"
play_json=$(mktemp "$PWD/play-preflight-service.XXXXXX")
trap 'rm -f -- "$play_json"' EXIT
printf %s "${PLAY_SERVICE_JSON_B64}" | sed 's/^data:[^,]*,//' | tr -d '\r\n ' | base64 -d > "$play_json"
python3 "${PLAY_TOOLS:?PLAY_TOOLS is required}/play_version_preflight.py" \
  "$play_json" "$PLAY_PACKAGE_NAME" "$PLAY_VERSION_NAME" "$PLAY_VERSION_CODE"
