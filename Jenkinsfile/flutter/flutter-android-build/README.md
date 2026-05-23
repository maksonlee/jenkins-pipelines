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

Only pushes to `refs/heads/main` are accepted by the trigger.

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

The Jenkins build agent keeps per-project caches under:

```text
/home/administrator/jenkins/flutter-cache/${PROJECT}
```

Cached directories:

```text
android-sdk
flutter-bin-cache
gradle
pub-cache
```

`flutter-bin-cache` is mounted to:

```text
/home/ubuntu/flutter/bin/cache
```

This avoids repeatedly downloading Flutter Android engine artifacts.

## Add A New Flutter Project

1. Create the GitHub repository under `maksonlee`.
2. Use one of the supported Flutter layouts.
3. Ensure Android `applicationId` is set in Gradle.
4. Add the GitHub webhook shown above.
5. For release builds, create the Vault secret at:

```text
secret/jenkins/mobile/app/${applicationId}
```

6. Run the Jenkins job manually once if you want to validate before relying on webhook triggers.
