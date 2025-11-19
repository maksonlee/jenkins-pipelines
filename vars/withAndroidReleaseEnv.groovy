def call(Map cfg = [:], Closure body) {
    if (!cfg.keystoreVaultPath) {
        error "withAndroidReleaseEnv: 'keystoreVaultPath' is required"
    }

    def image = (cfg.image ?: 'cdlee/android-build-env:latest') as String
    def insideArgs = (cfg.insideArgs ?: '') as String
    def keystoreVP = cfg.keystoreVaultPath as String
    def playVP = (cfg.playServiceVaultPath ?: null) as String
    def jksPath = (cfg.jksPath ?: '/tmp/upload.jks') as String
    def playJsonPath = (cfg.playJsonPath ?: '/tmp/play-service.json') as String
    def extraVaults = (cfg.extraVaults ?: []) as List

    def vaults = [[
                          path: keystoreVP, engineVersion: 2,
                          secretValues: [
                                  [envVar: 'UPLOAD_JKS_B64', vaultKey: 'upload_jks_b64'],
                                  [envVar: 'STORE_PASSWORD', vaultKey: 'store_password'],
                                  [envVar: 'KEY_PASSWORD',   vaultKey: 'key_password'],
                                  [envVar: 'KEY_ALIAS',      vaultKey: 'key_alias'],
                          ]
                  ]]
    if (playVP) {
        vaults << [
                path: playVP, engineVersion: 2,
                secretValues: [[envVar: 'PLAY_SERVICE_JSON_B64', vaultKey: 'json_b64']]
        ]
    }
    vaults.addAll(extraVaults)

    docker.image(image).inside(insideArgs) {
        withVault([vaultSecrets: vaults]) {
            withEnv(["JKS_PATH=${jksPath}", "PLAY_JSON_PATH=${playJsonPath}"]) {
                sh '''#!/bin/bash
set -euo pipefail
umask 077
: "${UPLOAD_JKS_B64:?ERROR: UPLOAD_JKS_B64 is empty or unset}"
printf %s "$UPLOAD_JKS_B64" | sed 's/^data:[^,]*,//' | tr -d '\r\n ' | base64 -d > "$JKS_PATH"
chmod 600 "$JKS_PATH"
if [ -n "${PLAY_SERVICE_JSON_B64:-}" ]; then
  printf %s "$PLAY_SERVICE_JSON_B64" | sed 's/^data:[^,]*,//' | tr -d '\r\n ' | base64 -d > "$PLAY_JSON_PATH"
  chmod 600 "$PLAY_JSON_PATH"
fi
echo "JKS bytes: $(wc -c < "$JKS_PATH")"
[ -f "$PLAY_JSON_PATH" ] && echo "Play JSON bytes: $(wc -c < "$PLAY_JSON_PATH")" || true
'''
            }
            body([
                    jksPath: jksPath,
                    playJsonPath: playJsonPath,
                    hasPlay: (playVP != null)
            ])
        }
    }
}
