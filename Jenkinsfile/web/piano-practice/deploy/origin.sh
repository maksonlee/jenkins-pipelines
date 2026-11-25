#!/usr/bin/env bash
set -euo pipefail

: "${PIANO_DEPLOY_KEY:?}" "${PIANO_KNOWN_HOSTS:?}" "${PIANO_DEPLOY_USER:?}" "${DEPLOY_EVIDENCE:?}"
if [[ "$PIANO_DEPLOY_USER" != 'piano-practice-deploy' ]]; then
    echo 'The SSH credential must use the dedicated piano-practice-deploy account.' >&2
    exit 1
fi
attempt=$(<"$DEPLOY_EVIDENCE/attempt-id")
if [[ ! "$attempt" =~ ^[a-f0-9]{32}$ ]]; then
    echo 'Invalid deployment attempt ID' >&2
    exit 1
fi
connection=(
    ssh -T -F /dev/null -i "$PIANO_DEPLOY_KEY"
    -o BatchMode=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=$PIANO_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null
    -o UpdateHostKeys=no -o ConnectTimeout=12 -o ServerAliveInterval=10 -o ServerAliveCountMax=3
    "$PIANO_DEPLOY_USER@app.maksonlee.com"
)

case "${1:-}" in
    stage)
        : "${SELECTED_CI_BUILD:?}" "${CI_ARTIFACTS:?}" "${EXPECTED_RELEASE:?}"
        if [[ ! "$SELECTED_CI_BUILD" =~ ^[1-9][0-9]{0,8}$ || ! "$EXPECTED_RELEASE" =~ ^[0-9a-f]{20}$ ]]; then
            echo 'Invalid CI build number or expected release ID' >&2
            exit 1
        fi
        tar --format=ustar -C "$CI_ARTIFACTS" -cf - release.json checksums.json piano-practice-dist.tar.gz |
            "${connection[@]}" "stage $attempt $SELECTED_CI_BUILD $EXPECTED_RELEASE"
        ;;
    activate|confirm|rollback)
        "${connection[@]}" "$1 $attempt" </dev/null
        ;;
    *)
        echo 'Unsupported deployment operation' >&2
        exit 1
        ;;
esac
