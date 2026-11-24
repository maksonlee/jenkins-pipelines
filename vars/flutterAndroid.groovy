// Stage implementations shared by flutterCi and flutterRelease. Each Flutter project's contract (layouts, .fvmrc,
// optional hooks, coverage and connected-device files, Play metadata) is described in docs/flutter.md.

def config(Map cfg, String step) {
    def backend = cfg.backendTests as Map
    def config = [
        repo                 : cfg.repo as String,
        branch               : (cfg.branch ?: 'main') as String,
        // Fallback for projects without an .fvmrc pin; a project's .fvmrc takes precedence.
        defaultFlutterVersion: (cfg.defaultFlutterVersion ?: '3.47.8') as String,
        backendTests         : backend ? [
            coverageFloor   : (backend.coverageFloor ?: 0).toString(),
            postgresDatabase: (backend.postgresDatabase ?: '') as String
        ] : null,
        tracks               : (cfg.tracks ?: ['internal', 'alpha', 'beta', 'production']) as List
    ]
    if (!(config.repo ==~ /^[A-Za-z0-9._-]+$/)) {
        error "${step}: invalid repo '${config.repo}'"
    }
    if (!(config.branch ==~ /^[A-Za-z0-9._\/-]+$/) || config.branch.contains('..')) {
        error "${step}: invalid branch '${config.branch}'"
    }
    if (!(config.defaultFlutterVersion ==~ /^[0-9]+\.[0-9]+\.[0-9]+$/)) {
        error "${step}: invalid defaultFlutterVersion '${config.defaultFlutterVersion}'"
    }
    if (config.backendTests) {
        if (!(config.backendTests.coverageFloor ==~ /^[0-9]+(\.[0-9]+)?$/)) {
            error "${step}: invalid backendTests.coverageFloor '${config.backendTests.coverageFloor}'"
        }
        if (config.backendTests.postgresDatabase && !(config.backendTests.postgresDatabase ==~ /^[a-z_][a-z0-9_]*$/)) {
            error "${step}: invalid backendTests.postgresDatabase '${config.backendTests.postgresDatabase}'"
        }
    }
    config.tracks.each { track ->
        if (!(track in ['internal', 'alpha', 'beta', 'production'])) {
            error "${step}: unsupported track '${track}'"
        }
    }
    return config
}

def setEnvironment(Map config) {
    env.PROJECT_REPO = "git@github.com:maksonlee/${config.repo}.git"
    env.PROJECT_BRANCH = config.branch
    env.DEFAULT_FLUTTER_VERSION = config.defaultFlutterVersion
    // This image contains Android, Dart, and FVM. The project Flutter SDK is installed into the mounted FVM cache.
    env.FLUTTER_IMAGE = buildImages.flutter()
    env.BUILD_CONTAINER_RESOURCE_ARGS = buildContainer.resourceArgs().join(' ')
    env.PYTHON_TEST_IMAGE = buildImages.python()
    env.POSTGIS_TEST_IMAGE = buildImages.postgis()
    env.FLUTTER_CACHE_ROOT = '/home/administrator/jenkins/flutter-cache'
    env.ANDROID_CACHE_ROOT = '/home/administrator/jenkins/android-cache'
    env.ARTIFACTORY_REPO = 'android-snapshots'
    env.ARTIFACTORY_ORG_PATH = 'com/maksonlee'
    env.ARTIFACTORY_MODULE = config.repo
    env.PLAY_SERVICE_VAULT_PATH = 'secret/jenkins/mobile/shared/play-service'
    env.FLUTTER_CI_TOOLS = "${pwd()}/.ci/flutter"
}

def fetchImage() {
    sh libraryResource('flutter/fetch-image.sh')
}

def projectBase() {
    return env.PROJECT_WORKDIR == '.' ? 'source' : "source/${env.PROJECT_WORKDIR}"
}

