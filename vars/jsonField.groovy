// One top-level field of a JSON file in the workspace, as text; a list is joined with commas. This Jenkins has no
// readJSON step. The result is untrusted text: check it against a regex before it reaches a message or a shell.
//
//     env.RELEASE_ID = jsonField('deploy-evidence/candidate.json', 'releaseId')

def call(String file, String key) {
    withEnv(["JSON_FILE=${file}", "JSON_KEY=${key}"]) {
        return sh(returnStdout: true, script: '''#!/bin/bash
set -euo pipefail
python3 -c 'import json, os; value = json.load(open(os.environ["JSON_FILE"]))[os.environ["JSON_KEY"]]; print(",".join(value) if isinstance(value, list) else value)'
''').trim()
    }
}
