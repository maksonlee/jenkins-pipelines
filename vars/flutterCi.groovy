// Push-triggered CI for one Flutter app, called from a project's thin Jenkinsfile:
//
//     flutterCi(repo: 'share-notes', branch: 'master')
//
// It checks out the branch tip, runs the project's quality gates and connected-device tests, builds a debug AAB,
// archives it and uploads it to the Artifactory android-snapshots repository. It never uses release signing or
// Google Play. See docs/flutter.md for the project contract.

def call(Map cfg) {
    def config = flutterAndroid.config(cfg, 'flutterCi')

    pipeline {
        agent { label 'ssh-agent-with-docker' }

        options {
            timestamps()
            disableConcurrentBuilds()
            skipDefaultCheckout()
            timeout(time: 90, unit: 'MINUTES')
            buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        }

        triggers {
            githubPush()
        }

        stages {
            stage('Fetch code') {
                steps {
                    deleteDir()
                    script {
                        flutterAndroid.setEnvironment(config)
                    }
                    dir('source') {
                        git branch: config.branch, credentialsId: 'vault-github-ssh', url: env.PROJECT_REPO
                    }
                    script {
                        flutterAndroid.resolveProject()
                        currentBuild.description = "${config.repo} ${config.branch}"
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
                steps {
                    script {
                        flutterAndroid.backendTests(config)
                    }
                }
            }

            stage('Build') {
                steps {
                    script {
                        flutterAndroid.build('appbundle-debug')
                    }
                }
            }

            stage('Connected Android tests') {
                when { expression { flutterAndroid.hasConnectedTests() } }
                steps {
                    script {
                        flutterAndroid.connectedTests()
                    }
                }
            }

            stage('Archive and upload to Artifactory') {
                steps {
                    script {
                        flutterAndroid.archiveAndUpload()
                    }
                }
            }
        }

        post {
            always {
                cleanWs()
            }
        }
    }
}
