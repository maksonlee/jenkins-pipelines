#!/bin/bash
# Run every offline check of this repository: the Python tests and a Groovy syntax check of the library, the Job
# DSL and all Jenkinsfiles. admin/pipelines-ci runs this after each push; run it locally before pushing.
# The Groovy check proves syntax only, not Jenkins semantics.
set -euo pipefail
cd "$(dirname "$0")/.."

GROOVY_IMAGE='groovy@sha256:4eed25f51ec3a52c5f1517bae05c26846c0cc7c0d423186988bce0e7c8502e11'

echo '== Library, Play tools, job list and Artifactory cleanup tests'
python3 -B -m unittest discover -s tests -t .
echo '== gst-stream-wall release tests'
python3 -B -m unittest Jenkinsfile/android/gst-stream-wall/release/test_publication_artifact_selection.py
echo '== cloud-cost-widgets release tests'
python3 -B -m unittest discover -s Jenkinsfile/android/cloud-cost-widgets/release -p 'test_*.py'
echo '== Unity math-runner checks'
c=Jenkinsfile/unity/math-runner-android-build-in-docker
python3 "$c/tests/validate.py" "$c/Jenkinsfile" "$c/bootstrap-unity-personal.sh"
python3 "$c/tests/contract_test.py" "$c/Jenkinsfile"
p=Jenkinsfile/unity/math-runner-play-internal
python3 -B -m unittest "$p/tests/test_publish.py" "$p/tests/test_artifactory_adapter.py"
echo '== piano-practice deploy tests'
python3 -B -m unittest discover -s Jenkinsfile/web/piano-practice/deploy -p 'test_*.py'

echo '== Groovy syntax'
work=$(mktemp -d "${TMPDIR:-/tmp}/groovy-check.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/src"
cp vars/*.groovy jobs/*.groovy "$work/src/"
count=0
while IFS= read -r file; do
  name=$(printf '%s' "${file%/Jenkinsfile}" | sed 's|/|__|g' | tr '.-' '__')
  grep -v '^@Library' "$file" > "$work/src/$name.groovy"
  count=$((count + 1))
done < <(git ls-files | grep -E '(^|/)Jenkinsfile$')
docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$work:/w" -w /w "$GROOVY_IMAGE" \
  sh -c 'groovyc -d classes src/*.groovy' 2> >(grep -v 'incubator modules' >&2)
echo "Groovy syntax OK: library, Job DSL and $count Jenkinsfiles"

git diff --check
echo 'All checks passed.'
