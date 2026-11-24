// Job DSL definitions for the per-project Jenkins jobs, applied by admin/seed-jobs with ignoreExisting: true.
//
// Each entry creates the project folder and one Pipeline-from-SCM job per role. Every job loads
// Jenkinsfile/<folder>/<role>/Jenkinsfile from this repository's main branch with a lightweight checkout; the
// Jenkinsfile owns parameters, triggers, options and retention. The seed only creates missing folders and jobs and
// never updates existing ones, because a Job DSL update replaces config.xml and would drop the Jenkinsfile's
// parameters and triggers until the next build. Never declare the top-level folders (android, flutter, web, ...):
// they hold folder-scoped credentials that a regenerated config would delete. Removing an entry never deletes a job.
// See docs/jobs.md.

def projects = [
    [folder: 'android/beepbeep', roles: ['ci', 'release'], repo: 'beepbeep', type: 'android'],
    [folder: 'android/bluetoothfinder', roles: ['ci', 'release'], repo: 'bluetoothfinder', type: 'android'],
    [folder: 'android/float-level', roles: ['ci', 'release'], repo: 'float-level', type: 'android'],
    [folder: 'android/headshot-maker-android', roles: ['ci', 'release'], repo: 'headshot-maker-android', type: 'android'],
    [folder: 'android/taiwandrive', roles: ['ci', 'release'], repo: 'taiwandrive', type: 'android'],
    [folder: 'android/gst-stream-wall', roles: ['release'], repo: 'gst-stream-wall', type: 'android'],
    [folder: 'android/learn-chess', roles: ['ci'], repo: 'learn-chess', type: 'android'],
    [folder: 'android/cloud-cost-widgets', roles: ['ci', 'release'], repo: 'cloud-cost-widgets', type: 'android'],
    [folder: 'flutter/taiwan-bus-app', roles: ['ci', 'release'], repo: 'taiwan-bus-app', type: 'flutter'],
    [folder: 'flutter/share-notes', roles: ['ci', 'release'], repo: 'share-notes', type: 'flutter'],
    [folder: 'flutter/classic-calculator', roles: ['ci', 'release'], repo: 'classic-calculator', type: 'flutter'],
    [folder: 'flutter/kids-calculator', roles: ['ci', 'release'], repo: 'kids-calculator', type: 'flutter'],
]

def descriptions = [
    'android/ci'    : 'Debug CI for maksonlee/%s: unit tests, debug lint, debug and instrumentation APKs with SHA256SUMS. Triggered by GitHub pushes; uses no release signing material.',
    'android/release': 'Manual Google Play release for maksonlee/%s: release-branch commit only, tests and lint, signed AAB verification, then a manual approval before the verified AAB is uploaded.',
    'flutter/ci'    : 'CI for maksonlee/%s: quality gates and a debug AAB uploaded to Artifactory snapshots. Triggered by GitHub pushes; uses no release signing material.',
    'flutter/release': 'Manual Google Play release for maksonlee/%s: release-branch commit only, quality gates, signed release AAB, then a manual approval before any Play access.',
]

// Jobs with their own Jenkinsfile instead of androidRelease or flutterRelease.
def jobDescriptions = [
    'android/cloud-cost-widgets/release': 'Manual Google Play release for maksonlee/cloud-cost-widgets with its own Jenkinsfile: alpha-status reads the Play state, internal-preflight builds and verifies the signed phone and Wear OS bundles, and internal-submit uploads the bundles of an approved preflight build to Internal testing.',
]

projects.each { project ->
    folder(project.folder) {
        description("Jenkins jobs for maksonlee/${project.repo}.")
    }
    project.roles.each { role ->
        pipelineJob("${project.folder}/${role}") {
            description(jobDescriptions["${project.folder}/${role}"] ?: String.format(descriptions["${project.type}/${role}"], project.repo))
            definition {
                cpsScm {
                    scm {
                        git {
                            remote {
                                url('git@github.com:maksonlee/jenkins-pipelines.git')
                                credentials('vault-github-ssh')
                            }
                            branch('*/main')
                        }
                    }
                    scriptPath("Jenkinsfile/${project.folder}/${role}/Jenkinsfile")
                    lightweight(true)
                }
            }
        }
    }
}
