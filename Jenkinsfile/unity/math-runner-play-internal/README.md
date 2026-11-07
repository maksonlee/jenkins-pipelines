# Math Runner internal Play publisher

Jenkins job: `unity/math-runner-play-internal`. The disabled XML is an initial creation template, not the live job state. This job does not build Unity, access the persistent Unity Personal worker, or read the Android upload JKS. It uses a fixed Artifactory path for an already signed AAB and fetches the shared Play service-account `json_b64` from Vault only after a human approves the exact internal action.

## Current live result (2026-11-07)

The job is enabled. Its [Artifactory-backed verification preview #4](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/4/) completed `SUCCESS` with `PUBLISH_TO_PLAY=false`. The exact same source artifact was then approved at the human input in [publisher build #5](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/5/), which completed `SUCCESS`: Play reported one exact AAB upload and a confirmed edit commit to `internal` status `draft`, versionCode `1`. A fresh package-only API readback found bundle code `1` with SHA-256 `a22ec535fd6e587640198109bbe153813665b15ecac0ec76dcf8621d3a624931` and release `0.1.0` / `draft`; that readback deleted only its own temporary edit. Draft is not served to testers. No completed rollout or tester invitation occurred.

Builds #4 and #5 loaded pipeline repository commit `042de73759c8c70e56171307fa82546299206f81`; the product AAB remains from commit `8602e68745213fbc93e63281e39a1f5111466977` and source Jenkins build #1. Build #1's Artifactory import and seven byte readbacks were done by a bounded protected API seed, not by a new Unity build. The new source-job `rtUpload` stage remains unexercised until a future explicitly requested source build. Earlier Copy Artifact preview #1 failed because its source permission lacked a leading slash, #2 succeeded after that permission fix, and old draft #3 was aborted at input before Play Vault or Play writes when the artifact route changed. The unused source Copy Artifact permission has since been removed; Copy Artifact itself remains installed on Jenkins.

## Source and Artifactory contract

The source is `unity/math-runner-android-build-in-docker`, with `SOURCE_BUILD_NUMBER` and `EXPECTED_AAB_SHA256` as the only artifact selectors. For build number `N` and expected lowercase hex digest `H`, the Jenkins Artifactory plugin downloads exactly from:

```text
android-snapshots/com/maksonlee/math-runner/jenkins-build-N-sha256-H-SNAPSHOT/
```

This is an existing local Maven snapshot repository and a Maven-shaped `group/artifact/version` path. The folder contains the seven public source verification files. `SportsDayMath.aab.sha256` is stored as `SportsDayMath.aab.sha256.txt` to avoid Artifactory's generated `.sha256` checksum URL; the publisher restores the original filename and bytes before verification. The other six files keep their names. An empty, partial or extra-file download fails closed. There is no Jenkins archive fallback, arbitrary URL, arbitrary repository or other package/track selector.

Future source builds verify and archive the seven files, check whether this exact Artifactory path has none or all seven identical bytes, upload only if absent, then download all seven and compare size and SHA-256 again. A conflicting or partial path fails. Build #1 predates that source stage; its verified seven archived files were imported once with a protected API process, with seven Artifactory byte readbacks. The snapshot repository is mutable and its retention policy was not available through the installed Artifactory edition, so the path alone is not an immutability guarantee. Every publisher run verifies the downloaded AAB and all proof again.

The publisher requires `release-verification.status=passed`, the exact expected AAB SHA, package `com.maksonlee.mathrunner`, version/code from the source report, ARM64 Unity IL2CPP, Unity BuildReport `Succeeded` and zero errors, the sidecars/manifest, and the fixed public signer SHA-256 `E11BC3918C83D0082BF28B72F2F99BC6F2867C38559F701CF4FDCE0F3702CEC7`. It independently runs strict `jarsigner` and `keytool` checks on the downloaded AAB. Build #1 has separate [Jenkins SUCCESS evidence](https://jenkins.maksonlee.com/job/unity/job/math-runner-android-build-in-docker/1/); exact Artifactory bytes and reports are required even when source Jenkins history is available. The publisher does not claim to rerun bundletool on every download.

## Play transaction

`PUBLISH_TO_PLAY=false` is verification only. `true` pauses at Jenkins `input` showing source build, product commit, version/code, AAB SHA, public signer and selected `internal` status. The helper/report/AAB digests are fixed before that input and rechecked before Vault. The default status is `draft`, which is not served to testers; `completed` must be explicitly selected and can reach eligible internal testers. The en-US release note is `Math Runner <version> internal test build.`

With approval, the transaction uses only Vault KV v2 `secret/jenkins/mobile/shared/play-service` / `json_b64`; it does not use the upload JKS, Unity credentials or the host administrator Jenkins API token. It can upload this AAB once or reuse an existing Play bundle only if its versionCode and API SHA-256 match. Version conflicts, ambiguous uploads/commits, unrelated drafts, newer versions and unsafe track changes fail closed without automatic retry. A draft preserves an older completed release; an explicitly selected completed target replaces an older completed release. `edits.commit` uses `changesInReviewBehavior=ERROR_IF_IN_REVIEW`; a cleanup attempt deletes only this job's uncommitted edit. The host `flock` serializes this job's edits but cannot prevent external Console/API activity.

The agent is the existing `ssh-agent-with-docker` Linux node; the actual remoting process runs as `administrator` UID 1000 on `client.maksonlee.com`. Python 3.14.4, cryptography 46.0.5, Java `jarsigner`/`keytool` and the lock parent were checked on that same host. No image or Unity license operation is involved.

## Checks and limits

From the `jenkins-pipelines` root:

```bash
candidate=Jenkinsfile/unity/math-runner-play-internal
python3 -B -m unittest "$candidate/tests/test_publish.py" "$candidate/tests/test_artifactory_adapter.py"
python3 -B -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ["Jenkinsfile/unity/math-runner-play-internal/publish.py", "Jenkinsfile/unity/math-runner-play-internal/tests/test_publish.py", "Jenkinsfile/unity/math-runner-play-internal/tests/test_artifactory_adapter.py"]]'
xmllint --noout "$candidate/job-config.disabled.xml"
git diff --check
```

Run Jenkins' official Declarative linter for both changed Jenkinsfiles. The adapter tests cover local conflict, complete readback, missing/extra files and original checksum-sidecar restoration; they do not run the Artifactory plugin or Play API. The first Artifactory-backed preview and publication results belong in the Math Runner repository `Evidence/Checks/PlayReadiness` and `Evidence/Maintenance`. The earlier Copy Artifact preview #2 succeeded, and old draft build #3 was aborted at its input before Vault/Play writes when the artifact route changed. Copy Artifact 805.v9d1393350780 remains installed but is not part of this publisher route.
