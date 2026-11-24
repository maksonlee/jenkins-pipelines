#!/bin/bash
# Install the CI-built app and instrumentation APKs on a disposable emulator with a private ADB server and
# run the project's device runner. Exit 3 means skipped (missing APKs, runner, SDK image or KVM).
set -euo pipefail
shopt -s nullglob
evidence="$PWD/evidence"
repo="${NATIVE_ANDROID_REPO:?NATIVE_ANDROID_REPO is required}"
runner="$PWD/source/${NATIVE_ANDROID_DEVICE_RUNNER:?NATIVE_ANDROID_DEVICE_RUNNER is required}"
app_apks=("$evidence"/apk/debug/*.apk)
test_apks=("$evidence"/apk/androidTest/*.apk)
mkdir -p "$evidence/device"
if (( ${#app_apks[@]} != 1 || ${#test_apks[@]} != 1 )); then
    echo 'Exactly one app APK and one instrumentation APK are required.' | tee "$evidence/device/preflight.log"
    exit 3
fi
if [[ ! -f "$runner" ]]; then
    echo 'The verified source commit does not contain the device runner.' | tee "$evidence/device/preflight.log"
    exit 3
fi

run_device() (
    (cd "$evidence" && sha256sum --check SHA256SUMS)
    export ANDROID_HOME=/home/administrator/jenkins/android-cache/flutter-android-sdk
    export ANDROID_SDK_ROOT="$ANDROID_HOME"
    adb="$ANDROID_HOME/platform-tools/adb"
    avdmanager="$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager"
    emulator="$ANDROID_HOME/emulator/emulator"
    image='system-images;android-34;default;x86_64'
    image_dir="$ANDROID_HOME/system-images/android-34/default/x86_64"
    if [[ ! -x "$adb" || ! -x "$avdmanager" || ! -x "$emulator" || ! -d "$image_dir" || ! -w /dev/kvm ]]; then
        echo 'The worker needs the Android 34 AOSP image, SDK host tools and write access to KVM.' >&2
        return 3
    fi

    emulator_pid=''
    adb_pid=''
    home=$(mktemp -d "$PWD/native-android-emulator.XXXXXX")
    export ANDROID_USER_HOME="$home/android"
    export ANDROID_AVD_HOME="$home/avd"
    export HOME="$home"
    mkdir -p "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME"
    avd="${repo}-${BUILD_NUMBER}"

    cleanup() {
        local exit_status=$?
        trap - EXIT INT TERM
        if [[ -n "$emulator_pid" ]]; then
            kill -TERM "$emulator_pid" 2>/dev/null || true
            for ((attempt = 0; attempt < 10; attempt++)); do
                kill -0 "$emulator_pid" 2>/dev/null || break
                sleep 1
            done
            kill -KILL "$emulator_pid" 2>/dev/null || true
            wait "$emulator_pid" 2>/dev/null || true
        fi
        if [[ -n "$adb_pid" ]]; then
            kill -TERM "$adb_pid" 2>/dev/null || true
            wait "$adb_pid" 2>/dev/null || true
        fi
        rm -rf -- "$home"
        exit "$exit_status"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    ports=$(python3 - <<'PY'
import socket

with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    for port in range(5584, 5682, 2):
        with socket.socket() as console, socket.socket() as transport:
            try:
                console.bind(('127.0.0.1', port))
                transport.bind(('127.0.0.1', port + 1))
            except OSError:
                continue
            print(port, server.getsockname()[1])
            break
    else:
        raise SystemExit('No free Android emulator port pair')
PY
)
    read -r console_port server_port <<< "$ports"
    serial="emulator-$console_port"
    export ADB_SERVER_SOCKET="tcp:127.0.0.1:$server_port"
    export ANDROID_ADB_SERVER_PORT="$server_port"
    export ADB_MDNS_AUTO_CONNECT=0
    unset ADB_VENDOR_KEYS ANDROID_SERIAL
    env -u ADB_SERVER_SOCKET -u ANDROID_ADB_SERVER_PORT "$adb" -L "tcp:localhost:$server_port" \
        --one-device "${repo}-ci-no-usb" server nodaemon >"$evidence/device/adb-server.log" 2>&1 &
    adb_pid=$!
    printf 'no\n' | "$avdmanager" create avd --name "$avd" --package "$image" --device pixel_2
    if ! kill -0 "$adb_pid" 2>/dev/null || ! timeout 5 "$adb" devices >/dev/null; then
        echo 'The private ADB server could not start.' >&2
        return 1
    fi
    "$emulator" -avd "$avd" -port "$console_port" -no-window -no-audio -no-boot-anim -no-metrics \
        -no-snapshot -gpu swiftshader_indirect -accel on \
        > >(sed -u -e '/Sending adb public key/d' -e '/adb\.pubkey/d' >"$evidence/device/emulator.log") 2>&1 &
    emulator_pid=$!

    for ((attempt = 0; attempt < 90; attempt++)); do
        if ! kill -0 "$emulator_pid" 2>/dev/null; then
            echo 'The ephemeral emulator exited before boot completed.' >&2
            return 1
        fi
        if [[ "$(timeout 3 "$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)" == 1 ]]; then
            python3 "$runner" --adb "$adb" --app-apk "${app_apks[0]}" --test-apk "${test_apks[0]}" \
                --out "$evidence/device/results" --serial "$serial" --avd "$avd"
            return
        fi
        sleep 2
    done
    echo 'The ephemeral emulator did not complete boot.' >&2
    return 1
)

run_device 2>&1 | tee "$evidence/device/preflight.log"
