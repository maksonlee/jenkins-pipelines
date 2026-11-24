#!/bin/bash
# Print the checked-out release commit after confirming that it is contained in origin/RELEASE_BRANCH, that the
# checkout is clean and that none of RELEASE_FORBIDDEN_PATHS (one per line) exists. The release job fetches
# signing material only after this passes.
set -euo pipefail
branch="${RELEASE_BRANCH:?RELEASE_BRANCH is required}"
release_commit=$(git rev-parse --verify 'HEAD^{commit}')
git rev-parse --verify "refs/remotes/origin/${branch}^{commit}" >/dev/null
if ! git merge-base --is-ancestor "$release_commit" "refs/remotes/origin/${branch}"; then
    echo "Release commit is not contained in origin/${branch}; refusing to expose signing credentials." >&2
    exit 1
fi
if [[ -n "$(git status --porcelain=v1)" ]]; then
    echo 'The release checkout is dirty.' >&2
    exit 1
fi
while IFS= read -r path; do
    if [[ -n "$path" && -e "$path" ]]; then
        echo "The release checkout must not contain $path." >&2
        exit 1
    fi
done <<< "${RELEASE_FORBIDDEN_PATHS:-}"
echo "$release_commit"
