#!/bin/bash
# Confirm that the CI checkout is the fetched tip of the configured branch and is clean, then print its SHA.
set -euo pipefail
branch="${NATIVE_ANDROID_BRANCH:?NATIVE_ANDROID_BRANCH is required}"
expected_commit=$(git rev-parse --verify "refs/remotes/origin/${branch}^{commit}")
source_commit=$(git rev-parse --verify 'HEAD^{commit}')
if [[ "$source_commit" != "$expected_commit" ]]; then
    echo "The checked-out commit does not match the fetched ${branch} tip" >&2
    exit 1
fi
if [[ -n "$(git status --porcelain=v1)" ]]; then
    echo 'The source checkout is dirty before build' >&2
    exit 1
fi
echo "$source_commit"
