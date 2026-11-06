#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run on the dedicated Linux Jenkins Docker worker, never from a Jenkins job.
# OAuth and Personal activation are one-time interactive setup operations.
image='harbor.maksonlee.com/library/unity-android-build-env@sha256:c45b47f9999a81df26f664419725ab5895438e5dbf6123bc002c766ecf3c4cf3'
tool_dir=/home/administrator/jenkins/unity-cli/1.0.0-beta.17
tool_sha=2f5246ee5bea5032da2917f5f0125d3cfe803c85dc34f78bd75a427db13a0b3c
state=/home/administrator/jenkins/unity/math-runner-worker-1
hostname=mathrunner-unity-worker-1
schema=mathrunner-unity-personal-v1

die() { echo "$*" >&2; exit 1; }
identity_digest() {
  {
    sha256sum "$state/identity/schema" "$state/identity/machine-id" \
      "$state/identity/passwd" "$state/identity/group"
    printf '%s\n' "$hostname" "$image" "$tool_sha"
  } | sha256sum | cut -d' ' -f1
}
check_host() {
  test "$(id -u):$(id -g)" = 1000:1000 || die 'Expected dedicated Jenkins UID:GID 1000:1000'
  printf '%s  %s\n' "$tool_sha" "$tool_dir/unity" | sha256sum --check --status ||
    die 'Pinned Unity CLI binary is missing or changed'
  docker image inspect "$image" >/dev/null ||
    die 'Pinned Unity image is not available locally'
}
check_state() {
  for dir in "$state" "$state/identity" "$state/home" "$state/share"; do
    test -d "$dir" || die "Missing worker state: $dir"
    test "$(stat -c '%u:%g:%a' "$dir")" = 1000:1000:700 ||
      die "Wrong ownership or mode: $dir"
  done
  test "$(cat "$state/identity/schema")" = "$schema" || die 'Worker state schema mismatch'
  local machine_id
  machine_id=$(cat "$state/identity/machine-id")
  [[ "$machine_id" =~ ^[0-9a-f]{32}$ ]] || die 'Invalid worker machine-id'
  grep -Eq '^unityci:x:1000:1000:[^:]*:/home/ubuntu:/bin/bash$' "$state/identity/passwd" ||
    die 'Fixed UID/passwd identity is missing'
  grep -Eq '^unityci:x:1000:$' "$state/identity/group" ||
    die 'Fixed GID/group identity is missing'
  for file in machine-id passwd group schema; do
    test "$(stat -c '%u:%g:%a' "$state/identity/$file")" = 1000:1000:600 ||
      die "Wrong identity file ownership or mode: $file"
  done
  test "$(stat -c '%u:%g:%a' "$state/worker.lock")" = 1000:1000:600 ||
    die 'Worker lock is missing or has wrong ownership/mode'
}
container_args() {
  args=(
    --rm --pull never
    --name mathrunner-unity-bootstrap
    --label com.maksonlee.mathrunner.worker=mathrunner-unity-worker-1
    --user 1000:1000 --hostname "$hostname"
    --cpus 6 --memory 12g --shm-size 2g
    --env HOME=/home/ubuntu --env USER=unityci --env LOGNAME=unityci
    --env XDG_RUNTIME_DIR=/run/user/1000
    --tmpfs /run/user/1000:rw,uid=1000,gid=1000,mode=0700
    --mount "type=bind,src=$state/home,dst=/home/ubuntu"
    --mount "type=bind,src=$state/share,dst=/usr/share/unity3d"
    --mount "type=bind,src=$state/identity/machine-id,dst=/etc/machine-id,readonly"
    --mount "type=bind,src=$state/identity/passwd,dst=/etc/passwd,readonly"
    --mount "type=bind,src=$state/identity/group,dst=/etc/group,readonly"
    --mount "type=bind,src=$tool_dir,dst=/opt/unity-cli,readonly"
  )
}
cli_status() {
  local kind=$1
  local output
  output=$(mktemp)
  trap 'rm -f -- "$output"' RETURN
  if ! timeout --signal=TERM --kill-after=10s 90s \
      docker run "${args[@]}" "$image" /opt/unity-cli/unity "$kind" status --format json \
      >"$output" 2>/dev/null 9<&-; then
    die "Unity CLI $kind status failed"
  fi
  python3 - "$output" "$kind" <<'PY'
import json
import pathlib
import sys
document = json.loads(pathlib.Path(sys.argv[1]).read_text())
data = document.get('data') or {}
if document.get('success') is not True:
    raise SystemExit('Unity CLI status did not report success')
if sys.argv[2] == 'auth' and data.get('loggedIn') is not True:
    raise SystemExit('Unity account is not signed in')
if sys.argv[2] == 'license' and (
        data.get('active') is not True or data.get('signedIn') is not True):
    raise SystemExit('Unity Personal license is not active')
PY
  trap - RETURN
  rm -f -- "$output"
  echo "Unity CLI $kind status: success"
}
init_state() {
  test ! -e "$state" || die 'Worker state already exists; refusing to regenerate identity'
  install -d -m 0700 /home/administrator/jenkins/unity
  mkdir -m 0700 "$state"
  mkdir -m 0700 "$state/identity" "$state/home" "$state/share"
  printf '%s\n' "$schema" > "$state/identity/schema"
  python3 - "$state/identity/machine-id" <<'PY'
import pathlib
import secrets
import sys
pathlib.Path(sys.argv[1]).write_text(secrets.token_hex(16) + '\n')
PY
  timeout --signal=TERM --kill-after=5s 30s \
    docker run --rm --pull never --network none "$image" cat /etc/passwd \
    > "$state/identity/passwd"
  timeout --signal=TERM --kill-after=5s 30s \
    docker run --rm --pull never --network none "$image" cat /etc/group \
    > "$state/identity/group"
  ! grep -Eq '^[^:]*:[^:]*:1000:' "$state/identity/passwd" ||
    die 'Image already has UID 1000; inspect it before adding identity'
  ! grep -Eq '^[^:]*:[^:]*:1000:' "$state/identity/group" ||
    die 'Image already has GID 1000; inspect it before adding identity'
  printf 'unityci:x:1000:1000:Unity CI:/home/ubuntu:/bin/bash\n' \
    >> "$state/identity/passwd"
  printf 'unityci:x:1000:\n' >> "$state/identity/group"
  chmod 0600 "$state/identity/"*
  : > "$state/worker.lock"
  chmod 0600 "$state/worker.lock"
  check_state
  echo 'Worker identity created; OAuth, activation and persistence validation are pending.'
}

