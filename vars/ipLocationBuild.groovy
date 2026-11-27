def run(String role) {
    if (!(role in ['ci', 'release']) || env.JOB_NAME != 'web/ip-location/' + role ||
            !(env.IP_LOCATION_COMMIT ==~ /[0-9a-f]{40}/)) {
        error('Invalid IP Location build context')
    }
    withEnv(['IP_LOCATION_ROLE=' + role, 'GIT_COMMIT=' + env.IP_LOCATION_COMMIT]) {
        sh '''#!/usr/bin/env bash
set -euo pipefail
image=$(docker build --quiet pipeline-source/Jenkinsfile/web/ip-location)
mkdir -p "$WORKSPACE/build-cache"
run=(docker run --rm --init --cpus=4 --memory=6g --shm-size=1g --user "$(id -u):$(id -g)"
     --mount "type=bind,src=$WORKSPACE/source,dst=/app"
     --mount "type=bind,src=$WORKSPACE/build-cache,dst=/build-cache"
     --workdir /app --env HOME=/build-cache --env CARGO_HOME=/build-cache/cargo --env CI=true)

if [[ "$IP_LOCATION_ROLE" == ci ]]; then
    "${run[@]}" "$image" bash -euo pipefail -c '
        cargo fetch --locked
        pnpm --dir web install --frozen-lockfile
        pnpm --dir web exec playwright install chromium
    '
    "${run[@]}" --network none "$image" bash -euo pipefail -c '
        cargo fmt --all -- --check
        cargo check --locked --offline --workspace --all-targets
        cargo clippy --locked --offline --workspace --all-targets -- -D warnings
        cargo test --locked --offline --workspace
        cargo build --locked --offline --workspace
        pnpm --dir web format:check
        pnpm --dir web typecheck
        pnpm --dir web build
        pnpm --dir web test
    '
else
    "${run[@]}" --env JOB_NAME --env BUILD_NUMBER --env GIT_COMMIT \\
        "$image" bash scripts/build-release.sh
fi
'''
    }
}
