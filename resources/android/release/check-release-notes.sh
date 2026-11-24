#!/bin/bash
# Require Google Play release notes for the selected track: every locale directory under RELEASE_NOTES_DIR needs
# a non-empty <PLAY_TRACK>.txt or default.txt, and at least one locale must exist. Prints the file used per locale.
set -euo pipefail
shopt -s nullglob
notes_dir="${RELEASE_NOTES_DIR:?RELEASE_NOTES_DIR is required}"
track="${PLAY_TRACK:?PLAY_TRACK is required}"
locales=("$notes_dir"/*/)
if (( ${#locales[@]} == 0 )); then
    echo "Missing Google Play release notes under $notes_dir." >&2
    exit 1
fi
for locale in "${locales[@]}"; do
    locale=${locale%/}
    if [[ -s "$locale/$track.txt" ]]; then
        echo "$(basename "$locale"): $track.txt"
    elif [[ -s "$locale/default.txt" ]]; then
        echo "$(basename "$locale"): default.txt"
    else
        echo "Missing non-empty $track.txt or default.txt in $locale." >&2
        exit 1
    fi
done
