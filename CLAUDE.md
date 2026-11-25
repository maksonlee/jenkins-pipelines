# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repository holds Jenkins Pipelines, the `jenkins-shared-lib` shared library they import with
`@Library('jenkins-shared-lib') _`, and the Dockerfiles for their build images. Jenkins loads everything from
`git@github.com:maksonlee/jenkins-pipelines.git` `main`:

- Each job is Pipeline from SCM with script path `Jenkinsfile/<folder>/<job>/Jenkinsfile`, so the Jenkins job path
  mirrors the directory.
- **A push to `main` changes what every job runs next**, and a change in `vars/` or `resources/` reaches every job
  that uses it.
- All jobs run on the single agent `ssh-agent-with-docker`. It has one executor, runs the build containers with its
  Docker daemon, and keeps persistent caches under `/home/administrator/jenkins/`.

`README.md` and `docs/jobs.md`, `docs/android.md`, `docs/flutter.md` and `docker/README.md` are the authoritative
contracts. Update them in the same change as the behavior they describe.

## Layout and the 2026-11 cleanup

- **The target layout** is one folder per project with standard jobs, and the shared code in the library, never one
  job shared by several projects:
  - `android/<repo>/{ci,release}`: `androidCi`, `androidRelease`
  - `flutter/<repo>/{ci,release}`: `flutterCi`, `flutterRelease`
- **Thin Jenkinsfiles.** A project's Jenkinsfile only passes settings to one step. Shell and Python live in
  `resources/` and are loaded with `libraryResource`, so they can be tested directly.
- **Job DSL.** `jobs/jenkins_jobs.groovy` is applied by `admin/seed-jobs` with `ignoreExisting: true`:
  - The seed creates missing jobs only and never updates or deletes them. An update replaces `config.xml`, which
    drops the Jenkinsfile-applied parameters and triggers, and deletes folder-scoped credentials if a top-level
    folder such as `android` or `web` is regenerated.
  - Never declare a top-level folder in the DSL.
- **Migration** runs new jobs next to the old ones. An old job is disabled once its replacement works, and deleted
  only with the owner's approval. The 12 Android and Flutter jobs replaced in 2026-11 (such as
  `android/beepbeep-master-ci`, `android/taiwandrive-android-build-in-docker`, `android/native-android-ci` and
  `flutter/flutter-android-build`) were deleted on 2026-11-24; their Jenkinsfiles remain in git history.
