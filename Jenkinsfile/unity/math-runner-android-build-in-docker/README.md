# Math Runner Android release AAB

Jenkins job: `unity/math-runner-android-build-in-docker` (currently disabled).

The Pipeline definition is loaded from `git@github.com:maksonlee/jenkins-pipelines.git` on `main`, with script path `Jenkinsfile/unity/math-runner-android-build-in-docker/Jenkinsfile`. The reviewed disabled job configuration is in `job-config.disabled.xml`. The candidate Jenkinsfile and Math Runner source must be committed and made available to Jenkins before a live run. Do not infer a Unity build from the Declarative linter result.

The job builds, verifies, archives and fingerprints one signed Android App Bundle. It does not activate or return a Personal license during a normal build, receive a Unity email/password, or upload to Google Play. A later Play job must consume the archived AAB and SHA-256, require separate authorization, and verify its own Play service-account access.

## Candidate and release inputs

- `GIT_REF`: `main` or a full SHA contained in `origin/main`. The product checkout records its exact commit and must be clean.
- `VERSION_NAME`: 1–64 ASCII characters matching `[0-9A-Za-z][0-9A-Za-z._+-]*`.
- `ANDROID_VERSION_CODE`: 1–2100000000.
- Package and output: `com.maksonlee.mathrunner`, `Builds/Release/Android/SportsDayMath.aab`.
- Product method: `MathRunner.Editor.ProjectBuilder.BuildAndroidPlayAab` using Unity 6000.6.7f1.
- Unity image: `harbor.maksonlee.com/library/unity-android-build-env@sha256:c45b47f9999a81df26f664419725ab5895438e5dbf6123bc002c766ecf3c4cf3`.
- Unity CLI for authentication and license health only: official `1.0.0-beta.17` Linux x64 binary, SHA-256 `2f5246ee5bea5032da2917f5f0125d3cfe803c85dc34f78bd75a427db13a0b3c`, installed outside the worker HOME at `/home/administrator/jenkins/unity-cli/1.0.0-beta.17/unity`. The job verifies its bytes and mounts it read-only. There is no per-build download or self-update.
- Android upload signing: Vault KV v2 `secret/jenkins/mobile/app/com.maksonlee.mathrunner` with `upload_jks_b64`, `store_password`, `key_password`, `key_alias`. The JKS and three UTF-8 secret files are created under this build's `${WORKSPACE}@tmp` directory, mounted read-only at `/run/mathrunner-signing`, and deleted after the container exits. Only their paths reach Unity; `keytool -alias` uses the alias to identify the public certificate. Neither signing files nor their source variables are placed in the persistent Unity HOME.
- Host: dedicated `ssh-agent-with-docker` worker with one executor, UID/GID 1000, Docker, Java/keytool/jarsigner, Python 3, and pinned bundletool 1.18.3 at `/home/administrator/jenkins/android-cache/tools/bundletool-all-1.18.3.jar` (SHA-256 `a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29`).

## Persistent Personal worker

`bootstrap-unity-personal.sh init` creates a dedicated `/home/administrator/jenkins/unity/math-runner-worker-1` with mode 0700, fixed machine-id, UID/GID passwd/group entries from the pinned image, persistent `home` and `share` directories, and a worker lock. It refuses to regenerate an existing identity. Every bootstrap and build container uses UID/GID 1000, hostname `mathrunner-unity-worker-1`, the same `/etc/machine-id`, `/home/ubuntu` HOME, and `/usr/share/unity3d`. The host administrator HOME, Windows license files, and project workspace are not used as license state.

Only an interactive, authorized `bootstrap-unity-personal.sh login` invokes official `unity auth login`; a human completes OAuth. The observed beta.17 login URL uses Unity's remote app-linking channel and the container did not open a local listening socket, so no SSH callback tunnel was needed in the bounded pre-login probe. Completion still requires a real user sign-in. `activate` invokes `unity license activate --personal --accept-eula` once after login. `status` checks `loggedIn`, `active`, and `signedIn` without printing account data. `verify` runs the Editor in two distinct, fresh containers with the same state and a disposable empty Unity project; it retains protected input, container-ID, exit-code and Editor-log evidence under the worker's `verification` directory, including the first failure. Only after both Editor exits and license checks succeed does it write the persistence proof. `seal` requires that proof and writes a ready marker bound to the complete fixed identity files, hostname, image digest and CLI hash. Sealed state rejects another login or activation. A normal Jenkins job requires matching proof/ready, identity paths/modes, and CLI auth/license status; otherwise it fails before Editor execution. The CLI's newer Licensing Client may have a different version from the Editor-embedded client, so record both when activation is actually tested.

The worker lock covers the whole Unity build container lifetime. Each build uses a fresh container with at most 6 CPUs, 12 GiB memory and 2 GiB shared memory. The Docker client does not inherit the lock descriptor, so a cancelled shell cannot leave it held while the container runs. Before launching, the job refuses any existing container with this worker label or a previous Math Runner signing directory. On abort, post-cleanup takes the same lock, verifies exact container name, labels and mounts, and stops only that build's container. It removes only the build's temporary signing directory and checkout. It never deletes, returns, or re-activates the persistent Personal state. A cleanup failure preserves evidence and fails the job.

## Release checks

Archival requires all of these:

1. The fixed AAB and Unity build report exist, with exactly one AAB in the release directory.
2. Pinned bundletool validates the bundle and dumps its base manifest.
3. Manifest package, versionName, and versionCode match the candidate.
4. The base module contains ARM64 Unity and IL2CPP libraries and no other native ABI.
5. Strict JAR signature verification rejects unsigned or tampered entries; only the expected self-signed upload-certificate warning is tolerated.
6. The signer certificate SHA-256 matches the public fingerprint derived from the runtime JKS.
7. The artifact SHA-256 and `release-verification.json` bind the AAB to the source commit, image digest, Unity CLI version, worker identity, package/version, ABI and signer.

Jenkins archives only the AAB, checksum, public signer fingerprint, manifest, signature report, release report and Unity packed-asset build report. The exact Jenkins pipeline SCM revision and global shared-library revision must be recorded from the first live build log separately from the product SHA.

The job remains disabled and its SCM still points to `main`; current uncommitted files cannot be executed by Jenkins. No Unity OAuth, Personal activation, signed AAB, Jenkins run or Play publication is implied by static checks.

## Candidate checks

From the `jenkins-pipelines` root on the Linux worker:

```bash
candidate=Jenkinsfile/unity/math-runner-android-build-in-docker
python3 "$candidate/tests/validate.py" "$candidate/Jenkinsfile" "$candidate/bootstrap-unity-personal.sh"
python3 "$candidate/tests/contract_test.py" "$candidate/Jenkinsfile"
python3 "$candidate/tests/jenkins_lint.py" "$candidate/Jenkinsfile"
xmllint --noout "$candidate/job-config.disabled.xml"
git diff --check
```

The fake-Docker contract test uses only synthetic signing strings, a disposable test tree, and stub executables. The Jenkins Declarative linter sends the candidate to Jenkins' read-only validation endpoint using literal API assignments from this worker's `~/.bashrc` without printing them. Neither check starts a Unity Editor or a Jenkins build.
