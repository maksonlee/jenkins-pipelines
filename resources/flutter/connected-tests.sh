#!/bin/bash
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
host_flutter="${FLUTTER_CACHE_ROOT}/fvm/versions/${FLUTTER_VERSION}/bin/flutter"
if [ ! -x "${adb_bin}" ] || [ ! -x "${host_flutter}" ]; then
  echo "Connected-test tooling is unavailable on this Jenkins agent." >&2
  exit 1
fi

"${adb_bin}" start-server >/dev/null
mapfile -t device_serials < <("${adb_bin}" devices | awk 'NR > 1 && $2 == "device" { print $1 }')

# Optional: one device serial or ro.product.model value per line ('#' starts a
# comment). Excluded devices are skipped and do not count toward the minimum.
exclude_file="${project_base}/connected-device-exclude.txt"
excluded_devices=()
if [ -f "${exclude_file}" ]; then
  mapfile -t excluded_devices < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' "${exclude_file}" | grep -v '^$')
fi
if [ "${#excluded_devices[@]}" -gt 0 ]; then
  selected_serials=()
  for serial in "${device_serials[@]}"; do
    model=$("${adb_bin}" -s "${serial}" shell getprop ro.product.model 2>/dev/null | tr -d '[:space:]' || true)
    excluded=false
    for entry in "${excluded_devices[@]}"; do
      if [ "${entry}" = "${serial}" ] || [ "${entry}" = "${model}" ]; then
        excluded=true
      fi
    done
    if [ "${excluded}" = true ]; then
      echo "Skipping excluded Android device ${serial} (${model:-unknown model})"
    else
      selected_serials+=("${serial}")
    fi
  done
  device_serials=("${selected_serials[@]}")
fi
echo "CONNECTED_ANDROID_DEVICES=${#device_serials[@]}, minimum=${minimum}"
if [ "${#device_serials[@]}" -lt "${minimum}" ]; then
  echo "Not enough authorized Android devices are connected." >&2
  "${adb_bin}" devices -l
  exit 1
fi

export ANDROID_HOME="${android_sdk}"
export ANDROID_SDK_ROOT="${android_sdk}"
export FVM_CACHE_PATH="${FLUTTER_CACHE_ROOT}/fvm"
export PUB_CACHE="${FLUTTER_CACHE_ROOT}/pub-cache"
export GRADLE_USER_HOME="${ANDROID_CACHE_ROOT}/gradle"
export PATH="${android_sdk}/platform-tools:${PATH}"

# The container build leaves absolute /workspace and /home/ubuntu paths in
# generated Flutter and Gradle metadata. Run physical-device tests from a clean
# archive of the same commit so the host build cannot reuse container paths or
# copy generated signing material.
connected_checkout=$(mktemp -d "${workspace_root}/connected-tests.XXXXXX")
trap 'rm -rf -- "${connected_checkout}"' EXIT
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
  if [ "${#excluded_devices[@]}" -gt 0 ]; then
    # AGP runs connected tests only on the devices in a comma-separated ANDROID_SERIAL.
    ANDROID_SERIAL=$(IFS=,; echo "${device_serials[*]}")
    export ANDROID_SERIAL
  fi
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
rm -rf -- "${connected_checkout}"
trap - EXIT
