# Math Runner Play internal publisher candidate

This is a separate, **disabled** Jenkins Pipeline source candidate. It has not been committed, pushed, created as a live job or run. It neither builds Unity nor reads the Unity Personal worker state. It consumes only an already signed AAB archive from `unity/math-runner-android-build-in-docker` and would use only the shared Play service-account Vault `json_b64` when explicitly approved to publish. It never reads the Android upload JKS or passwords.

## Fixed first candidate

`SOURCE_BUILD_NUMBER=1` and `EXPECTED_AAB_SHA256=a22ec535fd6e587640198109bbe153813665b15ecac0ec76dcf8621d3a624931` match the independently checked first AAB. The publisher accepts a future exact build number and expected hash without accepting an arbitrary job, URL, package, signer or track. The source job, package `com.maksonlee.mathrunner`, `internal` track and upload-certificate SHA-256 `E11BC3918C83D0082BF28B72F2F99BC6F2867C38559F701CF4FDCE0F3702CEC7` are fixed in source. Product commit, version name/code and image identity come from the archived `release-verification.json`; the image is retained in `verified.json`, while the other identifiers appear at the approval gate. The default release status is `draft`, which is not served to testers; `completed` must be explicitly selected and can serve eligible internal testers. The en-US release note is `Math Runner <version> internal test build.`

`PUBLISH_TO_PLAY=false` runs only source-copy and verification after this job is eventually installed. `true` still pauses at a human `input` naming the exact source build, product commit, version/code, AAB SHA, signer and `internal` release status. The Pipeline fixes the verified report digest before input and rechecks that digest, the AAB SHA and helper SHA immediately after input, before asking Vault for Play credentials. The publishing transaction can upload the exact AAB once, or reuse an existing Play bundle only if both versionCode and the API's bundle SHA-256 equal the verified local bytes. An existing versionCode with no/mismatched hash fails closed. A new `draft` preserves an earlier completed internal release; an explicitly approved `completed` target replaces an older completed version. Unrelated drafts, newer versions, complex track releases and a downgrade of an already completed target fail closed. A fresh approved run can move the same draft bundle to `completed` without re-uploading. Commit uses `changesInReviewBehavior=ERROR_IF_IN_REVIEW`; it does not cancel an ongoing review or fall back to another mode. Ambiguous upload/commit failures are not retried automatically. The code attempts to delete only its own uncommitted edit; a deletion failure requires human inspection before another run.

## Artifact transfer prerequisite

The Jenkins controller currently has no Copy Artifact plugin; anonymous archive access returns HTTP 403, and the source job's `stash` is not available to another job. This candidate uses the standard `copyArtifacts` step with `specific(SOURCE_BUILD_NUMBER)`, a fixed source-job name, exact seven-file filter, `flatten: true`, `optional: false` and fingerprinting. The plugin is **not installed** and its runtime step/permission flow has not been tested. Jenkins core 2.589 satisfies the reviewed Copy Artifact 805.v9d1393350780 minimum 2.541.3; mandatory dependencies were observed installed/active, but dynamic loading or restart need cannot be inferred until an authorized installation. No plugin or live job configuration was changed in this work.

After plugin installation and before a live run, grant the source job's Copy Artifact permission to only `unity/math-runner-play-internal`. A reviewed source `config.xml` addition would be a Copy Artifact permission property under its existing `<properties>` alongside the current parameter definitions, for example:

```xml
<hudson.plugins.copyartifact.CopyArtifactPermissionProperty plugin="copyartifact">
  <projectNameList>unity/math-runner-play-internal</projectNameList>
</hudson.plugins.copyartifact.CopyArtifactPermissionProperty>
```

Confirm the installed plugin's exact serialized form and read back the rest of the source job config before applying it. This candidate does not modify the source build job. Avoid using the host administrator's `~/.bashrc` Jenkins API token, adding a new OIC identity or reading controller artifact files directly.

