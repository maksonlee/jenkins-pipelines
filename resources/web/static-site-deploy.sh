#!/bin/bash
# One step of a static-site deployment (vars/staticSiteDeploy.groovy), chosen by DEPLOY_STEP. The origin helper
# static_site_release.py (HELPER) runs locally for `package` and as root over SSH on ORIGIN for everything else;
# nothing is installed on the origin. Every result is kept under EVIDENCE.
#   extract    SITE_DIRECTORY of RELEASE_COMMIT in ./source becomes build/site
#   package    SITE_TREE becomes the release archive; reads what is live; with a mode other than verify-only the
#              release ID must equal EXPECTED_RELEASE
#   stage      copies the archive to the origin and unpacks it beside the site
#   activate   switches the live files, then checks the public site: STATUS_CHECKS holds "<path> <HTTP status>"
#              lines and SAME_FILES "<path> <file in the release>" lines whose public bytes must be the built bytes
#   rollback   puts the previous files back
set -euo pipefail
: "${DEPLOY_STEP:?}" "${SITE_NAME:?}" "${HELPER:?}" "${ORIGIN:?}" "${EVIDENCE:?}"
ssh_options=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=12)

origin() {  # helper arguments
    ssh "${ssh_options[@]}" "$ORIGIN" "sudo -n python3 - $SITE_NAME $*" < "$HELPER"
}

case "$DEPLOY_STEP" in
extract)
    mkdir -p build/site
    git -C source archive "${RELEASE_COMMIT:?}" "${SITE_DIRECTORY:?}" | tar -x -C build/site --strip-components=1
    ;;
package)
    mkdir -p "$EVIDENCE"
    python3 "$HELPER" "$SITE_NAME" package "${SITE_TREE:?}" "${RELEASE_COMMIT:?}" "$EVIDENCE" > /dev/null
    cat "$EVIDENCE/candidate.json"
    origin status > "$EVIDENCE/origin-status.json"
    python3 - "$EVIDENCE" "${DEPLOY_MODE:?}" "${EXPECTED_RELEASE:-}" <<'PY'
import json, sys
evidence, mode, expected = sys.argv[1:]
candidate = json.load(open(f'{evidence}/candidate.json'))
status = json.load(open(f'{evidence}/origin-status.json'))
same = candidate['releaseId'].endswith('-' + status['liveContent'])
print(f"Candidate {candidate['releaseId']} (version {candidate['version']}) from {candidate['commit']}; "
      f"live version: {status['liveVersion']}; live files identical to this build: {same}; "
      f"already staged: {candidate['releaseId'] in status['staged']}")
if mode != 'verify-only' and expected != candidate['releaseId']:
    raise SystemExit('The built release differs from EXPECTED_RELEASE; run verify-only again')
PY
    ;;
stage)
    remote="/tmp/$SITE_NAME-release-${RELEASE_ID:?}.tar.gz"
    scp -q "${ssh_options[@]}" "$EVIDENCE/site-release.tar.gz" "$ORIGIN:$remote"
    trap 'ssh "${ssh_options[@]}" "$ORIGIN" "rm -f $remote"' EXIT
    origin stage "$RELEASE_ID" "${RELEASE_COMMIT:?}" "$remote" "${ARCHIVE_SHA256:?}" > "$EVIDENCE/staged.json"
    cat "$EVIDENCE/staged.json"
    ;;
activate)
    site="${SITE_URL:?}"
    origin activate "${RELEASE_ID:?}" "${RELEASE_COMMIT:?}" > "$EVIDENCE/activation.json"
    cat "$EVIDENCE/activation.json"

    : > "$EVIDENCE/public-checks.txt"
    check() {  # path, expected status
        local status
        status=$(curl --silent --show-error --max-time 30 --output /dev/null --write-out '%{http_code}' "$site$1")
        printf '%s %s\n' "$status" "$1" >> "$EVIDENCE/public-checks.txt"
        [[ "$status" == "$2" ]] || { echo "Unexpected HTTP $status for $1" >&2; return 1; }
    }
    same_file() {  # path, manifest path: the public bytes are the built bytes
        local expected actual
        expected=$(awk -v path="$2" '$2 == path { print $1 }' "$EVIDENCE/manifest.sha256")
        actual=$(curl --fail --silent --show-error --max-time 60 "$site$1" | sha256sum | cut -d' ' -f1)
        printf '%s %s\n' "$actual" "$1" >> "$EVIDENCE/public-checks.txt"
        [[ -n "$expected" && "$actual" == "$expected" ]] || { echo "$1 is not the built file" >&2; return 1; }
    }
    while read -r path status; do
        if [[ -n "$path" ]]; then
            check "$path" "$status"
        fi
    done <<< "${STATUS_CHECKS:-}"
    while read -r path file; do
        if [[ -n "$path" ]]; then
            same_file "$path" "$file"
        fi
    done <<< "${SAME_FILES:-}"
    cat "$EVIDENCE/public-checks.txt"
    ;;
rollback)
    origin rollback "${RELEASE_ID:?}" "${RELEASE_COMMIT:?}" > "$EVIDENCE/rollback.json"
    cat "$EVIDENCE/rollback.json"
    ;;
*)
    echo "Unknown DEPLOY_STEP: $DEPLOY_STEP" >&2
    exit 2
    ;;
esac
