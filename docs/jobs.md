# Jenkins job layout

Every product gets its own folder with standard jobs. Shared behavior lives in the `jenkins-shared-lib` library
(`vars/` steps and `resources/` scripts), not in a job shared by several projects. Each project's history, triggers,
artifacts, retention and approvals therefore stay separate, and a library change can be rolled out project by
project.

| Job | Trigger | Purpose |
| --- | --- | --- |
| `android/<repo>/ci` | GitHub push | `androidCi`: unit tests, debug lint, debug and instrumentation APKs. No signing material. |
| `android/<repo>/release` | manual | `androidRelease`: tests, signed AAB, verification, approval, Google Play upload. |
| `flutter/<repo>/ci` | GitHub push | `flutterCi`: quality gates and a debug AAB uploaded to Artifactory snapshots. |
| `flutter/<repo>/release` | manual | `flutterRelease`: quality gates, signed AAB, approval, Google Play upload. |
| `web/<repo>/ci` | GitHub push | The project's own Jenkinsfile: its tests in a container without network access. |
| `web/<repo>/deploy` | manual | `staticSiteDeploy` for a static site, or the project's own Jenkinsfile: verify-only, stage-only, activate-production. |
| `admin/seed-jobs` | manual | Creates the folders and jobs listed in `jobs/jenkins_jobs.groovy`. |
| `admin/pipelines-ci` | polls this repository | Runs `tools/run_checks.sh`: every offline test and a Groovy syntax check. |

The folder is the GitHub repository name. A project's Jenkinsfile lives at `Jenkinsfile/<folder>/<role>/Jenkinsfile`
and only passes the project's settings to one library step. See [android.md](android.md) and [flutter.md](flutter.md).

Jobs that do not follow the layout yet:

- `unity/math-runner-*`: dedicated and recently qualified; its job names are part of its evidence.
- `web/piano-practice/{ci,deploy}`: their own Jenkinsfiles; the deploy job takes the CI job and build to deploy and
  checks the release at the origin. The former `web/piano-practice-main-ci` and `web/piano-practice-deploy` were
  deleted on 2026-11-25. Releases the former CI job built stay on the origin and the origin helper still accepts
  them, but they can no longer be staged again from Jenkins.
- `android/gst-stream-wall/release`: has the new path but keeps its own Jenkinsfile, with promotion, listing-only and
  qualification gates.
- `android/cloud-cost-widgets/release`: has the new path but keeps its own Jenkinsfile and Play tools, with
  `alpha-status`, `internal-preflight`, `internal-submit` and `production-submit` modes. `internal-submit` takes the number of a preflight build that ran at the same
  jenkins-pipelines commit (rerun the preflight if `main` moved in between), reads that build's AAB SHA-256 from its
  archive and asks for approval before any Play access. The status read uses Play's release
  lifecycle method, which Play limits far below the documented API quota: after about a dozen reads of the app
  within a few minutes it answers 403 "Listing releases quota exceeded." for up to half an hour. A run therefore
  reads only the three tracks that hold a release, and a limited read does not block a submission: before any
  write the publisher compares the exact track inventory of a fresh Play edit, and the commit fails if changes
  are in review. Do not rerun the preflight repeatedly. `production-submit` builds and uploads nothing: it reads
  the status, proves in a deleted edit that Play holds the bundles named in `play_promote.py` with the digests the
  earlier preflight builds archived, asks for approval with that text, then stages both Production tracks and
  commits them. With managed publishing off Play sends the commit for review itself and rejects
  `changesNotSentForReview=true`. A rejected commit records only the name of a known cause from Play's message. Bundles
  must be signed by the Vault upload key used for the build; no fingerprint is kept in this repository. The old
  `android/cloud-cost-widgets-release`, which loaded a pinned commit of an unmerged branch, was deleted on
  2026-11-25.
- `artifactory/artifactory-cleanup`: runs weekly. In `android-snapshots`, every app under `com/maksonlee` keeps its
  newest `KEEP_VERSIONS` (default 5) version folders, ordered by their newest file. `math-runner` is never touched,
  because its per-build folders are Unity release evidence, and paths in any other layout are only reported.
  Every run archives the plan as `snapshot-cleanup.json`. `DELETE` defaults to true, so scheduled runs delete the
  planned folders, which go to the Artifactory trash can first. Uncheck it to only list them.
- `web/hongyu-portal/deploy`: its own Jenkinsfile and origin helper (`release_ops.py`, sent over SSH on every run).
  It builds the release from a commit on `main` with production dependencies and a security audit. `verify-only`
  stops there and reports what is live; `stage-only` also unpacks the release beside the live one, builds
  Laravel's caches and runs `nginx -t`; `activate-production` asks for approval, renames the `current` link, reloads
  PHP-FPM, checks the public site and renames the link back if a check fails. It logs in with the agent's own SSH key.