// Resolve the Flutter layout, the Flutter version from .fvmrc and the Android applicationId of the checkout.
def resolveProject() {
    def flutterProject = resolveFlutterWorkdir(sourceDir: 'source')
    env.PROJECT_WORKDIR = flutterProject.workDir
    env.PROJECT_VERSION_FILE = flutterProject.versionFile
    def fvmConfigFile = projectBase() + '/.fvmrc'
    env.FLUTTER_VERSION = env.DEFAULT_FLUTTER_VERSION
    if (fileExists(fvmConfigFile)) {
        def fvmConfig = readFile(file: fvmConfigFile)
        def flutterKey = '"flutter"'
        def keyOffset = fvmConfig.indexOf(flutterKey)
        def colonOffset = keyOffset < 0 ? -1 : fvmConfig.indexOf(':', keyOffset + flutterKey.length())
        def valueStart = colonOffset < 0 ? -1 : fvmConfig.indexOf('"', colonOffset + 1)
        def valueEnd = valueStart < 0 ? -1 : fvmConfig.indexOf('"', valueStart + 1)
        if (valueStart < 0 || valueEnd <= valueStart + 1) {
            error "Invalid FVM configuration: ${fvmConfigFile}"
        }
        env.FLUTTER_VERSION = fvmConfig.substring(valueStart + 1, valueEnd).trim()
    }
    if (!(env.FLUTTER_VERSION ==~ /^[0-9]+\.[0-9]+\.[0-9]+$/)) {
        error "Invalid Flutter version: ${env.FLUTTER_VERSION}"
    }
    echo "Flutter workdir: ${env.PROJECT_WORKDIR}"
    echo "Version file: ${env.PROJECT_VERSION_FILE}"
    echo "Flutter version: ${env.FLUTTER_VERSION}"

    def androidApp = resolveAndroidApplicationId(sourceDir: 'source', workDir: env.PROJECT_WORKDIR)
    env.PLAY_PACKAGE_NAME = androidApp.playPackageName
    env.KEYSTORE_VAULT_PATH = androidApp.keystoreVaultPath
    env.PLAY_METADATA_ROOT = "${projectBase()}/android/app/src/main/play"
    echo "Android applicationId: ${env.PLAY_PACKAGE_NAME}"
    echo "applicationId file: ${androidApp.applicationIdFile}"
}

// Read version: <name>+<code> from pubspec.yaml into PLAY_VERSION_NAME and PLAY_VERSION_CODE.
def resolveVersion() {
    def versionFile = "source/${env.PROJECT_VERSION_FILE}"
    def versionLine = readFile(versionFile).readLines().collect { it.trim() }.find { it.startsWith('version:') }
    if (!versionLine) {
        error "Cannot find Flutter version in ${versionFile}"
    }
    def versionValue = versionLine.substring('version:'.length()).trim()
    def versionParts = versionValue.split('\\+', -1)
    if (versionParts.size() != 2 || !(versionParts[0] ==~ /^[0-9]+\.[0-9]+\.[0-9]+$/) ||
            !(versionParts[1] ==~ /^[0-9]+$/) || versionParts[1].toInteger() <= 0) {
        error "Flutter version must use semanticVersion+positiveVersionCode: ${versionValue}"
    }
    env.PLAY_VERSION_NAME = versionParts[0]
    env.PLAY_VERSION_CODE = versionParts[1]
    echo "Release version: ${env.PLAY_VERSION_NAME}+${env.PLAY_VERSION_CODE}"
}

// Load <language>/<track>.txt or <language>/default.txt for every release-notes language into
// .play-release-notes.json. Fails when no language has notes.
def loadReleaseNotes(String track) {
    if (fileExists("${env.PLAY_METADATA_ROOT}/listings")) {
        echo "PLAY_LISTINGS_ROOT=${env.PLAY_METADATA_ROOT}/listings"
    } else {
        echo "No Google Play listing metadata found at ${env.PLAY_METADATA_ROOT}/listings"
    }
    def releaseNotesRoot = "${env.PLAY_METADATA_ROOT}/release-notes"
    def releaseNotesByLanguage = [:]
    def releaseNoteSources = []
    if (fileExists(releaseNotesRoot)) {
        def languageOutput = ''
        withEnv(["RELEASE_NOTES_ROOT=${releaseNotesRoot}"]) {
            languageOutput = sh(returnStdout: true, script: '''#!/bin/bash
set -euo pipefail
find "${RELEASE_NOTES_ROOT}" -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' | sort
''').trim()
        }
        def languages = languageOutput ? languageOutput.readLines() : []
        languages.each { language ->
            if (!(language ==~ /^[a-z]{2,3}(?:-[A-Z][A-Za-z0-9]{1,7})?$/)) {
                error "Invalid Google Play release note language: ${language}"
            }
            def releaseNotesPath = ["${releaseNotesRoot}/${language}/${track}.txt", "${releaseNotesRoot}/${language}/default.txt"]
                .find { fileExists(it) }
            if (releaseNotesPath) {
                def releaseNotes = readFile(releaseNotesPath).trim()
                if (!releaseNotes) {
                    error "Google Play release notes are empty: ${releaseNotesPath}"
                }
                if (releaseNotes.length() > 500) {
                    error "Google Play release notes exceed 500 characters for ${language}: ${releaseNotes.length()}"
                }
                releaseNotesByLanguage[language] = releaseNotes
                releaseNoteSources.add(releaseNotesPath)
            }
        }
    }
    if (!releaseNotesByLanguage) {
        error "Missing Google Play release notes metadata in ${releaseNotesRoot}"
    }
    def previewLanguage = releaseNotesByLanguage.containsKey('en-US') ? 'en-US' : releaseNotesByLanguage.keySet().toList().sort()[0]
    env.PLAY_RELEASE_NOTES = releaseNotesByLanguage[previewLanguage]
    writeFile(file: '.play-release-notes.json',
        text: groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(releaseNotesByLanguage)) + '\n')
    echo "PLAY_RELEASE_NOTES_LANGUAGES=${releaseNotesByLanguage.keySet().toList().sort().join(',')}"
    echo "PLAY_RELEASE_NOTES_SOURCES=${releaseNoteSources.join(',')}"
}

