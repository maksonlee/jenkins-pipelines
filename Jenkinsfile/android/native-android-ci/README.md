# Native Android Debug CI

`android/native-android-ci` is a Pipeline from SCM job for native Android projects under `maksonlee`. Jenkins loads this Jenkinsfile from `jenkins-pipelines` `main` using `vault-github-ssh`. `job-config.disabled.xml` is the initial creation template.

Choose **Build with Parameters** and set `PROJECT` to `learn-chess` or `cloud-cost-widgets` to build the tip of that repository's `main`. The job uses a clean checkout and keeps the five most recent builds with their evidence. Use the authorized Jenkins build page to download it.

## Project contract

Commit an executable Gradle wrapper and define a root Gradle task named `nativeAndroidCi` that depends on the project's required content and unit checks, debug lint, `assembleDebug` for each application, and `assembleDebugAndroidTest` for modules with instrumentation tests. The worker runs the task with `--continue --console=plain --no-daemon --max-workers=2` so eligible checks can finish when a task fails. Instrumentation APK assembly prepares APKs for later device testing.

Use AGP's standard debug signing config and output paths. Configure each required debug APK, instrumentation APK, lint, and unit-test module in `nativeAndroidProjects()` in the Jenkinsfile. The job collects the following files:

- `<module>/build/test-results/<task>/*.xml` for each configured unit-test task.
- `<module>/build/reports/lint-results-debug.xml` and matching HTML, SARIF or text reports when available.
- `<module>/build/outputs/apk/debug/*.apk` for each configured app.
- `<module>/build/outputs/apk/androidTest/debug/*.apk` for each configured instrumentation module.

Artifacts and reports appear under `evidence/`. The job confirms the checkout matches the fetched main tip for manual runs or the verified push SHA for webhook runs. `source-commit.txt` records that SHA, `build.txt` records the repository, pinned image and command exit, and `SHA256SUMS` lists checksums for every archived APK. Download the evidence directory and run `sha256sum --check SHA256SUMS` inside it. A build fails if any required output is missing, and can still contain reports and APKs from tasks that completed. Use completed successful builds for CI artifacts.

`learn-chess` keeps its app output paths under `apk/debug/`, `apk/androidTest/`, and `lint/`. For `cloud-cost-widgets`, the module path follows the output directory:

| Evidence directory under `evidence/` | Output |
| --- | --- |
| `apk/debug/apps/aws/` | Phone APK |
| `apk/debug/apps/aws-wear/` | Wear OS APK |
| `apk/androidTest/providers/aws/` | AWS provider instrumentation APK |
| `apk/androidTest/apps/aws-wear/` | Wear OS instrumentation APK |
| `junit/shared/core/`, `junit/providers/aws/` | JVM test XML |
| `lint/apps/aws/`, `lint/apps/aws-wear/`, `lint/providers/aws/`, `lint/shared/core/` | Debug lint reports |

## Learn Chess build and device evidence

Open the run-specific Jenkins URL on the commit, sign in, and download `ci-evidence.tar.gz` from the build artifacts. The archive contains both APKs, the Gradle and native test logs, diagnostics, JUnit XML, lint reports, and screenshots when those outputs were produced. Extract the bundle and run `sha256sum --check SHA256SUMS` in the extracted directory.

`run.json` records the requested SHA, verified checkout, workspace checks, build URL, pinned image, and separate build and device outcomes. `build-summary.json` records the command, Gradle and Java runtime, pinned plugin versions, SDK platform metadata, unit and lint counts, production content validator totals, and each APK's size and SHA-256. `device/results/summary.json` records the device matrix, native tests, and screenshot evidence. The top `summary.md` and the native test `summary.md` accompany the reports. Partial results are retained when available for failed, skipped, or interrupted runs.

The device stage uses the two APKs from that run after checking their hashes. It creates one disposable Android 34 AOSP Pixel 2 emulator on the Jenkins worker with KVM, a private ADB server, and per-build Android data. It runs the Learn Chess native suite, including the English and Traditional Chinese walkthroughs, and removes the emulator and its data after the run. It requires host Python 3, Java for `avdmanager`, the emulator and platform tools, and `system-images;android-34;default;x86_64` in the worker SDK.

## Worker and push setup

Use an `ssh-agent-with-docker` worker and the pinned `buildImages.android()` image. The worker supplies the Gradle cache and a read-only Android SDK from `/home/administrator/jenkins/android-cache`. Provision the SDK platforms and tools needed by each opted-in project. `learn-chess` uses Platform 37.0. `cloud-cost-widgets` uses Platform 37.1 for the phone and Wear OS apps and Platform 37.0 for its libraries. Each build uses a writable Android user home under its workspace. The worker's existing `/home/administrator/.android/debug.keystore` is mounted there read-only as the container's default Android debug keystore.

Configure `native-android-ci-webhook-token` as a Jenkins secret-text credential. Initialize the Generic Webhook Trigger from the Jenkinsfile settings when creating the job, or install it with the first manual run. To opt a repository in, add its production modules to `nativeAndroidProjects()` and configure its main-push sender with that token. The sender posts JSON to `${JENKINS_URL}/generic-webhook-trigger/invoke` with `Content-Type: application/json`, `X-GitHub-Event: push`, and the token in the `token` header.

The `learn-chess` and `cloud-cost-widgets` workflows at `.github/workflows/native-android-ci.yml` read the token from each repository's `NATIVE_ANDROID_CI_WEBHOOK_TOKEN` Actions secret. A successful workflow run confirms Jenkins queue acceptance. The Jenkins build page shows the run and artifacts.

The trigger accepts a push only when `repository.full_name` identifies an opted-in repository, `ref` is `refs/heads/main`, `deleted` is false and `after` is a nonzero 40-character commit SHA. The job verifies that commit against the trusted repository's fetched `main` history before checking it out. Enable artifact access only for users authorized to see the opted-in projects.

For a verified Learn Chess push, Jenkins sets `ci/jenkins/native-android/build` and `ci/jenkins/native-android/device` to pending after the history check, then publishes the two final outcomes with the build URL. Failure is a failed status. Aborted and skipped phases are GitHub error statuses with descriptions that identify the outcome. An unverified SHA is recorded in Jenkins when the workspace is available. GitHub receives execution statuses only for verified pushed SHAs. Manual runs produce Jenkins evidence. Rerun the GitHub workflow to queue the event SHA again. Jenkins publishes execution statuses in its serialized build order using its configured GitHub server integration.

## Local contract checks

From the pipeline repository root, run `python Jenkinsfile/android/native-android-ci/test_contract.py` with Bash, Git, and GNU coreutils on `PATH`. On Windows, set `BASH` to the full path to Git for Windows' `bin/bash.exe`.
