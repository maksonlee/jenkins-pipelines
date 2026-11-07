"""Bounded fake-Docker contract checks; no Unity, Vault or real Docker call."""
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(r"sh '''(.*?)'''", source, re.S)
assert len(blocks) == 8
preflight, build, post = (blocks[1], blocks[2], blocks[-1])
# Groovy decodes escaped backslashes in triple-single-quoted shell strings.
preflight, build, post = [x.replace("\\\\", "\\") for x in (preflight, build, post)]
real_state = "/home/administrator/jenkins/unity/math-runner-worker-1"
real_tool = "/home/administrator/jenkins/unity-cli/1.0.0-beta.17"
image = "harbor.maksonlee.com/library/unity-android-build-env@sha256:c45b47f9999a81df26f664419725ab5895438e5dbf6123bc002c766ecf3c4cf3"


def write_exe(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


with tempfile.TemporaryDirectory(prefix="mathrunner-contract-") as temp:
    root = pathlib.Path(temp)
    state = root / "state"
    tool = root / "tool"
    workspace = root / "workspace"
    fakebin = root / "bin"
    for path in (state, state / "identity", state / "home", state / "share",
                 tool, workspace, fakebin, pathlib.Path(str(workspace) + "@tmp"),
                 workspace / "Builds/Release/Android"):
        path.mkdir(parents=True, exist_ok=True)
    for path in (state, state / "identity", state / "home", state / "share"):
        path.chmod(0o700)
    identity = state / "identity"
    (identity / "schema").write_text("mathrunner-unity-personal-v1\n")
    (identity / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    (identity / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\nunityci:x:1000:1000:Unity CI:/home/ubuntu:/bin/bash\n")
    (identity / "group").write_text("root:x:0:\nunityci:x:1000:\n")
    for name in ("schema", "machine-id", "passwd", "group"):
        (identity / name).chmod(0o600)
    (state / "worker.lock").write_text("")
    (state / "worker.lock").chmod(0o600)
    (tool / "unity").write_text("#!/bin/sh\nexit 0\n")
    (tool / "unity").chmod(0o755)
    tool_sha = hashlib.sha256((tool / "unity").read_bytes()).hexdigest()
    checksums = subprocess.check_output([
        "sha256sum", *[str(identity / n) for n in
                      ("schema", "machine-id", "passwd", "group")]
    ], text=True)
    digest = hashlib.sha256((
        checksums + "mathrunner-unity-worker-1\n" + image + "\n" +
        tool_sha + "\n").encode()).hexdigest()
    for name in ("ready", "persistence-proof"):
        (state / name).write_text(digest + "\n")
        (state / name).chmod(0o600)
    log = root / "docker.log"
    write_exe(fakebin / "docker", """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  pull) exit 0 ;;
  ps) [ "${FAKE_PS_FAIL:-0}" = 0 ] || exit 9; exit 0 ;;
  run)
    for key in UPLOAD_JKS_B64 STORE_PASSWORD KEY_PASSWORD KEY_ALIAS UNITY_EMAIL UNITY_PASSWORD; do
      if printenv "$key" >/dev/null; then exit 81; fi
    done
    mkdir -p "$WORKSPACE/Evidence/Checks/Builds/PlayAab"
    printf bundle > "$WORKSPACE/Builds/Release/Android/SportsDayMath.aab"
    printf 'Build succeeded!\\n' > "$WORKSPACE/Evidence/Checks/Builds/PlayAab/Unity.log"
    exit 0 ;;
  *) exit 88 ;;
esac
""")
    write_exe(fakebin / "keytool", """#!/bin/sh
printf '    SHA256: %s\\n' 'AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA:AA'
""")
    write_exe(fakebin / "flock", """#!/bin/sh
[ "${FAKE_FLOCK_FAIL:-0}" = 0 ] || exit 9
exec /usr/bin/flock "$@"
""")
    build = build.replace(real_state, str(state)).replace(real_tool, str(tool))
    build = build.replace(
        "2f5246ee5bea5032da2917f5f0125d3cfe803c85dc34f78bd75a427db13a0b3c",
        tool_sha)
    post = post.replace(real_state, str(state))
    env = os.environ.copy()
    env.update({
        "PATH": str(fakebin) + os.pathsep + env["PATH"],
        "FAKE_DOCKER_LOG": str(log),
        "WORKSPACE": str(workspace),
        "BUILD_NUMBER": "42",
        "UNITY_IMAGE": image,
        "VERSION_NAME": "0.1.0",
        "ANDROID_VERSION_CODE": "1",
        "RELEASE_AAB": "Builds/Release/Android/SportsDayMath.aab",
        "UPLOAD_JKS_B64": "dGVzdA==",
        "STORE_PASSWORD": "synthetic-store-password",
        "KEY_PASSWORD": "synthetic-key-password",
        "KEY_ALIAS": "synthetic-alias",
    })

    def run(block, overrides=None):
        current = env | (overrides or {})
        return subprocess.run(["bash"], input=block.encode(), cwd=workspace,
                              env=current, capture_output=True)

    assert run(preflight).returncode == 0
    result = run(build)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    argv = log.read_text()
    assert "license activate" not in argv and "license return" not in argv
    assert "UNITY_EMAIL" not in argv and "UNITY_PASSWORD" not in argv
    assert "synthetic-" not in argv
    assert f"src={state / 'home'},dst=/home/ubuntu" in argv
    assert f"src={state / 'share'},dst=/usr/share/unity3d" in argv
    assert f"src={workspace}@tmp/mathrunner-ci-42/secrets,dst=/run/mathrunner-signing,readonly" in argv
    assert (workspace / "Builds/Release/Android/SportsDayMath.aab").is_file()
    assert (pathlib.Path(str(workspace) + "@tmp") / "mathrunner-ci-42/secrets/upload.jks").is_file()
    result = run(post)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert not (pathlib.Path(str(workspace) + "@tmp") / "mathrunner-ci-42").exists()
    assert (state / "home").is_dir() and (state / "share").is_dir()
    assert (state / "ready").read_text().strip() == digest
    log.write_text("")

    (state / "ready").unlink()
    assert run(build).returncode != 0
    assert " run " not in log.read_text()
    (state / "ready").write_text(digest + "\n")
    (state / "ready").chmod(0o600)
    stale = pathlib.Path(str(workspace) + "@tmp") / "mathrunner-ci-41"
    stale.mkdir(parents=True)
    assert run(preflight).returncode != 0
    assert run(build).returncode != 0
    stale.rmdir()
    log.write_text("")
    assert run(build, {"FAKE_PS_FAIL": "1"}).returncode != 0
    assert " run " not in log.read_text()
    log.write_text("")
    assert run(build, {"FAKE_FLOCK_FAIL": "1"}).returncode != 0
    assert " run " not in log.read_text()
print("fake-Docker contract: normal build, cleanup, missing seal, stale signing, ps failure, lock failure passed")