// Repository-root Python tests only when the project configures backendTests; otherwise only the optional
// scripts/validate_play_metadata.py hook.
def backendTests(Map config) {
    if (config.backendTests) {
        withEnv([
            "BACKEND_COVERAGE_FLOOR=${config.backendTests.coverageFloor}",
            "BACKEND_POSTGRES_DB=${config.backendTests.postgresDatabase}"
        ]) {
            sh libraryResource('flutter/backend-tests.sh')
        }
    } else if (fileExists('source/scripts/validate_play_metadata.py')) {
        sh libraryResource('flutter/validate-play-metadata.sh')
    } else {
        echo 'No backend tests configured and no Play metadata validator found; skipping backend checks.'
    }
}

def prepareSigning() {
    withVault([vaultSecrets: [[
        path: env.KEYSTORE_VAULT_PATH,
        engineVersion: 2,
        secretValues: [
            [envVar: 'UPLOAD_JKS_B64', vaultKey: 'upload_jks_b64'],
            [envVar: 'STORE_PASSWORD', vaultKey: 'store_password'],
            [envVar: 'KEY_PASSWORD', vaultKey: 'key_password'],
            [envVar: 'KEY_ALIAS', vaultKey: 'key_alias']
        ]
    ]]]) {
        sh libraryResource('flutter/prepare-signing.sh')
    }
}

// Quality gates (pub get, analyze, tests with coverage, Android lint and JVM unit tests) and the target build.
def build(String target) {
    def flutterBin = "/home/ubuntu/fvm/versions/${env.FLUTTER_VERSION}/bin/flutter"
    def base = projectBase()
    def qualityCommands = ["${flutterBin} pub get", "${flutterBin} analyze --no-pub"]
    if (fileExists("${base}/test")) {
        qualityCommands.add("${flutterBin} test --coverage --branch-coverage --no-pub")
    } else {
        echo "No Flutter tests found at ${base}/test"
    }
    if (fileExists("${base}/android/app")) {
        qualityCommands.add('./android/gradlew -p android :app:lintDebug --no-daemon')
    } else {
        echo "No Android app found at ${base}/android/app"
    }
    if (fileExists("${base}/android/app/src/test")) {
        qualityCommands.add('./android/gradlew -p android :app:testDebugUnitTest --no-daemon')
    } else {
        echo "No Android app unit tests found at ${base}/android/app/src/test"
    }
    def commandByTarget = [
        'appbundle-debug': "${flutterBin} build appbundle --debug --no-pub --no-android-gradle-daemon",
        // Release builds must regenerate mode-specific platform tooling after debug tests. With --no-pub,
        // Flutter can leave dev-only plugins in GeneratedPluginRegistrant while excluding them from release
        // dependencies.
        'appbundle-release': "${flutterBin} build appbundle --release --no-android-gradle-daemon"
    ]
    def artifactByTarget = [
        'appbundle-debug': "${base}/build/app/outputs/bundle/debug/*.aab",
        'appbundle-release': "${base}/build/app/outputs/bundle/release/*.aab"
    ]
    if (!commandByTarget.containsKey(target)) {
        error "Unknown Flutter build target: ${target}"
    }
    env.FLUTTER_BUILD_COMMAND = (qualityCommands + [commandByTarget[target]]).join(' && ')
    env.FLUTTER_ARTIFACTS = artifactByTarget[target]
    sh libraryResource('flutter/build.sh')
    archiveArtifacts(artifacts: "${base}/coverage/lcov.info", allowEmptyArchive: true, fingerprint: false)
    junit(testResults: "${base}/build/app/test-results/testDebugUnitTest/TEST-*.xml", allowEmptyResults: true)
}

