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
| `admin/seed-jobs` | manual | Creates the folders and jobs listed in `jobs/jenkins_jobs.groovy`. |
| `admin/pipelines-ci` | polls this repository | Runs `tools/run_checks.sh`: every offline test and a Groovy syntax check. |

The folder is the GitHub repository name. A project's Jenkinsfile lives at `Jenkinsfile/<folder>/<role>/Jenkinsfile`
and only passes the project's settings to one library step. See [android.md](android.md) and [flutter.md](flutter.md).

Jobs that do not follow the layout yet:

- `unity/math-runner-*`: dedicated and recently qualified; its job names are part of its evidence.
- `web/piano-practice-*`: the origin helper accepts releases only from `web/piano-practice-main-ci`.
- `android/gst-stream-wall/release`: has the new path but keeps its own Jenkinsfile, with promotion, listing-only and
  qualification gates.
- `android/cloud-cost-widgets/release`: has the new path but keeps its own Jenkinsfile and Play tools, with
  `alpha-status`, `internal-preflight` and `internal-submit` modes. `internal-submit` accepts only a preflight build
  that ran at the same jenkins-pipelines commit, so rerun the preflight if `main` moved in between. Both bundles
  must be signed by the Vault upload key used for the build; no fingerprint is kept in this repository. The old
  `android/cloud-cost-widgets-release`, which loaded a pinned commit of an unmerged branch, was deleted on
  2026-11-25.
- `artifactory/artifactory-cleanup`: runs weekly. In `android-snapshots`, every app under `com/maksonlee` keeps its
  newest `KEEP_VERSIONS` (default 5) version folders, ordered by their newest file. `math-runner` is never touched,
  because its per-build folders are Unity release evidence, and paths in any other layout are only reported.
  Every run archives the plan as `snapshot-cleanup.json`. `DELETE` defaults to true, so scheduled runs delete the
  planned folders, which go to the Artifactory trash can first. Uncheck it to only list them.
- `backstage/homelab-backstage-main-ci`: builds the homelab-backstage image and pushes it to Harbor.

These jobs are listed in `jobs/jenkins_jobs.groovy` too, so the seed can recreate them.
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
