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
    def userExtraArgs = (cfg.get('extraArgs', []) as List)
    def maxWorkers = (cfg.get('maxWorkers', 2)) as int
    def defaultArgs = ['--no-daemon']
    if (!userExtraArgs.any { it.toString().startsWith('--max-workers') }) {
        defaultArgs << "--max-workers=${maxWorkers}"
    }
    def extraArgs = (defaultArgs + userExtraArgs).unique()
    def workDir = (cfg.get('workDir', '')) as String    // e.g. 'android' if gradlew在子目錄
    def lockBuild = (cfg.get('lockBuild', true)) as boolean
    def lockPath = (cfg.get('lockPath', '/home/ubuntu/.gradle/android-release-build.lock')) as String

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
        def lockPrefix = lockBuild ? """lock_file='${lockPath}'
mkdir -p "\$(dirname "\$lock_file")"
exec 9>"\$lock_file"
echo "Waiting for Android release Gradle lock: \$lock_file"
flock 9
echo "Acquired Android release Gradle lock"
""" : ''

        sh """#!/bin/bash
set -euo pipefail
${cdPrefix}${lockPrefix}${gradleCmd} ${task} \\
  ${args.join(' ')}
"""
    }
}
