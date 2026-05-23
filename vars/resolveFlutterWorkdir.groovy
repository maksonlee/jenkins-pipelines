def call(Map cfg = [:]) {
    def sourceDir = (cfg.sourceDir ?: 'source') as String

    if (fileExists("${sourceDir}/pubspec.yaml")) {
        return [
                workDir    : '.',
                versionFile: 'pubspec.yaml',
                flutterRoot: sourceDir
        ]
    }

    if (fileExists("${sourceDir}/mobile/pubspec.yaml")) {
        return [
                workDir    : 'mobile',
                versionFile: 'mobile/pubspec.yaml',
                flutterRoot: "${sourceDir}/mobile"
        ]
    }

    error "Cannot find Flutter pubspec.yaml in ${sourceDir}/ or ${sourceDir}/mobile/"
}
