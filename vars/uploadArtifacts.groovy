def call(Map config = [:]) {
    def orgPath = config.orgPath
    def module = config.module
    def baseRev = config.baseRev
    def folderItegRev = config.folderItegRev ?: "SNAPSHOT"
    def repo = config.repo
    def artifacts = config.artifacts

    def timestamp = new Date().format("yyyyMMdd.HHmmss", TimeZone.getTimeZone('UTC'))
    def fileItegRev = "${timestamp}-${env.BUILD_NUMBER}"
    def targetBasePath = "${repo}/${orgPath}/${module}/${baseRev}-${folderItegRev}"

    def specMap = [
            files: artifacts.collect { art ->
                [
                        pattern: art.pattern,
                        target: "${targetBasePath}/${module}-${baseRev}-${fileItegRev}-${art.classifier}.${art.ext}"
                ]
            }
    ]

    echo "Uploading artifacts to: ${targetBasePath}"

    rtUpload(
            serverId: "artifactory",
            spec: groovy.json.JsonOutput.toJson(specMap),
            failNoOp: true
    )
}