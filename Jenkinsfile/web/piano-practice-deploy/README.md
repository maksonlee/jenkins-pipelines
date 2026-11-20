# Piano Practice manual deployment

Create this Pipeline from SCM as `web/piano-practice-deploy`, using `Jenkinsfile/web/piano-practice-deploy/Jenkinsfile` in this repository. It runs on `ssh-agent-with-docker`. Its source is always the archived `release/` artifacts of the selected successful build of `web/piano-practice-main-ci`. Pipeline Build Step and Copy Artifact must be active, and the CI job grants the deploy job access to its artifacts. [Build step](https://www.jenkins.io/doc/pipeline/steps/pipeline-build-step/#waitforbuild-wait-for-build-to-complete) · [Copy Artifact](https://www.jenkins.io/doc/pipeline/steps/copyartifact/)

1. Choose **Build with Parameters** and enter the numeric `CI_BUILD` shown on the CI job.
2. Run `verify-only` first. It checks the selected build, validates every archived file, recalculates the release ID, and opens the extracted site in an isolated Chromium session. The build log and `candidate.json` show the target commit and release ID.
3. Enter that ID in `EXPECTED_RELEASE` and choose `stage-only` when an origin check is needed. The job transfers the exact candidate, verifies the existing rollback target and the staged files, and records a receipt. The public content link remains in place.
4. Enter the same CI build and release ID and choose `activate-production` to switch the public site. Build submission confirms this fixed candidate. Jenkins binds the origin credentials after local validation and the browser check have passed.

Each browser run uses a fresh profile. New artifacts must enter `#daily`, show the automatic F01 example, and accept two simulator key presses with separate releases. The check resumes the saved F02 round after an offline reload and verifies the progress export has separate F01 demonstration and learner records. The exact artifact from CI build #3 keeps its original lesson flow for rollback. Both flows reload a later arrangement and verify its SVG offline. Activation verifies normal HTTPS and the exact candidate with the public browser check. It records an unchanged result if the exact release was already current. A failed public check triggers rollback only while the current link still belongs to this attempt. JSON results are archived under `deploy-evidence/`.

## Origin administration

The origin for the dedicated release operation is `piano-practice-deploy@app.maksonlee.com`. Configure a folder-scoped **SSH Username with private key** credential named `piano-practice-deploy-ssh` for that account and a **Secret file** named `piano-practice-deploy-known-hosts` containing the verified host key for `app.maksonlee.com`. The deployment client uses strict checking and the supplied known-hosts file.

Install the reviewed `release_ops.py` as root-owned, executable `/usr/local/sbin/piano-practice-origin`, and `origin-ssh-entry.sh` as root-owned, executable `/usr/local/sbin/piano-practice-ssh`. Set the client's public key to use the forced command `/usr/local/sbin/piano-practice-ssh` with the SSH `restrict` option. Permit only the installed origin program with non-interactive sudo for the dedicated account. Keep the account, site root, receipt directory and installed programs under the operator's control. Existing deployments to the site should take the same exclusive lock on the site root before changing `current`.

The helper uses `/var/www/piano-practice/{releases,recovery,current}`, with a verified current release and previous checksum inventory. Per-attempt receipts are `recovery/jenkins-<attempt>.json`. The staging record identifies the previous symlink. The activation record identifies the replacement symlink before switching, so the failure cleanup can recover after an interrupted command. If a job loses its worker after an activation request, inspect the attempt receipt and run the helper's constrained `rollback <attempt>` with the authorized credential while the failed attempt still owns `current`. The origin keeps the releases and checksums for recovery.

## Local checks

```sh
python3 -B -m unittest discover -s Jenkinsfile/web/piano-practice-deploy -p 'test_*.py'
bash -n Jenkinsfile/web/piano-practice-deploy/origin.sh
bash -n Jenkinsfile/web/piano-practice-deploy/origin-ssh-entry.sh
```

Run the origin transaction tests on Linux for the production symlink and locking behavior. To smoke-test a real artifact locally, follow the `verify-only` mode, which uses the Dockerfile of the CI job and the Playwright version locked in this directory.