- `web/share-notes/deploy` and `web/readerplay/deploy`: the `staticSiteDeploy` step, for a site that is static
  files in one directory. A Jenkinsfile passes the site name, repository, public URL, where the files come from and
  the public checks; the pipeline is `vars/staticSiteDeploy.groovy` with `resources/web/static-site-deploy.sh`, and
  the origin helper is `resources/web/static_site_release.py`, which knows each site's directories by its name.
  `verify-only` builds the release from a commit on the branch and reports what is live; `stage-only` also unpacks
  it beside the site; `activate-production` asks for approval, copies the live files aside, syncs the release in by
  content, checks the public site (`statusChecks`: HTTP status per path; `sameFiles`: public bytes equal to the
  built file) and syncs the copy back if a check fails. nginx is not managed.
  - share-notes (`flutterWeb: true`, branch `master`): `resources/web/flutter-web-build.sh` runs the Flutter tests
    and builds the web app in the Flutter image, with the Flutter version from `.fvmrc` and the build identity from
    the repository's `tool/build_flutter.py`. The browser tests in `tool/device/` are not run. The Android app
    keeps `flutter/share-notes/{ci,release}`.
  - readerplay (`directory: 'public'`): no build; it packages `public/` (the tests run in `web/readerplay/ci`).
    Staged releases are kept in `/var/www/html/readerplay-releases`, and the deployed files become `root:root`.
  A new static site needs an entry in `SITES` of the origin helper and a Jenkinsfile.
- `web/veil-backend/deploy`: its own Jenkinsfile, image (the CI toolchain plus the aarch64 cross-compiler) and
  origin helper. `verify-only` cross-builds the release binary from a commit on `main`, checks the glibc version
  it needs and reports what is live. `stage-only` also copies it to the origin as a non-executable file; a staged
  binary is never started, because the server applies its migrations when it starts. `activate-production` takes
  the staged release named by `EXPECTED_RELEASE` without rebuilding, asks for approval (the message says which
  migrations it adds), then runs the database backup unit, installs the binary, restarts the service and checks
  health on loopback and in public. An unhealthy start puts the previous binary back; migrations are not undone.
  nginx, the public pages, the configuration and Vault are not managed.
- `flutter/taiwan-bus-app/backend-deploy`: runs the repository's own `deploy/home-osm/deploy.sh` from a clean
  checkout of a commit on `main`, with the agent's SSH key. `verify-only` checks the HAProxy edge contract, runs
  the backend tests in a fresh virtual environment and compares the commit with the host's `DEPLOYED_REVISION`.
  `deploy-production` then asks for approval and runs the script, which stops the backend for about a minute,
  applies migrations and rolls back by itself. There is no stage-only mode, because the script has none.
- **Old staged releases.** `web/hongyu-portal/deploy`, `web/share-notes/deploy`, `web/readerplay/deploy` and
  `web/veil-backend/deploy` prune after every staging and activation: besides the live release and the one just
  staged or activated (for hongyu also the release that holds `.env` and the rollback target), the newest five
  stay and older ones are removed. The result lists them as `pruned`. piano-practice and taiwan-bus keep their own
  rules.
- `backstage/homelab-backstage-main-ci`: builds the homelab-backstage image and pushes it to Harbor.

These jobs are listed in `jobs/jenkins_jobs.groovy` too, so the seed can recreate them.

### IP Location

The `web/ip-location/ci` job checks the locked Rust workspace and runs the frontend browser
fixtures. It fetches dependencies in its project container before running checks with container
networking disabled. The `web/ip-location/release` job is manual. It runs the app's
`scripts/build-release.sh`, archives and fingerprints the Linux arm64 bundle and its checksum,
and grants archive access to the matching deploy job. `GIT_REF` accepts `main` or a full commit
SHA already on `main`.

Both jobs check out `maksonlee/ip-location` with the existing `vault-github-ssh` credential and
Git host-key verification. Prime CI once after seeding, then route the private app's push webhook
to Jenkins using the normal GitHub plugin setup. The jobs use the `ssh-agent-with-docker` agent,
a dedicated pinned build image, and dependency registries for setup.

`web/ip-location/deploy` takes `RELEASE_BUILD`, `COMMIT`, `ARCHIVE_SHA256` and `VERSION` from the
reviewed successful release. It checks the ARM64 package, its checksum sidecar and release
metadata in every mode. It archives the candidate identity and pipeline commit. The two
origin modes prompt for approval without using an executor. The approval shows the target
hostname and exact bundle identity. See the [IP Location origin runbook](../Jenkinsfile/web/ip-location/deploy/README.md).

## Helpers for jobs with their own Jenkinsfile

A job that cannot use one of the pipeline steps still takes the common parts from the library instead of copying
them (`@Library('jenkins-shared-lib') _` at the top):

| Step | Use |
| --- | --- |
| `checkoutReleaseCommit(repo:, branch:, gitRef:)` | Clones the project into `source` and checks out the branch tip or a full SHA already on the branch; returns the commit. Every manual release and deploy job with its own Jenkinsfile uses it. |
| `jsonField(file, key)` | One field of a JSON file as text (this Jenkins has no `readJSON`). Check the value with a regex before using it. |
| `androidBundle.verify(aab:, packageName:, ...)` | `verify-aab.sh` with the pinned bundletool; returns the version, signer and SHA-256 of the bundle. Used by `androidRelease`, veil-android and termstay. |
| `playUpload(...)` | The version preflight and the one-edit upload with the tools in `resources/play`; the only place those jobs read the Play service account, always after the approval. Used by `flutterRelease`, `androidRelease` and veil-android. |
| `gradleRelease(task, cfg)` | Gradle in a build image with the Android cache; also used without signing by the gst-stream-wall and veil-android jobs. |

