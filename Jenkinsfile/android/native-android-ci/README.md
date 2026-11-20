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

## Worker and push setup

Use an `ssh-agent-with-docker` worker and the pinned `buildImages.android()` image. The worker supplies the Gradle cache and a read-only Android SDK from `/home/administrator/jenkins/android-cache`. Provision the SDK platforms and tools needed by each opted-in project. `learn-chess` uses Platform 37.0. `cloud-cost-widgets` uses Platform 37.1 for the phone and Wear OS apps and Platform 37.0 for its libraries. Each build uses a writable Android user home under its workspace. The worker's existing `/home/administrator/.android/debug.keystore` is mounted there read-only as the container's default Android debug keystore.

Configure `native-android-ci-webhook-token` as a Jenkins secret-text credential. Initialize the Generic Webhook Trigger from the Jenkinsfile settings when creating the job, or install it with the first manual run. To opt a repository in, add its production modules to `nativeAndroidProjects()` and configure its main-push sender with that token. The sender posts JSON to `${JENKINS_URL}/generic-webhook-trigger/invoke` with `Content-Type: application/json`, `X-GitHub-Event: push`, and the token in the `token` header.

The `learn-chess` and `cloud-cost-widgets` workflows at `.github/workflows/native-android-ci.yml` read the token from each repository's `NATIVE_ANDROID_CI_WEBHOOK_TOKEN` Actions secret. A successful workflow run confirms the Jenkins build was queued. The Jenkins build page shows the result and artifacts.

The trigger accepts a push only when `repository.full_name` identifies an opted-in repository, `ref` is `refs/heads/main`, `deleted` is false and `after` is a nonzero 40-character commit SHA. The job verifies that commit against the trusted repository's fetched `main` history before checking it out. Enable artifact access only for users authorized to see the opted-in projects.

## Local contract checks

From the pipeline repository root, run `python Jenkinsfile/android/native-android-ci/test_contract.py` with Bash, Git, and GNU coreutils on `PATH`. On Windows, set `BASH` to the full path to Git for Windows' `bin/bash.exe`.
