# Flutter Android Build

Jenkins job:

```text
flutter/flutter-android-build
```

This job builds Flutter Android artifacts for GitHub repositories under `maksonlee`.

## GitHub Webhook

Configure each Flutter app repository with the same webhook:

```text
Payload URL:  https://jenkins.maksonlee.com/generic-webhook-trigger/invoke?token=flutter-android-build
Content type: application/json
Events:       Just the push event
Active:       enabled
```

The Jenkins job extracts:

```text
$.repository.name -> WEBHOOK_PROJECT
$.ref             -> WEBHOOK_REF
```

Only pushes to `refs/heads/main` and `refs/heads/master` are accepted by the trigger.

## Project Rules

The GitHub repository name is used as the project slug:

```text
PROJECT = repository.name
```

The app repository is derived as:

```text
git@github.com:maksonlee/${PROJECT}.git
```

Supported Flutter layouts:

```text
pubspec.yaml
mobile/pubspec.yaml
```

The Android `applicationId` is read from:

```text
android/app/build.gradle
android/app/build.gradle.kts
```

or, for `mobile/` layout:

```text
mobile/android/app/build.gradle
mobile/android/app/build.gradle.kts
```

## Signing

Release builds read upload signing material from Vault:

```text
secret/jenkins/mobile/app/${applicationId}
```

Required Vault keys:

```text
upload_jks_b64
store_password
key_password
key_alias
```

## Google Play Publishing

First-time Play Console setup is still manual:

1. Create the app in Play Console.
2. Complete store listing and app content declarations.
3. Upload the first AAB manually if Google Play API upload is not yet available for the app.
4. Create or select the Google Play service account and grant it access to the app.

Subsequent releases can be published by this Jenkins job. The job uploads the
release AAB, applies release notes from `android/app/src/main/play/release-notes`,
and, when `android/app/src/main/play/listings` is present, syncs Play Store
listing text and graphics before committing the Play edit. The shared Play
service account JSON is read from Vault:

```text
secret/jenkins/mobile/shared/play-service
```

Required Vault key:

```text
json_b64
```

Manual release parameters:

```text
BUILD_TARGET = appbundle-release
PUBLISH_TO_PLAY = true
PLAY_TRACK = internal | closed | open | production
PLAY_RELEASE_STATUS = draft | completed | inProgress
PLAY_ROLLOUT_FRACTION = 0.05
```

Use `PLAY_RELEASE_STATUS=inProgress` only for staged rollouts and set
`PLAY_ROLLOUT_FRACTION` to a value greater than 0 and less than 1. Production
publishing asks for manual confirmation by default.

Supported listing metadata:

```text
android/app/src/main/play/listings/<language>/title.txt
android/app/src/main/play/listings/<language>/short-description.txt
android/app/src/main/play/listings/<language>/full-description.txt
android/app/src/main/play/listings/<language>/graphics/icon/
android/app/src/main/play/listings/<language>/graphics/feature-graphic/
android/app/src/main/play/listings/<language>/graphics/phone-screenshots/
android/app/src/main/play/listings/<language>/graphics/seven-inch-screenshots/
android/app/src/main/play/listings/<language>/graphics/ten-inch-screenshots/
```

When a graphics directory exists, the job replaces that image type in Play with
the local PNG/JPEG files from the directory.

## Artifactory

Artifacts are uploaded to:

```text
android-snapshots/com/maksonlee/${PROJECT}/${version}-SNAPSHOT/
```

File name format:

```text
${PROJECT}-${version}-${yyyyMMdd.HHmmss}-${BUILD_NUMBER}.${ext}
```

The version is read from `pubspec.yaml` and strips the `+build` suffix.

## Cache

The Jenkins build agent keeps shared caches under:

```text
/home/administrator/jenkins/flutter-cache
/home/administrator/jenkins/android-cache
```

Cached directories:

```text
flutter-cache/fvm
flutter-cache/pub-cache
android-cache/flutter-android-sdk
android-cache/gradle
```

Flutter SDK versions are installed and selected by FVM from:

```text
/home/administrator/jenkins/flutter-cache/fvm
```

This keeps complete Flutter SDK versions in one FVM cache root instead of manually
mounting `flutter/bin/cache`.

## Add A New Flutter Project

1. Create the GitHub repository under `maksonlee`.
2. Use one of the supported Flutter layouts.
3. Ensure Android `applicationId` is set in Gradle.
4. Add the GitHub webhook shown above.
5. For release builds, create the Vault secret at:

```text
secret/jenkins/mobile/app/${applicationId}
```

6. Create the shared Google Play service-account Vault secret if it does not already exist.
7. Run the Jenkins job with `PUBLISH_TO_PLAY=false` once to validate build and signing.
8. Run the Jenkins job with `PUBLISH_TO_PLAY=true` and `PLAY_TRACK=internal`.
9. After internal testing passes, promote through closed/open/production as needed.