def hasConnectedTests() {
    return fileExists("${projectBase()}/connected-device-minimum.txt")
}

def connectedTests() {
    sh libraryResource('flutter/connected-tests.sh')
    junit(testResults: "${projectBase()}/build/app/outputs/androidTest-results/connected/**/*.xml", allowEmptyResults: true)
}

def hasReleaseArtifactChecks() {
    return fileExists('source/scripts/check_android_release_artifact.py')
}

def releaseArtifactChecks() {
    env.RELEASE_ARTIFACT_REPORT = "${projectBase()}/build/release-artifact-checks.json"
    try {
        sh libraryResource('flutter/release-artifact-checks.sh')
    } finally {
        archiveArtifacts artifacts: env.RELEASE_ARTIFACT_REPORT, allowEmptyArchive: true, fingerprint: true
    }
}

def archiveAndUpload() {
    archiveArtifacts artifacts: env.FLUTTER_ARTIFACTS, fingerprint: true
    def baseRev = null
    readFile("source/${env.PROJECT_VERSION_FILE}").readLines().each { line ->
        def trimmed = line.trim()
        if (baseRev == null && trimmed.startsWith('version:')) {
            baseRev = trimmed.substring('version:'.length()).trim().split('\\+')[0]
        }
    }
    if (!baseRev) {
        error "Cannot find version in source/${env.PROJECT_VERSION_FILE}"
    }
    def timestamp = new Date().format('yyyyMMdd.HHmmss', TimeZone.getTimeZone('UTC'))
    def targetBase = "${env.ARTIFACTORY_REPO}/${env.ARTIFACTORY_ORG_PATH}/${env.ARTIFACTORY_MODULE}/${baseRev}-SNAPSHOT"
    def targetFile = "${env.ARTIFACTORY_MODULE}-${baseRev}-${timestamp}-${env.BUILD_NUMBER}.aab"
    def spec = groovy.json.JsonOutput.toJson([files: [[pattern: env.FLUTTER_ARTIFACTS, target: "${targetBase}/${targetFile}", flat: true]]])
    echo "Uploading artifacts to: ${targetBase}"
    echo "Artifactory file: ${targetFile}"
    rtUpload(serverId: 'artifactory', spec: spec, failNoOp: true)
}

// SHA-256 of the single built AAB, shown in the approval input and rechecked by the upload.
def artifactSha256() {
    def output = ''
    withEnv(["FLUTTER_ARTIFACTS=${env.FLUTTER_ARTIFACTS}"]) {
        output = sh(returnStdout: true, script: '''#!/bin/bash
set -euo pipefail
mapfile -t aab_files < <(compgen -G "${FLUTTER_ARTIFACTS}" || true)
if [ "${#aab_files[@]}" -ne 1 ]; then
  echo "Expected exactly one release AAB." >&2
  exit 1
fi
sha256sum -- "${aab_files[0]}" | cut -d' ' -f1
''').trim()
    }
    if (!(output ==~ /^[0-9a-f]{64}$/)) {
        error 'Could not compute the release AAB SHA-256'
    }
    return output
}

def writePlayTools() {
    ['play_api.py', 'play_version_preflight.py', 'play_upload.py'].each { name ->
        writeFile file: ".ci/flutter/${name}", text: libraryResource("flutter/${name}")
    }
}

def withPlayService(Closure body) {
    withVault([vaultSecrets: [[
        path: env.PLAY_SERVICE_VAULT_PATH,
        engineVersion: 2,
        secretValues: [[envVar: 'PLAY_SERVICE_JSON_B64', vaultKey: 'json_b64']]
    ]]]) {
        body()
    }
}

// Version preflight and upload, both after the approval input.
def playPublish() {
    writePlayTools()
    withPlayService {
        sh libraryResource('flutter/play-preflight.sh')
        sh libraryResource('flutter/play-upload.sh')
    }
}
