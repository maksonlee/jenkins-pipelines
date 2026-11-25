#!/bin/bash
# Test and build the release web app of the Flutter project in ./source, for staticSiteDeploy(flutterWeb: true).
# The Flutter version comes from the project's .fvmrc, and the build arguments from its tool/build_flutter.py, which
# decides the build identity shown in the app; only its arguments are taken from it. The result is source/build/web.
#   FLUTTER_IMAGE, FLUTTER_CACHE_ROOT, BUILD_CONTAINER_RESOURCE_ARGS   set by the step
set -euo pipefail
: "${FLUTTER_IMAGE:?}" "${FLUTTER_CACHE_ROOT:?}" "${WORKSPACE:?}"

FLUTTER_VERSION=$(python3 -I - source/.fvmrc <<'PY'
import json, re, sys
version = json.load(open(sys.argv[1]))['flutter']
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
    raise SystemExit('Unexpected Flutter version in .fvmrc')
print(version)
PY
)
export FLUTTER_VERSION
echo "Flutter version: $FLUTTER_VERSION"

mkdir -p "$FLUTTER_CACHE_ROOT/fvm" "$FLUTTER_CACHE_ROOT/pub-cache"
python3 -I - "$WORKSPACE/source" > flutter-build-arguments <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'tool'))
import build_flutter
command = build_flutter.build_command(root, ['web', '--release'])
if command[:3] != ['fvm', 'flutter', 'build'] or any('-modified' in argument for argument in command):
    raise SystemExit('Unexpected web build command')
sys.stdout.write('\0'.join(command[2:]) + '\0')
PY
test -z "$(git -C source status --porcelain)"
# shellcheck disable=SC2086
docker run --rm ${BUILD_CONTAINER_RESOURCE_ARGS:-} -u "$(id -u):$(id -g)" \
  -e HOME=/home/ubuntu -e FVM_CACHE_PATH=/home/ubuntu/fvm -e PUB_CACHE=/home/ubuntu/pub-cache \
  -e FLUTTER_VERSION \
  -v "$FLUTTER_CACHE_ROOT/fvm:/home/ubuntu/fvm" -v "$FLUTTER_CACHE_ROOT/pub-cache:/home/ubuntu/pub-cache" \
  -v "$WORKSPACE/source:/workspace" -v "$WORKSPACE/flutter-build-arguments:/flutter-build-arguments:ro" \
  -w /workspace "$FLUTTER_IMAGE" bash -lc '
    set -euo pipefail
    flutter="/home/ubuntu/fvm/versions/$FLUTTER_VERSION/bin/flutter"
    fvm install "$FLUTTER_VERSION"
    "$flutter" config --no-analytics
    "$flutter" pub get
    "$flutter" test --no-pub
    xargs -0 "$flutter" < /flutter-build-arguments
  '
