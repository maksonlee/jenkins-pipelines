#!/bin/bash
# Record the source HEAD and status after the build and fail if the build moved HEAD or dirtied the checkout.
set -euo pipefail
mkdir -p evidence
git -C source rev-parse --verify 'HEAD^{commit}' > evidence/git-head-after-build.txt
git -C source status --porcelain=v1 > evidence/git-status-after-build.txt
test "$(cat evidence/git-head-after-build.txt)" = "$NATIVE_ANDROID_SOURCE_COMMIT"
test ! -s evidence/git-status-after-build.txt
