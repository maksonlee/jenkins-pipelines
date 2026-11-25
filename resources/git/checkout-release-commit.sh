#!/bin/bash
# In a fresh clone, check out the commit to release and print its SHA: RELEASE_GIT_REF is either RELEASE_BRANCH
# (its tip on origin) or a full commit SHA that origin/RELEASE_BRANCH already contains. The clone must be complete
# and the checkout ends detached and clean.
set -euo pipefail
branch="${RELEASE_BRANCH:?RELEASE_BRANCH is required}"
ref="${RELEASE_GIT_REF:?RELEASE_GIT_REF is required}"
if [[ "$(git rev-parse --is-shallow-repository)" != false ]]; then
    echo 'The release clone must not be shallow.' >&2
    exit 1
fi
if [[ "$ref" == "$branch" ]]; then
    commit=$(git rev-parse --verify "refs/remotes/origin/${branch}^{commit}")
elif [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
    commit=$(git rev-parse --verify "${ref}^{commit}")
    if ! git merge-base --is-ancestor "$commit" "refs/remotes/origin/${branch}"; then
        echo "Commit $commit is not contained in origin/${branch}." >&2
        exit 1
    fi
else
    echo "The reference must be ${branch} or a full commit SHA." >&2
    exit 1
fi
git checkout --quiet --detach "$commit"
if [[ -n "$(git status --porcelain)" ]]; then
    echo 'The release checkout is dirty.' >&2
    exit 1
fi
echo "$commit"
