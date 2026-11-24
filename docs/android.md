# Native Android jobs

## `androidCi`: `android/<repo>/ci`

```groovy
@Library('jenkins-shared-lib') _

androidCi(
    repo: 'beepbeep',
    branch: 'master',                                   // default main
    gradleTasks: [':app:testDebugUnitTest', ':app:lintDebug', ':app:assembleDebug', ':app:assembleDebugAndroidTest'],
    debugApkModules: ['app'],
    androidTestModules: ['app'],
    lintModules: ['app'],
    unitTestTasks: ['app': 'testDebugUnitTest']
)
```

**Build.** The job checks out the tip of the branch and verifies that the checkout is that fetched tip and clean.
It then runs `./gradlew <gradleTasks> --continue --console=plain --no-daemon` in `buildImages.android()`:

- The image runs with the shared container limits, the Gradle cache, and the worker's Android SDK mounted read-only
  from `/home/administrator/jenkins/android-cache`.
- `--max-workers` is added only when `maxWorkers` is set; otherwise the project's `gradle.properties` decides.
- `kotlinInProcess: true` adds `-Pkotlin.compiler.execution.strategy=in-process`. Kotlin then compiles inside the
  Gradle process, without a separate Kotlin daemon that inherits the Gradle heap, so a project with a large Gradle
  heap stays within the container's 6 GB limit. The project's own settings are unchanged.
- Android user home is a writable directory in the workspace, with the worker's `debug.keystore` mounted
  read-only.
- No release signing material, Play credentials or Artifactory upload is involved.

Projects use AGP's standard debug signing config and output paths. A project with a root aggregate task can list
only that task, for example `nativeAndroidCi`; learn-chess and cloud-cost-widgets do.

**Evidence.** Each listed output is copied into `evidence/` and archived:

- `junit/<module>/` from `<module>/build/test-results/<task>/*.xml`, for each `unitTestTasks` entry.
- `lint/<module>/` from `<module>/build/reports/lint-results-debug.{xml,html,sarif,txt}`; the XML report is required.
- `apk/debug/<module>/` from `<module>/build/outputs/apk/debug/*.apk`.
- `apk/androidTest/<module>/` from `<module>/build/outputs/apk/androidTest/debug/*.apk`.

`flatAppEvidence: true` drops the `<module>/` level. `SHA256SUMS` lists every APK; run `sha256sum --check SHA256SUMS`
inside the downloaded evidence. `source-commit.txt` and `build.txt` record the commit, image and Gradle exit. A
build fails when Gradle succeeds without every configured output. A failed build still archives what it produced.

**`[skip ci]`.** With `honorSkipCi: true`, a GitHub push whose head commit message contains `[skip ci]` or
`[ci skip]` ends as `NOT_BUILT`, as the GitHub Actions trigger used to skip it. Manual builds always run.

**Tracing and device tests (learn-chess).** With `trace: true`, the job also keeps the plain Gradle log, the Gradle
version, the SDK platform list and before/after checkout state. It runs `summaryScript` from the repository, then
writes `run.json` and `summary.md` and archives everything as `ci-evidence.tar.gz`.

`deviceRunner` adds a device stage, which:

- checks the APK hashes;
- creates one disposable Android 34 AOSP Pixel 2 emulator with KVM, a private ADB server and per-build Android data;
- runs the repository's runner with both APKs;
- removes the emulator and its data afterwards.

The worker needs host Python 3, Java for `avdmanager`, the emulator and platform tools, and
`system-images;android-34;default;x86_64` in the SDK. The device stage reports `skipped` when the APKs, the runner,
the image or KVM are missing.

## `androidRelease`: `android/<repo>/release`

