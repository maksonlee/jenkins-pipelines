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
        def plainArgs = []
        extraProps.each { k, v -> plainArgs << "-P${k}=${v}" }
        plainArgs.addAll(extraArgs)
        if (stacktrace) plainArgs << "--stacktrace"

        withEnv([
                'GRADLE_RELEASE_CMD=' + gradleCmd,
                'GRADLE_RELEASE_TASK=' + task,
                'GRADLE_RELEASE_WORK_DIR=' + (workDir ?: ''),
                'GRADLE_RELEASE_LOCK=' + (lockBuild ? 'true' : 'false'),
                'GRADLE_RELEASE_LOCK_PATH=' + lockPath,
                'GRADLE_RELEASE_HAS_PLAY=' + (envs.hasPlay ? 'true' : 'false'),
                'GRADLE_RELEASE_TRACK=' + (track ?: ''),
                'GRADLE_RELEASE_JKS_PATH=' + envs.jksPath,
                'GRADLE_RELEASE_PLAY_JSON_PATH=' + envs.playJsonPath,
                'GRADLE_RELEASE_ARGS=' + plainArgs.join('\n')
        ]) {
            sh '''#!/bin/bash
set -euo pipefail
if [ -n "${GRADLE_RELEASE_WORK_DIR:-}" ]; then
  cd "${GRADLE_RELEASE_WORK_DIR}"
fi

if [ "${GRADLE_RELEASE_LOCK:-}" = "true" ]; then
  lock_file="${GRADLE_RELEASE_LOCK_PATH}"
  mkdir -p "$(dirname "$lock_file")"
  exec 9>"$lock_file"
  echo "Waiting for Android release Gradle lock: $lock_file"
  flock 9
  echo "Acquired Android release Gradle lock"
fi

args=("${GRADLE_RELEASE_TASK}")
if [ "${GRADLE_RELEASE_HAS_PLAY:-}" = "true" ]; then
  args+=("-Pplay.serviceAccountCredentials=${GRADLE_RELEASE_PLAY_JSON_PATH}")
  if [ -n "${GRADLE_RELEASE_TRACK:-}" ]; then
    args+=("-Ptrack=${GRADLE_RELEASE_TRACK}")
  fi
fi

args+=(
  "-Psigning.storeFile=${GRADLE_RELEASE_JKS_PATH}"
  "-Psigning.storePassword=${STORE_PASSWORD}"
  "-Psigning.keyAlias=${KEY_ALIAS}"
  "-Psigning.keyPassword=${KEY_PASSWORD}"
)

while IFS= read -r extra_arg; do
  if [ -n "$extra_arg" ]; then
    args+=("$extra_arg")
  fi
done <<< "${GRADLE_RELEASE_ARGS:-}"

"${GRADLE_RELEASE_CMD}" "${args[@]}"
'''
        }
    }
}
