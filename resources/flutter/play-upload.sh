#!/bin/bash
# Decode the Play service account for one run of play_upload.py with the single release AAB, the release notes
# loaded by the job and the approved track, status and rollout fraction, and remove it afterwards.
set -euo pipefail
umask 077
: "${PLAY_SERVICE_JSON_B64:?ERROR: PLAY_SERVICE_JSON_B64 is empty or unset}"
mapfile -t aab_files < <(compgen -G "${FLUTTER_ARTIFACTS:?FLUTTER_ARTIFACTS is required}" || true)
if [ "${#aab_files[@]}" -ne 1 ]; then
  echo "Expected exactly one release AAB to upload." >&2
  exit 1
fi
release_notes_path="$PWD/.play-release-notes.json"
if [ ! -s "$release_notes_path" ]; then
  echo "Missing Google Play release notes file: $release_notes_path" >&2
  exit 1
fi
play_json=$(mktemp "$PWD/play-service.XXXXXX")
trap 'rm -f -- "$play_json"' EXIT
printf %s "${PLAY_SERVICE_JSON_B64}" | sed 's/^data:[^,]*,//' | tr -d '\r\n ' | base64 -d > "$play_json"
python3 "${FLUTTER_CI_TOOLS:?FLUTTER_CI_TOOLS is required}/play_upload.py" \
  "$play_json" "${aab_files[0]}" "$PLAY_PACKAGE_NAME" "$PLAY_TRACK" "$PLAY_RELEASE_STATUS" \
  "${PLAY_ROLLOUT_FRACTION:-}" "$release_notes_path"
