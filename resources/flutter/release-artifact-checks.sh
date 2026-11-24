#!/bin/bash
set -euo pipefail
bundletool="${ANDROID_CACHE_ROOT}/tools/bundletool-all-1.18.3.jar"
bundletool_sha256=a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29
mkdir -p "$(dirname "$bundletool")"
if [ ! -f "$bundletool" ]; then
  download=$(mktemp "${bundletool}.XXXXXX")
  trap 'rm -f "$download"' EXIT
  curl --fail --location --retry 3 \
    https://github.com/google/bundletool/releases/download/1.18.3/bundletool-all-1.18.3.jar \
    --output "$download"
  printf '%s  %s\n' "$bundletool_sha256" "$download" | sha256sum --check --status
  mv "$download" "$bundletool"
  trap - EXIT
fi
printf '%s  %s\n' "$bundletool_sha256" "$bundletool" | sha256sum --check --status
mapfile -t aab_files < <(compgen -G "${FLUTTER_ARTIFACTS}" || true)
if [ "${#aab_files[@]}" -ne 1 ]; then
  echo "Expected exactly one release AAB for artifact checks." >&2
  exit 1
fi
cd source
python3 scripts/check_android_release_artifact.py \
  --aab "${aab_files[0]#source/}" \
  --repo-root . \
  --android-sdk "${ANDROID_CACHE_ROOT}/flutter-android-sdk" \
  --bundletool "$bundletool" \
  --report "${RELEASE_ARTIFACT_REPORT#source/}"
