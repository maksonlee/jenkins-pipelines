def call(String task, Map cfg = [:]) {
    if (!task?.trim()) error "gradleRelease: 'task' is required"
    if (!cfg.keystoreVaultPath) error "gradleRelease: 'keystoreVaultPath' is required"

    def image = (cfg.image ?: 'cdlee/android-build-env:latest') as String
    def insideArgs = (cfg.insideArgs ?: '') as String
    def jksPath = (cfg.jksPath ?: '/tmp/upload.jks') as String
    def playJsonPath = (cfg.playJsonPath ?: '/tmp/play-service.json') as String
    def track = (cfg.track ?: null) as String
    def gradleCmd = (cfg.gradleCmd ?: './gradlew') as String
    def stacktrace = (cfg.get('stacktrace', true)) as boolean
    def extraProps = (cfg.get('extraProps', [:])) as Map   // e.g. [android.injected.signing.store.type:'jks']
    def extraArgs = (cfg.get('extraArgs', [])) as List    // e.g. ['--no-daemon']
    def workDir = (cfg.get('workDir', '')) as String    // e.g. 'android' if gradlew在子目錄

    withAndroidReleaseEnv(
            image: image,
            insideArgs: insideArgs,
            keystoreVaultPath: cfg.keystoreVaultPath,
            playServiceVaultPath: cfg.get('playServiceVaultPath', null),
            jksPath: jksPath,
            playJsonPath: playJsonPath,
            extraVaults: cfg.get('extraVaults', [])
    ) { envs ->
        def args = []
        if (envs.hasPlay) {
            args << "-Pplay.serviceAccountCredentials=${envs.playJsonPath}"
            if (track) args << "-Ptrack=${track}"
        }
        // signing props
        args += [
                "-Psigning.storeFile=${envs.jksPath}",
                '-Psigning.storePassword="$STORE_PASSWORD"',
                '-Psigning.keyAlias="$KEY_ALIAS"',
                '-Psigning.keyPassword="$KEY_PASSWORD"'
        ]
        // extra -P props
        extraProps.each { k, v -> args << "-P${k}=${v}" }
        // extra gradle args
        args.addAll(extraArgs)
        if (stacktrace) args << "--stacktrace"

        def cdPrefix = workDir?.trim() ? "cd ${workDir}\n" : ""

        sh """#!/bin/bash
set -euo pipefail
${cdPrefix}${gradleCmd} ${task} \\
  ${args.join(' ')}
"""
    }
}
