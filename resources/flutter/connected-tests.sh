#!/bin/bash
# Run every integration_test/*_test.dart and :app:connectedDebugAndroidTest on at least the number of Android
# devices in connected-device-minimum.txt.
# - Authorized physical devices are used first; connected-device-exclude.txt lists serials or ro.product.model
#   values to skip.
# - When fewer physical devices are usable (including none), this build starts disposable emulators to make up the
#   difference and removes them afterwards.
# - Emulators this build did not start, and excluded phones, are never used: ANDROID_SERIAL always names exactly
#   the selected devices.
# Tests run from a clean git archive of the commit with the host Flutter SDK and the host ADB server.
set -euo pipefail
project_base="source/${PROJECT_WORKDIR}"
minimum_file="${project_base}/connected-device-minimum.txt"
workspace_root=$(pwd)
source_project="${workspace_root}/${project_base}"
minimum=$(tr -d '[:space:]' < "${minimum_file}")
if ! [[ "${minimum}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid connected Android device minimum: ${minimum}" >&2
  exit 1
fi

android_sdk="${ANDROID_CACHE_ROOT}/flutter-android-sdk"
adb_bin="${android_sdk}/platform-tools/adb"
emulator_bin="${android_sdk}/emulator/emulator"
avdmanager_bin="${android_sdk}/cmdline-tools/latest/bin/avdmanager"
emulator_image='system-images;android-34;default;x86_64'
emulator_image_dir="${android_sdk}/system-images/android-34/default/x86_64"
kvm_device="${CONNECTED_KVM_DEVICE:-/dev/kvm}"
host_flutter="${FLUTTER_CACHE_ROOT}/fvm/versions/${FLUTTER_VERSION}/bin/flutter"
if [ ! -x "${adb_bin}" ] || [ ! -x "${host_flutter}" ]; then
  echo "Connected-test tooling is unavailable on this Jenkins agent." >&2
  exit 1
fi

emulator_home=""
emulator_pids=()
emulator_serials=()
connected_checkout=""
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  local serial pid
  for serial in "${emulator_serials[@]}"; do
    "${adb_bin}" -s "${serial}" emu kill >/dev/null 2>&1 || true
  done
  for pid in "${emulator_pids[@]}"; do
    kill -TERM "${pid}" 2>/dev/null || true
  done
  for pid in "${emulator_pids[@]}"; do
    for _ in $(seq 1 15); do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  if [ -n "${emulator_home}" ]; then
    rm -rf -- "${emulator_home}"
  fi
  if [ -n "${connected_checkout}" ]; then
    rm -rf -- "${connected_checkout}"
  fi
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${adb_bin}" start-server >/dev/null
mapfile -t attached_serials < <("${adb_bin}" devices | awk 'NR > 1 && $2 == "device" { print $1 }')

# Optional: one device serial or ro.product.model value per line ('#' starts a comment).
exclude_file="${project_base}/connected-device-exclude.txt"
excluded_devices=()
if [ -f "${exclude_file}" ]; then
  mapfile -t excluded_devices < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' "${exclude_file}" | grep -v '^$' || true)
fi
device_serials=()
for serial in "${attached_serials[@]}"; do
  if [[ "${serial}" == emulator-* ]]; then
    echo "Ignoring ${serial}: only emulators started by this build are used"
    continue
  fi
  model=""
  if [ "${#excluded_devices[@]}" -gt 0 ]; then
    model=$("${adb_bin}" -s "${serial}" shell getprop ro.product.model 2>/dev/null | tr -d '[:space:]' || true)
  fi
  excluded=false
  for entry in "${excluded_devices[@]}"; do
    if [ "${entry}" = "${serial}" ] || [ "${entry}" = "${model}" ]; then
      excluded=true
    fi
  done
  if [ "${excluded}" = true ]; then
    echo "Skipping excluded Android device ${serial} (${model:-unknown model})"
  else
    device_serials+=("${serial}")
  fi
done
physical_count=${#device_serials[@]}
missing=$((minimum - physical_count))
echo "CONNECTED_ANDROID_DEVICES=${physical_count} physical, minimum=${minimum}"

free_console_port() {
  python3 - "$@" <<'PY'
import socket
import sys

taken = {int(port) for port in sys.argv[1:]}
for port in range(5584, 5682, 2):
    if port in taken:
        continue
    with socket.socket() as console, socket.socket() as adb:
        try:
            console.bind(("127.0.0.1", port))
            adb.bind(("127.0.0.1", port + 1))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit("No free Android emulator port pair")
PY
}

if [ "${missing}" -gt 0 ]; then
  if [ ! -x "${emulator_bin}" ] || [ ! -x "${avdmanager_bin}" ] || [ ! -d "${emulator_image_dir}" ] ||
      [ ! -w "${kvm_device}" ]; then
    echo "Only ${physical_count} of ${minimum} Android devices are usable, and no emulator can be started" \
      "(it needs ${emulator_image}, the emulator, avdmanager and write access to ${kvm_device})." >&2
    "${adb_bin}" devices -l || true
    exit 1
  fi
  emulator_home=$(mktemp -d "${workspace_root}/connected-emulators.XXXXXX")
  mkdir -p "${emulator_home}/avd"
  ports=()
  for index in $(seq 1 "${missing}"); do
    port=$(free_console_port "${ports[@]}")
    ports+=("${port}")
    avd="ci-${BUILD_NUMBER:-0}-${index}"
    printf 'no\n' | ANDROID_AVD_HOME="${emulator_home}/avd" "${avdmanager_bin}" create avd \
      --name "${avd}" --package "${emulator_image}" --device pixel_2 >/dev/null
    ANDROID_AVD_HOME="${emulator_home}/avd" "${emulator_bin}" -avd "${avd}" -port "${port}" \
      -no-window -no-audio -no-boot-anim -no-metrics -no-snapshot -gpu swiftshader_indirect -accel on \
      > "${emulator_home}/${avd}.log" 2>&1 &
    emulator_pids+=("$!")
    emulator_serials+=("emulator-${port}")
    echo "Started disposable emulator emulator-${port} (${emulator_image})"
  done
  for index in "${!emulator_serials[@]}"; do
    serial=${emulator_serials[$index]}
    booted=false
    for _ in $(seq 1 150); do
      if ! kill -0 "${emulator_pids[$index]}" 2>/dev/null; then
        break
      fi
      if [ "$(timeout 3 "${adb_bin}" -s "${serial}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)" = 1 ]; then
        booted=true
        break
      fi
      sleep 2
    done
    if [ "${booted}" != true ]; then
      echo "Disposable emulator ${serial} did not complete boot." >&2
      tail -n 40 "${emulator_home}/ci-${BUILD_NUMBER:-0}-$((index + 1)).log" >&2 || true
      exit 1
    fi
    echo "Emulator ${serial} is ready"
  done
  device_serials+=("${emulator_serials[@]}")
fi
echo "CONNECTED_TEST_DEVICES=${device_serials[*]}"

export ANDROID_HOME="${android_sdk}"
export ANDROID_SDK_ROOT="${android_sdk}"
export FVM_CACHE_PATH="${FLUTTER_CACHE_ROOT}/fvm"
export PUB_CACHE="${FLUTTER_CACHE_ROOT}/pub-cache"
export GRADLE_USER_HOME="${ANDROID_CACHE_ROOT}/gradle"
export PATH="${android_sdk}/platform-tools:${PATH}"
# AGP runs connected tests only on the devices in a comma-separated ANDROID_SERIAL.
ANDROID_SERIAL=$(IFS=,; echo "${device_serials[*]}")
export ANDROID_SERIAL

# The container build leaves absolute /workspace and /home/ubuntu paths in
# generated Flutter and Gradle metadata. Run device tests from a clean
# archive of the same commit so the host build cannot reuse container paths or
# copy generated signing material.
connected_checkout=$(mktemp -d "${workspace_root}/connected-tests.XXXXXX")
if [ "${PROJECT_WORKDIR}" = "." ]; then
  git -C source archive HEAD | tar -C "${connected_checkout}" -xf -
  connected_project="${connected_checkout}"
else
  git -C source archive HEAD "${PROJECT_WORKDIR}" | tar -C "${connected_checkout}" -xf -
  connected_project="${connected_checkout}/${PROJECT_WORKDIR}"
fi
cd "${connected_project}"
"${host_flutter}" pub get

mapfile -t integration_tests < <(find integration_test -type f -name '*_test.dart' -print 2>/dev/null | sort)
if [ "${#integration_tests[@]}" -gt 0 ]; then
  for serial in "${device_serials[@]}"; do
    for test_file in "${integration_tests[@]}"; do
      echo "Running ${test_file} on ${serial}"
      "${host_flutter}" test "${test_file}" -d "${serial}" --no-pub
    done
  done
else
  echo "No Flutter integration tests found."
fi

if [ -d android/app/src/androidTest ]; then
  ./android/gradlew -p android :app:connectedDebugAndroidTest --no-daemon
else
  echo "No Android instrumentation tests found."
fi

connected_results="build/app/outputs/androidTest-results/connected"
if [ -d "${connected_results}" ]; then
  source_results="${source_project}/${connected_results}"
  mkdir -p "${source_results}"
  cp -a "${connected_results}/." "${source_results}/"
fi
cd "${workspace_root}"
