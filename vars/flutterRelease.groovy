// Manual Google Play release for one Flutter app, called from a project's thin Jenkinsfile:
//
//     flutterRelease(repo: 'taiwan-bus-app', backendTests: [coverageFloor: 35, postgresDatabase: 'taiwan_bus_test'])
//
// GIT_REF must be the release branch or a full commit SHA already contained in it. The job loads the release notes
// for the selected track, runs the same quality gates as flutterCi, builds and signs the release AAB, runs the
// optional scripts/check_android_release_artifact.py, archives it and uploads it to Artifactory. Only with
// PUBLISH_TO_PLAY and after a manual input naming the exact commit, version, SHA-256, track and status does it read
// the Play service account, check that the versionCode is new and upload that same AAB. See docs/flutter.md.

def call(Map cfg) {
    def config = flutterAndroid.config(cfg, 'flutterRelease')

    pipeline {
        agent none

        options {
            timestamps()
            disableConcurrentBuilds()
            skipDefaultCheckout()
            // Stage timeouts instead of one build timeout: the approval wait has its own limit so that a
            // forgotten input does not hold the only executor indefinitely.
            buildDiscarder(logRotator(numToKeepStr: '50', artifactNumToKeepStr: '20'))
        }

        parameters {
            string(name: 'GIT_REF', defaultValue: config.branch, trim: true,
                description: "${config.branch} or a full commit SHA already contained in origin/${config.branch}")
            booleanParam(name: 'PUBLISH_TO_PLAY', defaultValue: false,
                description: 'Upload the release AAB to Google Play after a manual approval')
            choice(name: 'PLAY_TRACK', choices: config.tracks, description: 'Google Play API track ID')
            choice(name: 'PLAY_RELEASE_STATUS', choices: ['completed', 'draft', 'inProgress'],
                description: 'Google Play release status; inProgress is a staged rollout')
            string(name: 'PLAY_ROLLOUT_FRACTION', defaultValue: '', trim: true,
                description: 'Required for inProgress, greater than 0 and less than 1, for example 0.05')
        }

        stages {
            stage('Build and verify') {
                agent { label 'ssh-agent-with-docker' }
                stages {
                    stage('Resolve release') {
                        steps {
                            script {
                                def gitRef = params.GIT_REF?.trim()
                                if (!gitRef || (gitRef != config.branch && !(gitRef ==~ /^[0-9a-fA-F]{40}$/))) {
                                    error "GIT_REF must be ${config.branch} or a full 40-character commit SHA"
                                }
                                if (!(params.PLAY_TRACK in config.tracks)) {
                                    error 'PLAY_TRACK is not offered for this project'
                                }
                                def fraction = params.PLAY_ROLLOUT_FRACTION?.trim() ?: ''
                                if (params.PLAY_RELEASE_STATUS == 'inProgress') {
                                    if (!(fraction ==~ /^0?\.[0-9]+$/) || fraction.toBigDecimal() <= 0) {
                                        error 'PLAY_ROLLOUT_FRACTION must be greater than 0 and less than 1 for inProgress'
                                    }
                                } else if (fraction) {
                                    error 'PLAY_ROLLOUT_FRACTION is only used with PLAY_RELEASE_STATUS=inProgress'
                                }
                                env.GIT_REF = gitRef
                            }
                        }
                    }

                    stage('Fetch code') {
                        steps {
                            deleteDir()
                            script {
                                flutterAndroid.setEnvironment(config)
                            }
                            dir('source') {
                                checkout(changelog: false, poll: false, scm: [
                                    $class: 'GitSCM',
                                    branches: [[name: env.GIT_REF]],
                                    extensions: [],
                                    userRemoteConfigs: [[credentialsId: 'vault-github-ssh', url: env.PROJECT_REPO]]
                                ])
                                script {
                                    withEnv(["RELEASE_BRANCH=${config.branch}", 'RELEASE_FORBIDDEN_PATHS=']) {
                                        env.RELEASE_COMMIT = sh(returnStdout: true,
                                            script: libraryResource('android/release/resolve-commit.sh')).trim()
                                    }
                                }
                            }
                            script {
                                flutterAndroid.resolveProject()
                                flutterAndroid.resolveVersion()
                                flutterAndroid.loadReleaseNotes(params.PLAY_TRACK)
                                currentBuild.description = "${config.repo} ${env.PLAY_VERSION_NAME}+${env.PLAY_VERSION_CODE} ${env.RELEASE_COMMIT.take(12)}"
                            }
                        }
                    }

                    stage('Fetch image') {
                        steps {
                            script {
                                flutterAndroid.fetchImage()
                            }
                        }
                    }

                    stage('Backend and metadata tests') {
                        options { timeout(time: 30, unit: 'MINUTES') }
                        steps {
                            script {
                                flutterAndroid.backendTests(config)
                            }
                        }
                    }

                    stage('Prepare release signing') {
                        steps {
                            script {
                                flutterAndroid.prepareSigning()
                            }
                        }
                    }

                    stage('Build') {
                        options { timeout(time: 60, unit: 'MINUTES') }
                        steps {
                            script {
                                flutterAndroid.build('appbundle-release')
                            }
                        }
                    }

                    stage('Connected Android tests') {
                        when { expression { flutterAndroid.hasConnectedTests() } }
                        options { timeout(time: 60, unit: 'MINUTES') }
                        steps {
                            script {
                                flutterAndroid.connectedTests()
                            }
                        }
                    }

                    stage('Release artifact checks') {
                        when { expression { flutterAndroid.hasReleaseArtifactChecks() } }
                        options { timeout(time: 20, unit: 'MINUTES') }
                        steps {
                            script {
                                flutterAndroid.releaseArtifactChecks()
                            }
                        }
                    }

                    stage('Archive and upload to Artifactory') {
                        steps {
                            script {
                                env.RELEASE_AAB_SHA256 = flutterAndroid.artifactSha256()
                                flutterAndroid.archiveAndUpload()
                            }
                        }
                    }
                }
            }

            stage('Approve Google Play upload') {
                when { expression { params.PUBLISH_TO_PLAY } }
                options { timeout(time: 60, unit: 'MINUTES') }
                steps {
                    input message: "Upload ${env.PLAY_PACKAGE_NAME} ${env.PLAY_VERSION_NAME} (${env.PLAY_VERSION_CODE}) " +
                        "from ${env.RELEASE_COMMIT}, AAB SHA-256 ${env.RELEASE_AAB_SHA256}, to Google Play " +
                        "${params.PLAY_TRACK} as ${params.PLAY_RELEASE_STATUS}" +
                        (params.PLAY_RELEASE_STATUS == 'inProgress' ? " at ${params.PLAY_ROLLOUT_FRACTION}" : '') +
                        "? Release notes: ${env.PLAY_RELEASE_NOTES}"
                }
            }

            stage('Upload to Google Play') {
                agent { label 'ssh-agent-with-docker' }
                when { beforeAgent true; expression { params.PUBLISH_TO_PLAY } }
                options { timeout(time: 30, unit: 'MINUTES') }
                steps {
                    script {
                        // Pass parameters explicitly: a job's first build has no parameter environment variables.
                        flutterAndroid.playPublish(
                            track: params.PLAY_TRACK,
                            releaseStatus: params.PLAY_RELEASE_STATUS,
                            rolloutFraction: params.PLAY_ROLLOUT_FRACTION?.trim() ?: '',
                            expectedSha256: env.RELEASE_AAB_SHA256
                        )
                    }
                }
            }
        }

        post {
            always {
                node('ssh-agent-with-docker') {
                    cleanWs()
                }
            }
        }
    }
}
