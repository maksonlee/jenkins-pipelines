# jenkins-pipelines

Jenkins Pipelines, the `jenkins-shared-lib` shared library, and the build images for the jobs on
https://jenkins.maksonlee.com. Jenkins loads everything from this repository's `main` branch.

| Path | Contents |
| --- | --- |
| `vars/` | Library steps: `androidCi`, `androidRelease`, `flutterCi`, `flutterRelease`, and the shared `gradleRelease`, `withAndroidReleaseEnv`, `buildImages`, `buildContainer` helpers |
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
python3 -B -m unittest discover -s tests -t .
python3 -B -m unittest Jenkinsfile/android/gst-stream-wall/release/test_publication_artifact_selection.py
python3 -I tools/jenkins_inventory.py --check      # read-only; uses the Jenkins API settings in ~/.bashrc
git diff --check
```

The Unity and piano-practice jobs have their own checks in their directories.
