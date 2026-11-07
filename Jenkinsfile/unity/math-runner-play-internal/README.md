# Math Runner internal Play publisher

Jenkins job: `unity/math-runner-play-internal`. The disabled XML is an initial creation template, not the live job state. This job does not build Unity, access the persistent Unity Personal worker, or read the Android upload JKS. It uses a fixed Artifactory path for an already signed AAB and fetches the shared Play service-account `json_b64` from Vault only after a human approves the exact internal action.

## Current live result (2026-11-07)

The job is enabled. The original Artifactory-backed [verification preview #4](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/4/) and [draft #5](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/5/) succeeded for the code 1 AAB. The privacy-link [verification preview #6](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/6/) then succeeded without Play access. After exact input approval, [publisher #7](https://jenkins.maksonlee.com/job/unity/job/math-runner-play-internal/7/) succeeded: Play confirmed one upload of signed code 2 AAB SHA-256 `05758d35565ad8e6f4f48cae6ac8b1d8fece10c917ac25100f3966a181458372` and one edit commit replacing the code 1 `internal` draft with version `0.1.0` / code `2` / `draft`. A fresh package-only readback confirmed that bundle and single internal draft, with no other-track releases; its own temporary edit was deleted. Draft is not served to testers. No completed rollout or tester invitation occurred.

Builds #4 and #5 loaded pipeline repository commit `042de73759c8c70e56171307fa82546299206f81` and source build #1 from product commit `8602e68745213fbc93e63281e39a1f5111466977`. Builds #6 and #7 loaded pipeline commit `7fd64455ff25026e4e3de86791b8704a0fb4904c` and signed source build #2 from product commit `88167cf916937bfb7ba7eb290bacdba99833cb98`. Build #1's Artifactory import was a one-time protected API seed; build #2 exercised the source-job `rtUpload` stage and seven byte readbacks. This README update is after those runtime inputs. Earlier Copy Artifact preview #1 failed because its source permission lacked a leading slash, #2 succeeded after that permission fix, and old draft #3 was aborted at input before Play Vault or Play writes when the artifact route changed. The unused source Copy Artifact permission has since been removed; Copy Artifact itself remains installed on Jenkins.

## Guarded draft replacement

The guarded replacement route ran for code 2 in publisher #7, with both
blank-default replacement parameters present in the live enabled job config.
Future runs must still confirm the then-current Play state before selecting a
predecessor. The disabled XML is only an initial source template; a new SCM
Jenkinsfile alone does not add live parameters.

The default route leaves both new parameters empty and keeps the existing
fail-closed behavior for an unrelated draft. To replace an internal draft in
one edit, set both `REPLACE_DRAFT_VERSION_CODE` and
`EXPECTED_REPLACED_DRAFT_SHA256`; publisher #7 used the former code 1 bundle SHA-256
`a22ec535fd6e587640198109bbe153813665b15ecac0ec76dcf8621d3a624931`.
The target must have a higher versionCode and `RELEASE_STATUS=draft`. Before
bundle upload, the publisher checks the exact old Play bundle SHA-256 and a
single matching old draft on the internal track. Missing, changed, completed,
multiple-version, newer, or unrelated draft state fails closed. A matching
target bundle may be reused, but it does not bypass the predecessor check.

The approval input displays the verified source build and product commit,
target version name/code, new AAB SHA-256 and upload signer, internal draft
status, and expected old draft code/SHA and replacement effect. The pinned
helper, verified report, and new AAB hashes are rechecked after input.
The transaction uploads or reuses the target bundle, sends one desired
internal track state that omits the old draft and includes the new draft,
then commits with `ERROR_IF_IN_REVIEW`. It does not delete the old draft in
a separate edit, complete a release, invite testers, or retry an ambiguous
upload/commit. If the old draft has disappeared after an uncertain result,
a repeat invocation with the same predecessor parameters fails closed
pending state inspection. Earlier completed releases retain the existing
draft-route policy.

## Source and Artifactory contract

The source is `unity/math-runner-android-build-in-docker`, with `SOURCE_BUILD_NUMBER` and `EXPECTED_AAB_SHA256` as the only artifact selectors. For build number `N` and expected lowercase hex digest `H`, the Jenkins Artifactory plugin downloads exactly from:

```text
android-snapshots/com/maksonlee/math-runner/jenkins-build-N-sha256-H-SNAPSHOT/
```

This is an existing local Maven snapshot repository and a Maven-shaped `group/artifact/version` path. The folder contains the seven public source verification files. `SportsDayMath.aab.sha256` is stored as `SportsDayMath.aab.sha256.txt` to avoid Artifactory's generated `.sha256` checksum URL; the publisher restores the original filename and bytes before verification. The other six files keep their names. An empty, partial or extra-file download fails closed. There is no Jenkins archive fallback, arbitrary URL, arbitrary repository or other package/track selector.

Source builds verify and archive the seven files, check whether this exact Artifactory path has none or all seven identical bytes, upload only if absent, then download all seven and compare size and SHA-256 again. A conflicting or partial path fails. Build #1 predates that stage; its verified seven archived files were imported once with a protected API process. Build #2 exercised the stage and independently matched all seven Artifactory byte readbacks to its Jenkins archive. The snapshot repository is mutable and its retention policy was not available through the installed Artifactory edition, so the path alone is not an immutability guarantee. Every publisher run verifies the downloaded AAB and all proof again.

The publisher requires `release-verification.status=passed`, the exact expected AAB SHA, package `com.maksonlee.mathrunner`, version/code from the source report, ARM64 Unity IL2CPP, Unity BuildReport `Succeeded` and zero errors, the sidecars/manifest, and the fixed public signer SHA-256 `E11BC3918C83D0082BF28B72F2F99BC6F2867C38559F701CF4FDCE0F3702CEC7`. It independently runs strict `jarsigner` and `keytool` checks on the downloaded AAB. Build #1 has separate [Jenkins SUCCESS evidence](https://jenkins.maksonlee.com/job/unity/job/math-runner-android-build-in-docker/1/); exact Artifactory bytes and reports are required even when source Jenkins history is available. The publisher does not claim to rerun bundletool on every download.

## Play transaction

`PUBLISH_TO_PLAY=false` is verification only. `true` pauses at Jenkins `input` showing source build, product commit, version/code, AAB SHA, public signer and selected `internal` status. The helper/report/AAB digests are fixed before that input and rechecked before Vault. The default status is `draft`, which is not served to testers; `completed` must be explicitly selected and can reach eligible internal testers. The en-US release note is `Math Runner <version> internal test build.`

With approval, the transaction uses only Vault KV v2 `secret/jenkins/mobile/shared/play-service` / `json_b64`; it does not use the upload JKS, Unity credentials or the host administrator Jenkins API token. It can upload this AAB once or reuse an existing Play bundle only if its versionCode and API SHA-256 match. Version conflicts, ambiguous uploads/commits, unrelated drafts without the explicit matching predecessor, newer versions and unsafe track changes fail closed without automatic retry. A draft preserves an older completed release; an explicitly selected completed target replaces an older completed release. `edits.commit` uses `changesInReviewBehavior=ERROR_IF_IN_REVIEW`; a cleanup attempt deletes only this job's uncommitted edit. The host `flock` serializes this job's edits but cannot prevent external Console/API activity.

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

Run Jenkins' official Declarative linter when runtime Jenkinsfiles change. The adapter tests cover local conflict, complete readback, missing/extra files and original checksum-sidecar restoration; the actual Artifactory upload/download ran in source build #2 and publisher #6/#7, while the actual Play draft replacement ran in #7. Results are in the Math Runner repository `Evidence/Checks/PrivacyPolicy/Release` and `Evidence/Maintenance`. The earlier Copy Artifact preview #2 succeeded, and old draft build #3 was aborted at its input before Vault/Play writes when the artifact route changed. Copy Artifact 805.v9d1393350780 remains installed but is not part of this publisher route.
