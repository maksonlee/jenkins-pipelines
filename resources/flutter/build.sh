#!/bin/bash
set -euo pipefail
uid=$(id -u)
gid=$(id -g)
flutter_cache_root="${FLUTTER_CACHE_ROOT}"
android_cache_root="${ANDROID_CACHE_ROOT}"
android_cache="${android_cache_root}/flutter-android-sdk"
fvm_cache="${flutter_cache_root}/fvm"
gradle_cache="${android_cache_root}/gradle"
pub_cache="${flutter_cache_root}/pub-cache"
mkdir -p "${android_cache}" "${gradle_cache}" "${pub_cache}" "${fvm_cache}"

if [ ! -d "${android_cache}/cmdline-tools/latest" ]; then
  echo "Seeding Android command-line tools into ${android_cache}"
  seed_container=$(docker create "${FLUTTER_IMAGE}")
  trap 'docker rm -f "${seed_container}" >/dev/null 2>&1 || true' EXIT
  docker cp "${seed_container}:/home/ubuntu/Android/cmdline-tools" "${android_cache}/"
  docker rm "${seed_container}" >/dev/null
  trap - EXIT
fi

mkdir -p \
  "${android_cache}/platforms" \
  "${android_cache}/build-tools" \
  "${android_cache}/platform-tools" \
  "${android_cache}/ndk" \
  "${android_cache}/cmake" \
  "${android_cache}/licenses"

echo "FLUTTER_CACHE_ROOT=${flutter_cache_root}"
echo "ANDROID_CACHE_ROOT=${android_cache_root}"
echo "FLUTTER_CACHE_SIZE_BEFORE=$(du -sh "${flutter_cache_root}" 2>/dev/null | cut -f1)"
echo "ANDROID_CACHE_SIZE_BEFORE=$(du -sh "${android_cache_root}" 2>/dev/null | cut -f1)"
start=$(date +%s)

docker run --rm \
  ${BUILD_CONTAINER_RESOURCE_ARGS} \
  -u ${uid}:${gid} \
  -e HOME=/home/ubuntu \
  -e ANDROID_HOME=/home/ubuntu/Android \
  -e ANDROID_SDK_ROOT=/home/ubuntu/Android \
  -e FVM_CACHE_PATH=/home/ubuntu/fvm \
  -e PUB_CACHE=/home/ubuntu/pub-cache \
  -v "${android_cache}:/home/ubuntu/Android" \
  -v "${fvm_cache}:/home/ubuntu/fvm" \
  -v "${gradle_cache}:/home/ubuntu/.gradle" \
  -v "${pub_cache}:/home/ubuntu/pub-cache" \
  -v "$PWD/source:/workspace" \
  -w "/workspace/${PROJECT_WORKDIR}" \
  "${FLUTTER_IMAGE}" \
  bash -lc "yes | sdkmanager --licenses && fvm install ${FLUTTER_VERSION} && /home/ubuntu/fvm/versions/${FLUTTER_VERSION}/bin/flutter config --no-analytics && /home/ubuntu/fvm/versions/${FLUTTER_VERSION}/bin/flutter --version && ${FLUTTER_BUILD_COMMAND}"

coverage_file="source/${PROJECT_WORKDIR}/coverage/lcov.info"
coverage_minimum_file="source/${PROJECT_WORKDIR}/coverage-minimum.txt"
branch_coverage_minimum_file="source/${PROJECT_WORKDIR}/branch-coverage-minimum.txt"
if [ -f "${coverage_file}" ]; then
  minimum=0
  branch_minimum=0
  enforce_branch_minimum=false
  if [ -s "${coverage_minimum_file}" ]; then
    minimum=$(tr -d '[:space:]' < "${coverage_minimum_file}")
    if ! [[ "${minimum}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      echo "Invalid Flutter coverage minimum: ${minimum}" >&2
      exit 1
    fi
  fi
  if [ -s "${branch_coverage_minimum_file}" ]; then
    branch_minimum=$(tr -d '[:space:]' < "${branch_coverage_minimum_file}")
    if ! [[ "${branch_minimum}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      echo "Invalid Flutter branch coverage minimum: ${branch_minimum}" >&2
      exit 1
    fi
    enforce_branch_minimum=true
  fi
  awk -F: -v minimum="${minimum}" -v branch_minimum="${branch_minimum}" \
    -v enforce_branch_minimum="${enforce_branch_minimum}" '
    /^LF:/ { lines_found += $2 }
    /^LH:/ { lines_hit += $2 }
    /^BRDA:/ {
      branches_found++
      split($2, branch_fields, ",")
      if (branch_fields[4] != "-" && branch_fields[4] + 0 > 0) branches_hit++
    }
    END {
      percentage = lines_found ? (100 * lines_hit / lines_found) : 0
      printf "FLUTTER_LINE_COVERAGE=%.2f%% (%d/%d), minimum=%.2f%%\n",
        percentage, lines_hit, lines_found, minimum
      failed = !lines_found || percentage + 0.00001 < minimum

      if (branches_found || enforce_branch_minimum == "true") {
        branch_percentage = branches_found ? (100 * branches_hit / branches_found) : 0
        printf "FLUTTER_BRANCH_COVERAGE=%.2f%% (%d/%d), minimum=%.2f%%\n",
          branch_percentage, branches_hit, branches_found, branch_minimum
        if (!branches_found || branch_percentage + 0.00001 < branch_minimum) failed = 1
      }

      if (failed) exit 1
    }
  ' "${coverage_file}"
fi

end=$(date +%s)
echo "TOTAL_SECONDS=$((end-start))"
echo "FLUTTER_CACHE_SIZE_AFTER=$(du -sh "${flutter_cache_root}" 2>/dev/null | cut -f1)"
echo "ANDROID_CACHE_SIZE_AFTER=$(du -sh "${android_cache_root}" 2>/dev/null | cut -f1)"
for d in "${flutter_cache_root}"/* "${android_cache_root}"/*; do
  if [ -d "$d" ]; then du -sh "$d"; fi
done | sort -h || true
ls -lh ${FLUTTER_ARTIFACTS}