```groovy
@Library('jenkins-shared-lib') _

androidRelease(
    repo: 'taiwandrive',
    packageName: 'com.maksonlee.taiwandrive',
    verifyUploadCertificate: true,                      // the signer must equal Vault certificate_sha256
    verifyTasks: [':engine:test', ':pack:test', ':app:testDebugUnitTest', ':app:lintDebug'],  // default app unit tests + debug lint
    tracks: ['alpha', 'internal'],                      // PLAY_TRACK choices, first is the default; default ['internal']
    releaseStatuses: ['draft', 'completed'],            // default ['completed', 'draft']
    forbiddenPaths: ['app/src/main/assets/offline_pack.zip'],
    forbiddenAabEntry: 'offline_pack\\.zip$'
)
```

Parameters are `GIT_REF`, `PUBLISH_TO_PLAY` (default false), `PLAY_TRACK` and `PLAY_RELEASE_STATUS`. The stages run in
this order:

1. **Resolve.** `GIT_REF` must be the branch or a full commit SHA contained in `origin/<branch>`. The checkout must be
   clean and must not contain any `forbiddenPaths`. This is checked before any signing material is read.
2. **Release notes.** Every locale under `<module>/src/main/play/release-notes/` needs a non-empty `<track>.txt` or
   `default.txt`.
3. **Build.** `gradleRelease(':<module>:bundleRelease', extraArgs: verifyTasks)`. Signing comes from Vault
   `secret/jenkins/mobile/app/<packageName>` (`upload_jks_b64`, `store_password`, `key_password`, `key_alias`)
   through the `SIGNING_*` environment variables. A project that reads only `-Psigning.*` properties sets
   `passSigningProperties: true` until its build reads `SIGNING_*`.
4. **Verify.** On the agent:
   - the AAB must be the only bundle in its directory, and bundletool 1.18.3 (checked against its pinned SHA-256)
     validates it;
   - the manifest package must equal `packageName`, and its versionCode and versionName are recorded in
     `release-manifest.txt`;
   - strict `jarsigner -verify` must pass with no unsigned entries;
   - the signer certificate SHA-256 is recorded as `signerSha256` and shown in the approval input. With
     `verifyUploadCertificate: true` it must also equal Vault `certificate_sha256`. Without it, Google Play checks
     the upload key when the AAB is uploaded. Fingerprints are never written to this public repository;
   - no entry may match `forbiddenAabEntry`.

   The AAB, its `.sha256`, the signature report, the manifest summary and the bundletool output are archived and
   fingerprinted.
5. **Approve** (only with `PUBLISH_TO_PLAY`). The input names the package, version, commit, AAB SHA-256, signer, track and
   status. It times out after 60 minutes.
6. **Publish.** The verified AAB is checked against its recorded SHA-256 and staged alone in
   `<module>/build/outputs/play-publication`. Then the job reads `secret/jenkins/mobile/shared/play-service` and runs
   GPP `publishReleaseBundle --artifact-dir=<that dir> --track=<track> --release-status=<status>`. The command-line
   options override the project's `play {}` block. GPP 4.0.0's default release status is `completed`.

With `publishListingsWithRelease: true` the job runs `publishReleaseApps` instead, which also publishes the store
listing from the same commit. Only the four apps that always did this keep it. The library never switches tasks on
its own (`publishPlayListings: false`).

## Projects

| Job | Settings |
| --- | --- |
| `android/beepbeep/{ci,release}` | branch `master`; `-Psigning.*` until the build reads `SIGNING_*`; listing with release |
| `android/bluetoothfinder/{ci,release}` | branch `2.x`; same as beepbeep |
| `android/float-level/{ci,release}` | package `com.maksonlee.floatinglevel`; same as beepbeep |
| `android/headshot-maker-android/{ci,release}` | package `com.maksonlee.headshotmaker`; same as beepbeep |
| `android/taiwandrive/{ci,release}` | engine and pack JVM tests; alpha/internal; no bundled offline pack; signer checked against Vault |
| `android/learn-chess/ci` | `nativeAndroidCi`, traced evidence, emulator device suite, honors `[skip ci]` |
| `android/cloud-cost-widgets/ci` | `nativeAndroidCi` for the phone and Wear OS apps; Kotlin compiled in-process |
| `android/gst-stream-wall/release` | own Jenkinsfile (Rust/GStreamer image, upload, promotion and listing modes) |
