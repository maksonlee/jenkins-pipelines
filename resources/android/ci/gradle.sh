#!/bin/bash
# Run the project's debug CI Gradle tasks inside the build container. NATIVE_ANDROID_GRADLE_ARGS holds one
# argument per line. With NATIVE_ANDROID_TRACE=true the command, Gradle version, SDK platforms and the plain
# log are also kept in ../evidence.
set -euo pipefail
run_gradle() {
    if [[ ! -w "$ANDROID_USER_HOME" ]]; then
        echo 'The build container needs a writable Android user home' >&2
        return 2
    fi
    if [[ ! -r "$ANDROID_USER_HOME/debug.keystore" ]]; then
        echo 'The existing worker debug keystore is unreadable in the build container' >&2
        return 2
    fi
    local command=(./gradlew)
    local argument
    while IFS= read -r argument; do
        if [[ -n "$argument" ]]; then
            command+=("$argument")
        fi
    done <<< "${NATIVE_ANDROID_GRADLE_ARGS:?NATIVE_ANDROID_GRADLE_ARGS is required}"
    if [[ "${NATIVE_ANDROID_TRACE:-}" == true ]]; then
        printf '%s\n' "${command[*]}" > ../evidence/build-command.txt
        ./gradlew --version > ../evidence/gradle-version.txt 2>&1 || true
        for props in "${ANDROID_HOME:-}"/platforms/android-*/source.properties; do
            if [[ -f "$props" ]]; then
                printf '[%s]\n' "$(basename "$(dirname "$props")")"
                grep -E '^(Pkg.Revision|AndroidVersion.ApiLevel|AndroidVersion.ExtensionLevel)=' "$props" || true
            fi
        done > ../evidence/android-sdk.txt
    fi
    "${command[@]}"
}

if [[ "${NATIVE_ANDROID_TRACE:-}" == true ]]; then
    mkdir -p ../evidence
    run_gradle 2>&1 | tee ../evidence/gradle.log
else
    run_gradle
fi
