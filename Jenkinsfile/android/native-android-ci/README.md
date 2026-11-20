# Native Android Debug CI

`android/native-android-ci` is a Pipeline from SCM job for native Android projects under `maksonlee`. Jenkins loads this Jenkinsfile from `jenkins-pipelines` `main` using `vault-github-ssh`. `job-config.disabled.xml` is the initial creation template.

Choose **Build with Parameters** and set `PROJECT` to an opted-in repository slug to build the tip of `main`. The first project is `learn-chess`. The job uses a clean checkout and keeps the five most recent builds with their evidence. Use the authorized Jenkins build page to download it.

## Project contract

Commit an executable Gradle wrapper and define a root Gradle task named `nativeAndroidCi` that depends on the project's required content and unit checks, debug lint, `assembleDebug`, and `assembleDebugAndroidTest`. The worker runs the task with `--continue --console=plain --no-daemon --max-workers=2` so eligible checks can finish when a task fails. Instrumentation APK assembly prepares an APK for later device testing.

Use AGP's standard debug signing config and output paths in the application module. Configure that module and the production unit-test modules and task names in `nativeAndroidProjects()` in the Jenkinsfile. The job collects:

- `<module>/build/test-results/<task>/*.xml` for each configured unit-test task.
- `<app>/build/reports/lint-results-debug.xml` and matching HTML, SARIF or text reports when available.
- `<app>/build/outputs/apk/debug/*.apk` and `<app>/build/outputs/apk/androidTest/debug/*.apk`.

Build artifacts and reports appear under `evidence/`. `source-commit.txt` is the checked-out Git SHA, `build.txt` records the repository, pinned image and command exit, and `SHA256SUMS` lists the APK checksums. Download the evidence directory and run `sha256sum --check SHA256SUMS` inside it. A failed build can still contain reports and APKs from tasks that completed. Use completed successful builds for CI artifacts.

## Worker and push setup

Use an `ssh-agent-with-docker` worker and the pinned `buildImages.android()` image. The worker supplies the Gradle cache and a read-only Android SDK from `/home/administrator/jenkins/android-cache`. Provision the SDK platform and tools needed by each opted-in project. `learn-chess` uses Platform 37.0. Each build uses a writable Android user home under its workspace. The worker's existing `/home/administrator/.android/debug.keystore` is mounted there read-only as the container's default Android debug keystore.

Configure `native-android-ci-webhook-token` as a Jenkins secret-text credential. Initialize the Generic Webhook Trigger from the Jenkinsfile settings when creating the job, or install it with the first manual run. To opt a repository in, add its production modules to `nativeAndroidProjects()` and configure its main-push sender with that token. The sender posts JSON to `${JENKINS_URL}/generic-webhook-trigger/invoke` with `Content-Type: application/json`, `X-GitHub-Event: push`, and the token in the `token` header.

The `learn-chess` workflow `.github/workflows/native-android-ci.yml` reads the token from the repository's `NATIVE_ANDROID_CI_WEBHOOK_TOKEN` Actions secret. A successful workflow run confirms the Jenkins build was queued. The Jenkins build page shows the result and artifacts.

The trigger accepts a push only when `repository.full_name` identifies an opted-in repository, `ref` is `refs/heads/main`, `deleted` is false and `after` is a nonzero 40-character commit SHA. The job verifies that commit against the trusted repository's fetched `main` history before checking it out. Enable artifact access only for users authorized to see the opted-in projects.
