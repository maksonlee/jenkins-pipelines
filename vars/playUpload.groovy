// The one Google Play upload path of flutterRelease, androidRelease and android/veil-android/release. Call it only
// after the manual approval input: it is the only place those jobs read the Play service account.
//
// It writes the Play tools from resources/play into .ci/play, refuses a version code that is not above every bundle
// already uploaded (play_version_preflight.py) and then, in one edit, uploads the single AAB matching aabGlob, sets
// the track release with the release notes and status, and commits (play_upload.py).
//   aabGlob, packageName, versionName, versionCode, track, releaseStatus, expectedSha256   required
//   rolloutFraction    only with releaseStatus inProgress
//   releaseNotesDir    build .play-release-notes.json from <dir>/<language>/<track>.txt or default.txt; without it
//                      the caller has already written that file
//   metadataRoot       a src/main/play directory whose store listing goes into the same edit; empty sends none
// Every value is passed explicitly: a job's first build has no parameter environment variables.

def call(Map cfg) {
    ['aabGlob', 'packageName', 'versionName', 'versionCode', 'track', 'releaseStatus', 'expectedSha256'].each { name ->
        if (!cfg[name]) {
            error "playUpload: '${name}' is required"
        }
    }
    ['play_api.py', 'play_version_preflight.py', 'play_upload.py', 'release_notes.py'].each { name ->
        writeFile file: ".ci/play/${name}", text: libraryResource("play/${name}")
    }
    withEnv([
        "PLAY_TOOLS=${pwd()}/.ci/play",
        "PLAY_AAB_GLOB=${cfg.aabGlob}",
        "PLAY_PACKAGE_NAME=${cfg.packageName}",
        "PLAY_VERSION_NAME=${cfg.versionName}",
        "PLAY_VERSION_CODE=${cfg.versionCode}",
        "PLAY_TRACK=${cfg.track}",
        "PLAY_RELEASE_STATUS=${cfg.releaseStatus}",
        "PLAY_ROLLOUT_FRACTION=${cfg.rolloutFraction ?: ''}",
        "EXPECTED_AAB_SHA256=${cfg.expectedSha256}",
        "RELEASE_NOTES_DIR=${cfg.releaseNotesDir ?: ''}",
        "PLAY_METADATA_ROOT=${cfg.metadataRoot ?: ''}"
    ]) {
        if (cfg.releaseNotesDir) {
            sh '''#!/bin/bash
set -euo pipefail
python3 -I "$PLAY_TOOLS/release_notes.py" "$RELEASE_NOTES_DIR" "$PLAY_TRACK" > .play-release-notes.json
'''
        }
        withVault([vaultSecrets: [[
            path: 'secret/jenkins/mobile/shared/play-service',
            engineVersion: 2,
            secretValues: [[envVar: 'PLAY_SERVICE_JSON_B64', vaultKey: 'json_b64']]
        ]]]) {
            sh libraryResource('play/play-preflight.sh')
            sh libraryResource('play/play-upload.sh')
        }
    }
}
