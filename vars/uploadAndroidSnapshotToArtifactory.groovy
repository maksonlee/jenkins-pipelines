def call(Map cfg = [:]) {
    def serverId = cfg.get('serverId', 'artifactory')
    def repo = cfg.get('repo', 'android-snapshots')
    def orgPath = cfg.get('orgPath', 'com/maksonlee')
    def module = cfg.get('module', 'beepbeep')
    def folderSuffix = cfg.get('folderSuffix', 'SNAPSHOT')

    def gradleFile = cfg.get('gradleFile', 'app/build.gradle.kts')
    def aabPattern = cfg.get('aabPattern', 'app/build/outputs/bundle/release/*.aab')
    def mappingPattern = cfg.get('mappingPattern', 'app/build/outputs/mapping/release/**')

    def text = readFile(gradleFile)
    def baseRev = extractVersionName(text)
    if (!baseRev) {
        error("Cannot find versionName in ${gradleFile}")
    }

    def timestamp = new Date().format("yyyyMMdd.HHmmss", TimeZone.getTimeZone('UTC'))
    def fileItegRev = "${timestamp}-${env.BUILD_NUMBER}"

    def targetBase = "${repo}/${orgPath}/${module}/${baseRev}-${folderSuffix}"

    sh """#!/bin/bash
    set -euo pipefail
    ls -lh ${aabPattern}
    for f in ${aabPattern}; do
      test -s "\$f"
    done
  """

    def spec = """{
    "files": [
      {
        "pattern": "${aabPattern}",
        "target": "${targetBase}/${module}-${baseRev}-${fileItegRev}.aab",
        "flat": true
      },
      {
        "pattern": "${mappingPattern}",
        "target": "${targetBase}/mapping/",
        "flat": false
      }
    ]
  }"""

    echo "Uploading artifacts to: ${targetBase}"
    rtUpload(serverId: serverId, spec: spec, failNoOp: true)
}

String extractVersionName(String text) {
    for (String line : text.readLines()) {
        def t = line.trim()
        if (t.startsWith("versionName") && t.contains("\"")) {
            int a = t.indexOf('"')
            int b = t.indexOf('"', a + 1)
            if (a >= 0 && b > a) return t.substring(a + 1, b)
        }
    }
    return null
}
