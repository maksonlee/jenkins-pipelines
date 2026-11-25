# jenkins-pipelines

Jenkins Pipelines, the `jenkins-shared-lib` shared library, and the build images for the jobs on
https://jenkins.maksonlee.com. Jenkins loads everything from this repository's `main` branch.

| Path | Contents |
| --- | --- |
| `vars/` | Library steps: `androidCi`, `androidRelease`, `flutterCi`, `flutterRelease`, `staticSiteDeploy`, and the helpers they and the jobs with their own Jenkinsfile share (`gradleRelease`, `withAndroidReleaseEnv`, `androidBundle`, `playUpload`, `checkoutReleaseCommit`, `jsonField`, `buildImages`, `buildContainer`) |
| `resources/` | Shell and Python scripts the steps load with `libraryResource` |
| `Jenkinsfile/<folder>/<job>/Jenkinsfile` | One file per Jenkins job, at the job's path |
| `jobs/jenkins_jobs.groovy` | Job DSL list of per-project folders and jobs, applied by `admin/seed-jobs` |
| `docker/` | Dockerfiles of the images pinned in `vars/buildImages.groovy` |
| `tests/` | Offline tests for the library scripts and the job list |
| `tools/jenkins_inventory.py` | Read-only inventory of the live jobs and drift check against `jobs/` |

Read [docs/jobs.md](docs/jobs.md) for the job layout and standards, [docs/android.md](docs/android.md) and
[docs/flutter.md](docs/flutter.md) for the project contracts, and [docker/README.md](docker/README.md) for the images.

## Checks

```bash
tools/run_checks.sh                                # every offline test and a Groovy syntax check (needs Docker)
python3 -I tools/jenkins_inventory.py --check      # read-only; uses the Jenkins API settings in ~/.bashrc
```

`admin/pipelines-ci` runs `tools/run_checks.sh` after every push to `main`.
