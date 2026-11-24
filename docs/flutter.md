# Flutter Android jobs

Each Flutter app has `flutter/<repo>/ci` (`flutterCi`) and `flutter/<repo>/release` (`flutterRelease`):

```groovy
@Library('jenkins-shared-lib') _

flutterCi(repo: 'share-notes', branch: 'master')        // branch defaults to main
```

```groovy
@Library('jenkins-shared-lib') _

flutterRelease(
    repo: 'taiwan-bus-app',
    // Repository-root Python tests with a disposable PostGIS database; omit for apps without a backend.
    backendTests: [coverageFloor: 35, postgresDatabase: 'taiwan_bus_test']
)
```

## Project rules

- Repository: `git@github.com:maksonlee/<repo>.git`, read with `vault-github-ssh`.
- Layout: `pubspec.yaml` at the root, or `mobile/pubspec.yaml`. The Android `applicationId` comes from
  `android/app/build.gradle(.kts)` under that directory.
- Flutter version: `.fvmrc` in the Flutter directory. The fallback is 3.47.8. Versions are installed by FVM into
  `/home/administrator/jenkins/flutter-cache/fvm`, and the image itself has Android, Dart and FVM.
- Version: `version: <major.minor.patch>+<versionCode>` in `pubspec.yaml`. Releases require that form.
- Caches: `/home/administrator/jenkins/flutter-cache/{fvm,pub-cache}` and
  `/home/administrator/jenkins/android-cache/{flutter-android-sdk,gradle}`.

## Quality gates (both jobs)

**Backend tests.** With `backendTests`, the project needs `tests/` and `requirements-dev.txt` at the repository root:

- `python -m coverage run --branch -m unittest discover -s tests` runs in `buildImages.python()`, and coverage must
  reach `coverageFloor`.
- With `postgresDatabase`, a disposable `buildImages.postgis()` container is reachable through `TEST_DATABASE_URL`.

**Optional repository hooks:**

- `scripts/validate_play_metadata.py`: runs in the Python image.
- `scripts/prepare_flutter_test_fixtures.py`: runs after the Python tests with
  `--output .tmp/flutter-test-fixtures`. Only that directory is writable; the source is read-only.

**Flutter and Android checks:** `pub get`, `analyze`, `test --coverage --branch-coverage`, `:app:lintDebug`, and
`:app:testDebugUnitTest` when `android/app/src/test` exists.

**Files in the Flutter directory:**

- `coverage-minimum.txt` and `branch-coverage-minimum.txt`: numeric percentage floors. The branch floor fails when
  the LCOV report has no branch records.
- `connected-device-minimum.txt`: runs every `integration_test/*_test.dart` and `connectedDebugAndroidTest` on at
  least that many devices. It uses the host Flutter SDK and ADB, and runs from a clean `git archive` of the same
  commit.
  - Authorized physical devices on the agent are used first. A phone that is asleep or shows its lock screen
    cannot run the tests and is skipped; the job never wakes, unlocks or reconfigures a phone. To use a phone, keep
    it unlocked with **Stay awake** on.
  - When fewer are usable, including when none is attached, the job starts disposable Android 34 Pixel 2 emulators
    for this build to make up the difference and removes them afterwards. This needs
    `system-images;android-34;default;x86_64`, the emulator, avdmanager and KVM on the agent; without them the job
    fails before the tests.
  - Emulators the job did not start are never used.
- `connected-device-exclude.txt`: one serial or `ro.product.model` per line (`#` starts a comment). Listed devices
  are skipped and do not count toward the minimum.

## `flutterCi`

The job is triggered by a GitHub push (`githubPush()`; webhook `https://jenkins.maksonlee.com/github-webhook/`) or
started by hand. It builds the branch tip:

1. Runs the quality gates and builds a **debug** AAB.
2. Archives it and uploads it to `android-snapshots/com/maksonlee/<repo>/<version>-SNAPSHOT/`. Old version
   folders are pruned by `artifactory/artifactory-cleanup` (see [jobs.md](jobs.md)).

It uses no release signing and no Google Play access.

## `flutterRelease`

Parameters are `GIT_REF` (the branch or a full SHA contained in it), `PUBLISH_TO_PLAY` (default false), `PLAY_TRACK`
(`internal`, `alpha`, `beta`, `production`), `PLAY_RELEASE_STATUS` (`completed`, `draft`, `inProgress`) and
`PLAY_ROLLOUT_FRACTION` (only with `inProgress`, between 0 and 1).

1. **Resolve.** Checks the commit is in `origin/<branch>`.
2. **Release notes.** Loads them for every language from `android/app/src/main/play/release-notes/<language>/<track>.txt`,
   or else `default.txt`. Each is at most 500 characters, and at least one language is required.
3. **Build.** Runs the quality gates, prepares `android/key.properties` from Vault
   `secret/jenkins/mobile/app/<applicationId>` (`upload_jks_b64`, `store_password`, `key_password`, `key_alias`),
   and builds the **release** AAB.
4. **Artifact checks.** Optional `scripts/check_android_release_artifact.py --aab ... --repo-root . --android-sdk ...
   --bundletool <pinned 1.18.3> --report ...`. A failure blocks publication, and the report is archived either way.
5. **Archive.** The AAB is archived and uploaded to Artifactory snapshots like CI.
6. **Approve** (only with `PUBLISH_TO_PLAY`; every track). The input names the package, version, commit, AAB SHA-256,
   track, status and release notes. It times out after 60 minutes.
7. **Upload.** The job reads `secret/jenkins/mobile/shared/play-service` (`json_b64`) and:
   - checks that the versionCode is greater than every bundle already in Play;
   - rechecks the approved SHA-256 and, when present, the artifact report against the commit, package and version;
   - in one Play edit, uploads that AAB, syncs `android/app/src/main/play/listings` when present, sets the track
     release with the notes, and commits.

**Listing sync.** Text comes from `listings/<language>/{title,short-description,full-description}.txt` (50/80/4000
characters). Graphics come from `listings/<language>/graphics/{icon,feature-graphic,phone-screenshots,seven-inch-screenshots,ten-inch-screenshots}/`.
A graphics directory replaces that image type with its PNG/JPEG files.

**First Play setup is manual:**

1. Create the app in Play Console.
2. Complete the store listing and app content declarations.
3. Upload the first AAB by hand if the API cannot yet.
4. Grant the shared service account access.

## Projects

| Job | Settings |
| --- | --- |
| `flutter/taiwan-bus-app/{ci,release}` | `mobile/` layout; backend tests with PostGIS, coverage floor 35; all repository hooks |
| `flutter/share-notes/{ci,release}` | branch `master` |
| `flutter/classic-calculator/{ci,release}` | 100% line and branch coverage, two connected devices, one excluded model |
| `flutter/kids-calculator/{ci,release}` | none |

These jobs replaced `flutter/flutter-android-build`, which served all four apps through one Generic Webhook job. It
had a `PROJECT` parameter and `BUILD_TARGET`, and required approval only for production. It was deleted on
2026-11-24.
