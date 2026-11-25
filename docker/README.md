# Build images

Jobs reference images through `vars/buildImages.groovy`, pinned by digest, so a rebuilt or re-pulled tag cannot
change CI without a commit. To move jobs to a rebuilt image:

1. Change the Dockerfile here.
2. Build it on the Linux amd64 agent and push it to `harbor.maksonlee.com/library/`.
3. Read the digest back from Harbor and update `buildImages.groovy`.

Never point a job at a mutable tag.

| `buildImages` | Image | Source | Base | Used by |
| --- | --- | --- | --- | --- |
| `android()` | `android-build-env:jdk25-20261029` | `android-build-env/` | `ubuntu:24.04` by digest; Temurin 25 and 21, cmdline-tools by checksum | `androidCi`, `androidRelease` (through `gradleRelease`) |
| `flutter()` | `flutter-android-build-env:jdk25-20261029` | `flutter-android-build-env/` | `android()` digest + Dart, FVM | `flutterCi`, `flutterRelease` |
| `gstStreamWall()` | `gst-stream-wall-android-build-env:1.98.0-3` | `gst-stream-wall-android-build-env/` | `android()` digest + Rust 1.98.0 with the Android target, native build tools, bundletool by checksum | `android/gst-stream-wall/{ci,release}` |
| `unityAndroid()` | `unity-android-build-env:6000.6.7f1-android-1` | `unity-android-build-env/` | GameCI editor by digest | `unity/math-runner-*` (the digest is also bound into the Unity worker seal) |
| `playwright()` | `mcr.microsoft.com/playwright:v1.65.1-noble` by digest | Microsoft | — | `web/readerplay/ci` |
| `python()` | `python:3.13-bookworm` by digest | Docker Hub | — | Flutter backend tests and Play metadata validators |
| `postgis()` | `postgis/postgis:18-3.6` by digest | Docker Hub | — | Flutter backend tests with `postgresDatabase` |

**Images that do not come from `buildImages`:**

- `Jenkinsfile/web/piano-practice/ci/Dockerfile` (Node 24 + Playwright, both bases by digest) is built on every
  run by the piano CI and deploy jobs.
- `Jenkinsfile/web/hongyu-portal/ci/Dockerfile` (the Playwright base plus PHP 8.3 and Composer, bases by digest) is
  built on every run by the hongyu-portal CI. Its Playwright browsers are cached in
  `/home/administrator/jenkins/web-cache/ms-playwright`.
- `Jenkinsfile/web/veil-backend/ci/Dockerfile` (Rust 1.98.0 by digest plus Clippy and the libraries MariaDB needs)
  is built on every run by the veil-backend CI. Its cargo registry is cached in
  `/home/administrator/jenkins/rust-cache/veil-backend`, and its pinned MariaDB and Vault test inputs are in
  `/home/administrator/jenkins/veil-test-inputs`.
- `Jenkinsfile/android/veil-android/ci/Dockerfile` (`android()` by digest plus Rust 1.98.0, four Android targets and
  a host C toolchain) is built on every run by the veil-android CI. Its cargo cache is
  `/home/administrator/jenkins/android-cache/rust/veil-android`.
- `Jenkinsfile/android/termstay/ci/Dockerfile` (Ubuntu 26.04 by digest plus the host packages termstay documents) is
  built on every run by the termstay CI. The repository's own toolchains are cached in
  `/home/administrator/jenkins/termstay-cache/toolchains`.
- The `aosp-builder` images were removed from the agent with the AOSP jobs on 2026-11-25.

The gst image moved from the legacy 2025 base (SDKMAN JDKs, Gradle on Java 21) to `android()` on 2026-11-25, so
every Android image now shares one base and Gradle runs on Temurin 25. Qualified the same day: `ci` #3 passed
(139 tests), and `release` #6, without publishing, rebuilt the tagged `android-v0.1.0-alpha.23` commit into an AAB
byte-identical to the one `release` #4 built on the old image. `1.98.0-2` stays in Harbor for a rollback.

**Agent disk.** Old tags that no job referenced (`flutter-android-build-env:3.44.0`, `:3.44.0-fvm` and
`gst-stream-wall-android-build-env:1.98.0-1`) and unused dangling images were removed on 2026-11-24. Before removing
an image, check `docker system df` and `docker ps -a`: other projects share this Docker daemon, and
`docker image prune` keeps a dangling image that a stopped container still uses. No job uses the legacy
`cdlee/android-build-env` / `android-build-env:latest` or `gst-stream-wall-android-build-env:1.98.0-2` any more;
they are still on the agent (about 5.6 GB together) until the owner approves removing them.