Left separate on purpose: the four `web/<repo>/ci` jobs and the gst-stream-wall, veil-android and termstay CI jobs
share only their options block, and the hongyu-portal, veil-backend and piano-practice deployments have their own
release formats and origin helpers.
- `aosp/aosp-build-in-docker`, `build-prj-0001`, `k8s-dynamic-agent-test` and
  `monitoring/taiwan-bus-production-health` were deleted on 2026-11-25, together with the Gerrit and Kubernetes
  credentials, cloud and plugins they alone used.

## Standards

- Job definition: Pipeline from SCM, `git@github.com:maksonlee/jenkins-pipelines.git` with `vault-github-ssh`,
  branch `*/main`, lightweight checkout. Product checkouts also use `vault-github-ssh`.
- Push trigger: the GitHub plugin (`githubPush()`). Point the repository webhook at
  `https://jenkins.maksonlee.com/github-webhook/` for push events. A job is matched once one of its builds has
  checked out that repository.
- Options set by the library steps:
  - every job: `timestamps`, `disableConcurrentBuilds`, `skipDefaultCheckout`;
  - ci: one timeout per build, and keeps 30 builds with artifacts for 10;
  - release: stage timeouts plus a 60-minute approval timeout, so that a forgotten input does not hold the
    agent's only executor; keeps 50 builds with artifacts for 20.
- Release: `GIT_REF` is the release branch or a full commit SHA contained in it. Every Google Play write waits for a
  manual input that names the commit, version, AAB SHA-256, track and status. Play credentials are read only after
  that input.

## Creating jobs

`jobs/jenkins_jobs.groovy` is applied by `admin/seed-jobs` with `ignoreExisting: true`:

- The seed creates only missing folders and jobs and never updates or deletes existing ones. A Job DSL update
  replaces `config.xml`. That would drop the parameters and triggers a Jenkinsfile applied, until the next build, and
  would delete folder-scoped credentials if a top-level folder were regenerated.
- Declarative parameters and triggers appear after a job's first build. Start each new job once by hand. A release
  job's first build uses `PUBLISH_TO_PLAY=false`, so it only verifies.
- After changing `jobs/jenkins_jobs.groovy`, run the seed once: it stops with "script not yet approved".
  Compare the pending script with the reviewed file, approve exactly that hash under **Manage Jenkins →
  In-process Script Approval**, and run the seed again.
  - The page's Approve button uses a session-bound Stapler proxy, which rejects API-token requests. From the API,
    approve through the script console:
    `ScriptApproval.get().approveScript('<SHA512:...>')`.
- `Jenkinsfile/admin/seed-jobs/job-config.xml` is the configuration the seed job was created with. To recreate
  it: create the `admin` folder, then post this file to `/job/admin/createItem?name=seed-jobs`.
- To change an existing job's definition, edit it in Jenkins and keep `jobs/jenkins_jobs.groovy` in step.
- **Rebuilding Jenkins.** Create the top-level folders (`admin`, `android`, `flutter`, `unity`, `web`, `backstage`,
  `artifactory`) and the credentials, recreate the seed job as above, and run it: it creates every other job.
  Each job gets its parameters and triggers from its first build. `admin/pipelines-ci` polls instead of using a
  webhook, because a push event for this repository would reach every `githubPush()` job that loads from it.
  `python3 tools/jenkins_inventory.py --check` reports any job whose SCM, branch, script path or lightweight flag
  differs from the file, and any job that is missing.

## Adding a project

1. Add `Jenkinsfile/<type>/<repo>/<role>/Jenkinsfile` calling the step for that type.
2. Add the project to `jobs/jenkins_jobs.groovy`, then run `python3 -B -m unittest discover -s tests -t .`.
3. Merge to `main`, approve the script if asked, run `admin/seed-jobs`, then build each new job once.
4. Add the GitHub webhook. For a release:
   - A native app is signed with the shared `secret/jenkins/mobile/shared/upload-key`, so register that upload key in Play. To
     also compare its signer with the `certificate_sha256` there, set `verifyUploadCertificate: true`.
   - A Flutter app needs its own `secret/jenkins/mobile/app/<applicationId>`.

## Migrating a project from an old job

Run the new jobs next to the old ones:

1. Build the new `ci` once, and the new `release` with `PUBLISH_TO_PLAY=false`.
2. Disable the old jobs as soon as the new `ci` has succeeded, so that one push does not build twice. Point
   webhooks that used the Generic Webhook Trigger at `/github-webhook/`.
3. Disabled jobs keep their history and URLs. Re-enable one to roll back. Delete them only after an agreed
   observation period and with the owner's approval. The jobs replaced in 2026-11 were deleted on 2026-11-24, and
   their Jenkinsfiles were removed from `Jenkinsfile/`; they remain in git history.