- **Exceptions on purpose:**
  - `unity/math-runner-*`: job names are part of its evidence and seal.
  - `web/piano-practice/{ci,deploy}`: their own Jenkinsfiles. `release_ops.py`, which also runs installed as root on
    the origin, and `package-release.mjs` accept the CI job names `web/piano-practice/ci` and the former
    `web/piano-practice-main-ci`, because the releases on the origin were built under the former name. The former
    jobs `web/piano-practice-main-ci` and `web/piano-practice-deploy` stay until the owner retires them. After a
    change to `release_ops.py`, reinstall it on the origin (see the deploy README).
  - `android/gst-stream-wall/release`: its own qualified Jenkinsfile.
  - `android/cloud-cost-widgets/release`: its own Jenkinsfile with alpha-status, internal-preflight and
    internal-submit modes and its own Play tools. Merged from `codex/cloud-cost-widgets-release` on 2026-11-24;
    the old `android/cloud-cost-widgets-release` job and that branch were deleted on 2026-11-25.
  - `android/veil-android/release` (unsigned build, `sign-aab.sh` outside Gradle, the Flutter Play tools) and
    `android/termstay/release` (runs the repository's own release script, which reads Vault with the agent's token
    and the private `termstay-cache/release.env` on the agent): their own Jenkinsfiles.
  - Deploy jobs, all manual with `verify-only` first and an approval before production changes:
    `web/hongyu-portal/deploy` and `web/veil-backend/deploy` (each with its own `release_ops.py` and tests),
    `web/share-notes/deploy` and `web/readerplay/deploy` (both on `resources/web/static_site_release.py`), and
    `flutter/taiwan-bus-app/backend-deploy` (runs the repository's deploy script). They log in to the hosts with
    the agent's own SSH key. An origin helper is sent over SSH on every run; nothing is installed on a host.
  - `artifactory/artifactory-cleanup`: weekly; every app in `android-snapshots` keeps its newest `KEEP_VERSIONS`
    version folders (5, approved by the owner on 2026-11-25), and `math-runner` (Unity evidence) is never touched.
    Scheduled runs delete; uncheck `DELETE` to only report. Tests: `tests/artifactory/`.
  - `backstage/homelab-backstage-main-ci`: its own Jenkinsfile.
  - Every job except `admin/seed-jobs` is listed in the DSL, the exceptions as `standaloneJobs`, so a rebuilt
    Jenkins gets them back. `admin/pipelines-ci` runs `tools/run_checks.sh` after each push; it polls on purpose
    (a webhook on this repository would reach every `githubPush()` job).
  - `aosp/aosp-build-in-docker`, `build-prj-0001`, `k8s-dynamic-agent-test` and
    `monitoring/taiwan-bus-production-health` were deleted on 2026-11-25: their Gerrit and Kubernetes hosts were
    unreachable and the monitor had been disabled since 2026-08-01. Their Jenkinsfiles remain in git history.

## Checks

Groovy cannot run locally. These checks are offline unless noted:

```bash
tools/run_checks.sh                                                 # all offline checks below + Groovy syntax; CI runs it
python3 -B -m unittest discover -s tests -t .                       # library scripts, Play tools, job list
python3 -B -m unittest discover -s tests -t . -k release            # a subset by name
python3 -B -m unittest Jenkinsfile/android/gst-stream-wall/release/test_publication_artifact_selection.py
python3 -B -m unittest discover -s Jenkinsfile/android/cloud-cost-widgets/release -p 'test_*.py'
python3 -I tools/jenkins_inventory.py --check                       # live Jenkins, GET only

c=Jenkinsfile/unity/math-runner-android-build-in-docker
python3 "$c/tests/validate.py" "$c/Jenkinsfile" "$c/bootstrap-unity-personal.sh"
python3 "$c/tests/contract_test.py" "$c/Jenkinsfile"
p=Jenkinsfile/unity/math-runner-play-internal
python3 -B -m unittest "$p/tests/test_publish.py" "$p/tests/test_artifactory_adapter.py"
python3 -B -m unittest discover -s Jenkinsfile/web/piano-practice/deploy -p 'test_*.py'
git diff --check
```

- **Groovy syntax** can be checked with the local `groovy` image:
  `docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v <dir>:/w -w /w groovy:latest sh -c 'groovyc -d classes src/*.groovy'`.
  Strip `@Library` lines from Jenkinsfiles first. This proves syntax only, not Jenkins semantics; the only real
  verification is a Jenkins run.
- **`tools/jenkins_inventory.py` and `Jenkinsfile/unity/.../tests/jenkins_lint.py`** read `JENKINS_URL`,
  `JENKINS_USER` and `JENKINS_API_TOKEN` from literal assignments in `~/.bashrc` and never print them.
  `jenkins_lint.py` also checks the math-runner job config.

## Library facts that are easy to break

- **`gradleRelease(task, cfg)`** runs Gradle in `buildImages.android()` through `withAndroidReleaseEnv`:
  - the Android cache is mounted, Vault material is decoded to `/tmp` files and deleted in `finally`, and a host
    `flock` serializes release builds;
  - it exports `SIGNING_*` and also passes `-Psigning.*` unless `passSigningProperties: false`;
  - it switches `publish<Variant>Bundle` to `publish<Variant>Apps` when Play listings exist, unless
    `publishPlayListings: false`. `androidRelease` always sets false and picks the task explicitly.
- **`androidRelease`** uploads only the verified AAB through GPP `--artifact-dir`, `--track` and `--release-status`.
  GPP 4.0.0 supports these options on publish tasks; its default release status is `completed`. The signer is
  recorded, and compared with Vault `certificate_sha256` only when `verifyUploadCertificate: true`. Never put
  fingerprints or other operational secrets in this public repository.
- **`androidCi`** never reads signing material. It mounts the SDK read-only and the worker `debug.keystore`.
  Checkout polling must stay enabled, or `githubPush()` will not match the repository.
- **Release jobs** use stage timeouts plus a 60-minute approval timeout, not one build timeout, because the input
  holds the only executor.
- **`buildImages.groovy`** pins every image by digest; never use a mutable tag. `flutter-android-build-env` is built
  `FROM` the `android-build-env` digest, and the gst image still uses the legacy 2025 base.
- **Vault layout:**
  - `secret/jenkins/mobile/shared/upload-key` holds the upload key the native Android apps share: `upload_jks_b64`,
    `store_password`, `key_password`, `key_alias` and `certificate_sha256`. `androidRelease` and the gst and
    cloud-cost-widgets release Jenkinsfiles read it;
  - `secret/jenkins/mobile/app/<applicationId>` holds the same fields for apps with their own key: the 4 Flutter
    apps (each registered in Play with a different key) and the sealed Unity math-runner;
  - `secret/jenkins/mobile/shared/play-service` holds `json_b64`.
- **This Jenkins has no `readJSON`** (no pipeline-utility-steps); read JSON with a python one-liner. An empty
  string parameter is not exported to `sh`, so scripts use `${NAME:-}`.
- **Seeding.** A change to `jobs/jenkins_jobs.groovy` needs its script approved again. Compare the pending script
  with the pushed file before approving, and do not edit the local file while a seed run is waiting.
- **Unity:**
  - `publish.py` is pinned by `PUBLISHER_SHA256` in its Jenkinsfile, so update the hash with any edit.
  - The build job's tests expect exactly 8 `sh '''` blocks and 5 embedded `PY` heredocs.
  - `job-config.disabled.xml` files are creation templates, not live state.

## Conventions

- **Groovy and shell:**
  - Pass values to shell through `withEnv` into single-quoted `sh '''#!/bin/bash ...'''` or `libraryResource`
    scripts, never by Groovy interpolation of secrets or parameters.
  - Validate every parameter and config value with a regex first.
  - In `'''` strings Groovy still unescapes `\\` and joins backslash-newline.
- **Release safety:**
  - A release job accepts only the release branch or a full SHA contained in it.
  - Every Play write follows a manual `input` that names the commit, version, SHA-256, track and status.
  - Play credentials are read only after that input.
- **Result-only README updates.** The Unity READMEs record live run results. They are updated after a run, and
  never claim a run that did not happen.
- **Commit subjects** are plain imperative sentences, for example "Let projects exclude devices from connected
  tests". Older commits used `ci:` and `fix:` prefixes.
