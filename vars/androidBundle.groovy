// Verification of a signed release AAB on the agent, shared by androidRelease and the release jobs with their own
// Jenkinsfile (veil-android, termstay). The pinned bundletool lives in the agent's Android cache.

def bundletoolJar() {
    return '/home/administrator/jenkins/android-cache/tools/bundletool-all-1.18.3.jar'
}

def bundletoolSha256() {
    return 'a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29'
}

// Run resources/android/release/verify-aab.sh on cfg.aab, which must be the only bundle in its directory, and
// return [versionCode, versionName, signerSha256, sha256]. The script leaves <aab>.sha256, release-manifest.txt,
// signature-verification.txt and bundletool-validate.txt next to the bundle.
//   packageName            the manifest package the bundle must have
//   forbiddenEntry         optional extended regex no bundle entry may match
//   certificateVaultPath   optional: the signer must equal certificate_sha256 at this Vault path; without it the
//                          signer is only recorded
def verify(Map cfg) {
    def aab = cfg.aab as String
    def settings = [
        "RELEASE_AAB=${aab}",
        "RELEASE_PACKAGE=${cfg.packageName}",
        "BUNDLETOOL_JAR=${bundletoolJar()}",
        "BUNDLETOOL_SHA256=${bundletoolSha256()}",
        "RELEASE_FORBIDDEN_AAB_ENTRY=${cfg.forbiddenEntry ?: ''}",
        "RELEASE_VERIFY_SIGNER=${cfg.certificateVaultPath ? 'true' : 'false'}"
    ]
    if (cfg.certificateVaultPath) {
        withVault([vaultSecrets: [[
            path: cfg.certificateVaultPath,
            engineVersion: 2,
            secretValues: [[envVar: 'EXPECTED_UPLOAD_CERT_SHA256', vaultKey: 'certificate_sha256']]
        ]]]) {
            withEnv(settings) {
                sh libraryResource('android/release/verify-aab.sh')
            }
        }
    } else {
        withEnv(settings) {
            sh libraryResource('android/release/verify-aab.sh')
        }
    }

    def releaseDir = aab.contains('/') ? aab.substring(0, aab.lastIndexOf('/')) : '.'
    def manifest = [:]
    readFile("${releaseDir}/release-manifest.txt").readLines().each { line ->
        def separator = line.indexOf('=')
        if (separator > 0) {
            manifest[line.substring(0, separator)] = line.substring(separator + 1)
        }
    }
    def result = [
        versionCode : manifest.versionCode,
        versionName : manifest.versionName,
        signerSha256: manifest.signerSha256,
        sha256      : readFile("${aab}.sha256").trim().tokenize()[0]
    ]
    if (!(result.versionCode ==~ /^[1-9][0-9]{0,9}$/) || !result.versionName ||
            !(result.signerSha256 ==~ /^[0-9A-F]{64}$/) || !(result.sha256 ==~ /^[0-9a-f]{64}$/)) {
        error 'androidBundle.verify: unexpected release identifiers'
    }
    return result
}
