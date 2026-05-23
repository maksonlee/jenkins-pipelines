def call(Map cfg = [:]) {
    def flutterRoot = cfg.flutterRoot as String
    if (!flutterRoot) {
        def sourceDir = (cfg.sourceDir ?: 'source') as String
        def workDir = (cfg.workDir ?: '.') as String
        flutterRoot = workDir == '.' ? sourceDir : "${sourceDir}/${workDir}"
    }

    def candidates = [
            "${flutterRoot}/android/app/build.gradle",
            "${flutterRoot}/android/app/build.gradle.kts"
    ]
    def applicationId = null
    def applicationIdFile = null

    for (candidate in candidates) {
        if (fileExists(candidate)) {
            def gradleText = readFile(candidate)
            def matcher = gradleText =~ /(?m)^\s*applicationId\s*(?:=)?\s*["']([^"']+)["']/
            if (matcher.find()) {
                applicationId = matcher.group(1)
                applicationIdFile = candidate
                break
            }
        }
    }

    if (!applicationId) {
        error "Cannot find Android applicationId in ${candidates.join(', ')}"
    }

    return [
            applicationId     : applicationId,
            applicationIdFile : applicationIdFile,
            playPackageName   : applicationId,
            keystoreVaultPath : "secret/jenkins/mobile/app/${applicationId}"
    ]
}