usage() {
  echo 'Usage: bootstrap-unity-personal.sh {init|login|activate|status|verify|seal}' >&2
  exit 2
}
action=${1:-}
case "$action" in init|login|activate|status|verify|seal) ;; *) usage ;; esac
check_host
if [ "$action" = init ]; then
  init_state
  exit 0
fi
check_state
if [ -e "$state/ready" ] && [[ "$action" = login || "$action" = activate ]]; then
  die 'Sealed worker rejects a new login or activation'
fi
exec 9<"$state/worker.lock"
flock -x -w 60 9 || die 'Worker is busy'
worker_containers=$(docker ps -aq --filter label=com.maksonlee.mathrunner.worker=mathrunner-unity-worker-1) ||
  die 'Cannot enumerate worker containers'
test -z "$worker_containers" || die 'A worker container already exists'
container_args
cleanup_bootstrap() {
  local result=$?
  local ids
  ids=$(docker ps -aq --filter 'name=^/mathrunner-unity-bootstrap$') || exit 1
  if [ -n "$ids" ]; then
    python3 - "$state" "$ids" <<'PY'
import json
import pathlib
import subprocess
import sys
containers = json.loads(subprocess.check_output(
    ['docker', 'inspect', sys.argv[2]], text=True))
if len(containers) != 1:
    raise SystemExit('More than one bootstrap container exists')
container = containers[0]
labels = container['Config'].get('Labels') or {}
mounts = {(m.get('Source'), m.get('Destination')) for m in container.get('Mounts', [])}
state = pathlib.Path(sys.argv[1])
if (container.get('Name') != '/mathrunner-unity-bootstrap' or
        labels.get('com.maksonlee.mathrunner.worker') != 'mathrunner-unity-worker-1' or
        (str(state / 'home'), '/home/ubuntu') not in mounts or
        (str(state / 'share'), '/usr/share/unity3d') not in mounts):
    raise SystemExit('Bootstrap container mismatch; preserving it')
PY
    timeout --signal=TERM --kill-after=10s 120s \
      docker stop --time 90 "$ids" >/dev/null 9<&- || exit 1
  fi
  exit "$result"
}
trap cleanup_bootstrap EXIT
case "$action" in
  login)
    # The actual OAuth redirect must be observed before asking a person to use
    # the URL. This does not open the user's browser or expose a public port.
    timeout --signal=TERM --kill-after=10s 12m \
      docker run -i "${args[@]}" "$image" /opt/unity-cli/unity --no-banner auth login 9<&-
    ;;
  activate)
    cli_status auth
    timeout --signal=TERM --kill-after=10s 5m \
      docker run "${args[@]}" "$image" /opt/unity-cli/unity --no-banner \
      license activate --personal --accept-eula 9<&-
    cli_status license
    echo 'Personal activation succeeded; fresh-container Editor persistence check remains.'
    ;;
  status)
    cli_status auth
    cli_status license
    ;;
  verify)
    test ! -e "$state/persistence-proof" || die 'Persistence proof already exists'
    test ! -e "$state/ready" || die 'Worker is already sealed'
    test ! -e "$state/verification" || die 'Previous Editor verification evidence exists'
    cli_status auth
    cli_status license
    mkdir -m 0700 "$state/verification"
    verification="$state/verification"
    printf 'image=%s\ntool_sha256=%s\nidentity_sha256=%s\n' \
      "$image" "$tool_sha" "$(identity_digest)" > "$verification/inputs.txt"
    chmod 0600 "$verification/inputs.txt"
    work=$(mktemp -d /tmp/mathrunner-unity-editor.XXXXXXXX)
    cleanup_verify() {
      local result=$?
      local ids
      ids=$(docker ps -aq --filter 'name=^/mathrunner-unity-bootstrap$') || {
        echo 'Cannot enumerate bootstrap container; preserving Editor project.' >&2
        exit 1
      }
      if [ -n "$ids" ]; then
        docker inspect "$ids" > "$verification/container-inspect.json" || exit 1
        python3 - "$verification/container-inspect.json" "$work" "$verification" "$state" <<'PY'
import json
import pathlib
import sys
containers = json.loads(pathlib.Path(sys.argv[1]).read_text())
if len(containers) != 1:
    raise SystemExit('Bootstrap cleanup found more than one container')
container = containers[0]
labels = container['Config'].get('Labels') or {}
if (container.get('Name') != '/mathrunner-unity-bootstrap' or
        labels.get('com.maksonlee.mathrunner.worker') != 'mathrunner-unity-worker-1'):
    raise SystemExit('Bootstrap container identity mismatch; preserving it')
mounts = {(m.get('Source'), m.get('Destination')) for m in container.get('Mounts', [])}
common = {(str(pathlib.Path(sys.argv[4]) / 'home'), '/home/ubuntu'),
          (str(pathlib.Path(sys.argv[4]) / 'share'), '/usr/share/unity3d')}
project = {(sys.argv[2], '/workspace'), (sys.argv[3], '/logs')}
project_mounts = {item for item in mounts if item[1] in ('/workspace', '/logs')}
if not common.issubset(mounts) or (project_mounts and project_mounts != project):
    raise SystemExit('Bootstrap container mounts mismatch; preserving it')
PY
        timeout --signal=TERM --kill-after=10s 120s \
          docker stop --time 90 "$ids" >/dev/null 9<&- || exit 1
      fi
      if [[ "$work" = /tmp/mathrunner-unity-editor.* ]] && [ -d "$work" ]; then
        rm -rf -- "$work"
      else
        echo 'Refusing Editor project cleanup outside task directory.' >&2
        exit 1
      fi
      if [ "$result" -eq 0 ]; then
        identity_digest > "$state/persistence-proof"
        chmod 0600 "$state/persistence-proof"
      fi
      exit "$result"
    }
    trap cleanup_verify EXIT
    mkdir -p "$work/Assets" "$work/Packages" "$work/ProjectSettings"
    printf '{"dependencies":{}}\n' > "$work/Packages/manifest.json"
    printf 'm_EditorVersion: 6000.6.7f1\n' > "$work/ProjectSettings/ProjectVersion.txt"
    for pass in A B; do
      if timeout --signal=TERM --kill-after=30s 12m \
        docker run "${args[@]}" \
        --cidfile "$verification/Container-$pass.id" \
        --mount "type=bind,src=$work,dst=/workspace" \
        --mount "type=bind,src=$verification,dst=/logs" \
        --workdir /workspace \
        "$image" /opt/unity/Editor/Unity -batchmode -nographics -quit \
        -projectPath /workspace -logFile "/logs/Editor-$pass.log" 9<&-; then
        printf '0\n' > "$verification/Editor-$pass.exit"
      else
        result=$?
        printf '%s\n' "$result" > "$verification/Editor-$pass.exit"
        die "Editor container $pass failed (exit $result); protected log retained"
      fi
      test -s "$verification/Editor-$pass.log" || die "Editor $pass produced no log"
      cli_status license
      echo "Editor container $pass exited successfully with active Personal status."
    done
    test "$(cat "$verification/Container-A.id")" != "$(cat "$verification/Container-B.id")" ||
      die 'Editor A and B did not use distinct containers'
    sha256sum "$verification"/Editor-{A,B}.log > "$verification/log-sha256.txt"
    chmod 0600 "$verification"/*
    ;;
  seal)
    cli_status auth
    cli_status license
    test ! -e "$state/ready" || die 'Worker is already sealed'
    test "$(stat -c '%u:%g:%a' "$state/persistence-proof")" = 1000:1000:600 ||
      die 'No protected A/B Editor persistence proof'
    test "$(cat "$state/persistence-proof")" = "$(identity_digest)" ||
      die 'A/B Editor proof does not match worker identity/image/CLI'
    identity_digest > "$state/ready"
    chmod 0600 "$state/ready"
    echo 'Worker is sealed for normal Jenkins builds.'
    ;;
esac