Copy Artifact's `specific` selector chooses a build number; it does **not** prove that build ended `SUCCESS`. The completed source build #1 has separate [SUCCESS and archive evidence](https://jenkins.maksonlee.com/job/unity/job/math-runner-android-build-in-docker/1/). For any source build, the publisher checks the exact AAB hash, two public sidecars, release report `status=passed`, manifest package/version, ARM64 Unity IL2CPP libraries and Unity BuildReport `Succeeded` with zero errors. It also independently runs `jarsigner -verify -strict` and `keytool -printcert -jarfile` on the copied AAB, allowing only the source pipeline's documented self-signed upload certificate warning and requiring the pinned signer fingerprint. It does not pretend that an archive alone proves the future Jenkins build result or independently re-run bundletool; the source job's verified report and explicit expected bytes remain required.

## Runtime and Play boundaries

The agent is the existing fixed `ssh-agent-with-docker` Linux node. Its SSHLauncher points to `client.maksonlee.com:22` with remoteFS `/home/administrator/jenkins`; the remoting process runs as `administrator` UID 1000 there. The same host/UID supplied `/usr/bin/python3` 3.14.4, `cryptography` 46.0.5, Java `jarsigner` and `keytool` for the earlier source build, and a writable parent for the Play lock. The Pipeline explicitly checks Python's crypto import before the Vault stage. It signs the service-account OAuth assertion entirely in memory. No new image, Unity Editor, Licensing Client, Gradle project or Play Publisher Gradle plugin is needed. `withVault` uses only `secret/jenkins/mobile/shared/play-service` / `json_b64` inside the approved publish stage. A host `flock` serializes this job's Math Runner Play edits; it cannot prevent unrelated external Console/API users from editing the app. `edits.insert` may invalidate an earlier edit from the same API user, so a live run requires awareness of any concurrent work.

The separate Math Runner repository `Evidence/Checks/PlayReadiness/README.md` probe found API access to this app, zero bundles and zero releases, and an en-US listing. The user reported Play Console's protection view as 6/7 active, with the app signing key protected and releases signed by Play. These observations do not prove upload/track/commit permission, the certificate details or first-upload eligibility. Before any live Play write, confirm the required Console first-upload/signing state and the chosen `draft` or `completed` effect; a complete public listing is not a blanket requirement for preparing this internal-test job. No Play API, Unity, Jenkins job or plugin runtime action is performed by the offline tests below.

## Candidate checks

From the `jenkins-pipelines` root after copying this source candidate:

```bash
candidate=Jenkinsfile/unity/math-runner-play-internal
python3 -B -m unittest "$candidate/tests/test_publish.py"
python3 -B -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ["Jenkinsfile/unity/math-runner-play-internal/publish.py", "Jenkinsfile/unity/math-runner-play-internal/tests/test_publish.py"]]'
xmllint --noout "$candidate/job-config.disabled.xml"
git diff --check
```

Run Jenkins' official read-only Declarative linter on this exact `Jenkinsfile` without creating a job. It may validate syntax even while the Copy Artifact step is absent; that is not runtime proof. A successful candidate remains pending plugin installation, source-job copy permission, explicit Play publication authorization and a bounded first live run. Product Unity gates and the already verified AAB are not rerun for this Jenkins-only candidate.

The current five-file candidate passed 12 offline fake-Play/contract cases, format 5/5, Python AST 2/2, embedded Bash 6/6, XML parsing and Jenkins' official Declarative linter (HTTP 200). The first static helper run wrongly counted five Bash blocks because it omitted the `sh(script: '''...''')` form; the helper was corrected to include that form, after which all six passed. This was a diagnostic extractor error, not a Pipeline Bash failure. The `verify` command also passed on a protected disposable copy of build #1's seven archived artifacts, including direct `jarsigner`/`keytool` checks; the copy was removed and its sanitized metadata is in the Math Runner repository `Evidence/Checks/PlayReadiness/PublisherCandidateVerification.json`. No live Copy Artifact step, Jenkins publisher job or Play mutation was run.
