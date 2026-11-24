#!/bin/bash
# Copy the configured JUnit XML, debug lint reports, debug APKs and instrumentation APKs from source/ into
# evidence/ and write SHA256SUMS for every APK. A successful Gradle run without every configured output fails.
set -euo pipefail
shopt -s nullglob globstar
evidence="$PWD/evidence"
cd source
missing=0

copy_outputs() {
    local destination=$1
    shift
    local count=0
    local output
    for output in "$@"; do
        if [[ -f "$output" && ! -L "$output" ]]; then
            mkdir -p "$evidence/$destination"
            cp -- "$output" "$evidence/$destination/"
            count=$((count + 1))
        fi
    done
    if (( count == 0 )); then
        echo "No CI output found for $destination" >&2
        missing=1
    fi
}

module_evidence_directory() {
    if [[ "$NATIVE_ANDROID_FLAT_APP_EVIDENCE" == true ]]; then
        printf '%s' "$1"
    else
        printf '%s/%s' "$1" "$2"
    fi
}

read -r -a unit_test_tasks <<< "$NATIVE_ANDROID_UNIT_TEST_TASKS"
for unit_test_task in "${unit_test_tasks[@]}"; do
    IFS=: read -r module task <<< "$unit_test_task"
    copy_outputs "junit/$module" "$module/build/test-results/$task"/*.xml
done

read -r -a lint_modules <<< "$NATIVE_ANDROID_LINT_MODULES"
for module in "${lint_modules[@]}"; do
    destination=$(module_evidence_directory lint "$module")
    report_base="$module/build/reports/lint-results-debug"
    copy_outputs "$destination" "$report_base.xml"
    for report in "$report_base."{html,sarif,txt}; do
        if [[ -f "$report" && ! -L "$report" ]]; then
            mkdir -p "$evidence/$destination"
            cp -- "$report" "$evidence/$destination/"
        fi
    done
done

read -r -a debug_apk_modules <<< "$NATIVE_ANDROID_DEBUG_APK_MODULES"
for module in "${debug_apk_modules[@]}"; do
    copy_outputs "$(module_evidence_directory apk/debug "$module")" "$module/build/outputs/apk/debug"/*.apk
done

read -r -a android_test_modules <<< "$NATIVE_ANDROID_ANDROID_TEST_MODULES"
for module in "${android_test_modules[@]}"; do
    copy_outputs "$(module_evidence_directory apk/androidTest "$module")" "$module/build/outputs/apk/androidTest/debug"/*.apk
done

cd "$evidence"
apks=(apk/debug/**/*.apk apk/androidTest/**/*.apk)
if (( ${#apks[@]} )); then
    sha256sum -- "${apks[@]}" > SHA256SUMS
fi

if [[ "$NATIVE_ANDROID_GRADLE_EXIT" == 0 ]] && (( missing )); then
    echo 'Gradle succeeded without all required CI outputs' >&2
    exit 1
fi
