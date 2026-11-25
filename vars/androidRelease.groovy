// Manual Google Play release for one native Android app, called from a project's thin Jenkinsfile:
//
//     androidRelease(repo: 'beepbeep', branch: 'master', packageName: 'com.maksonlee.beepbeep', ...)
//
// GIT_REF must be the release branch or a full commit SHA already contained in it. The job checks release notes,
// runs the configured verification tasks and builds the signed AAB, then verifies it on the agent (bundletool,
// manifest package and version, strict jarsigner) and records its signer certificate SHA-256. With
// verifyUploadCertificate the signer must also equal Vault certificate_sha256; otherwise Google Play checks the
// upload key at upload time, as before. Only with PUBLISH_TO_PLAY and after a manual input naming the exact commit,
// version, SHA-256, signer, track and status does it read the Play service account and upload that same verified
// AAB through GPP (--artifact-dir, --track, --release-status). See docs/android.md.

def call(Map cfg) {
    def config = androidReleaseConfig(cfg)

    pipeline {
        agent { label 'ssh-agent-with-docker' }

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
                description: 'Upload the verified AAB to Google Play after a manual approval')
            choice(name: 'PLAY_TRACK', choices: config.tracks, description: 'Google Play API track ID')
            choice(name: 'PLAY_RELEASE_STATUS', choices: config.releaseStatuses, description: 'Google Play release status')
        }

        stages {
            stage('Resolve release') {
                steps {
                    script {
                        def gitRef = params.GIT_REF?.trim()
                        if (!gitRef || (gitRef != config.branch && !(gitRef ==~ /^[0-9a-fA-F]{40}$/))) {
                            error "GIT_REF must be ${config.branch} or a full 40-character commit SHA"
                        }
                        if (!(params.PLAY_TRACK in config.tracks) || !(params.PLAY_RELEASE_STATUS in config.releaseStatuses)) {
                            error 'PLAY_TRACK or PLAY_RELEASE_STATUS is not offered for this project'
                        }
                        env.GIT_REF = gitRef
                    }
                }
            }

            stage('Fetch code') {
                steps {
                    deleteDir()
                    checkout(changelog: false, poll: false, scm: [
                        $class: 'GitSCM',
                        branches: [[name: env.GIT_REF]],
                        extensions: [],
                        userRemoteConfigs: [[
                            credentialsId: 'vault-github-ssh',
                            url: "git@github.com:maksonlee/${config.repo}.git"
                        ]]
                    ])
                    script {
                        withEnv([
                            "RELEASE_BRANCH=${config.branch}",
                            'RELEASE_FORBIDDEN_PATHS=' + config.forbiddenPaths.join('\n')
                        ]) {
                            env.RELEASE_COMMIT = sh(returnStdout: true,
                                script: libraryResource('android/release/resolve-commit.sh')).trim()
                        }
                        // Pass parameters explicitly: a job's first build has no parameter environment variables.
                        withEnv(["RELEASE_NOTES_DIR=${config.module}/src/main/play/release-notes", "PLAY_TRACK=${params.PLAY_TRACK}"]) {
                            sh libraryResource('android/release/check-release-notes.sh')
                        }
                        currentBuild.description = "${config.repo} ${env.RELEASE_COMMIT.take(12)}"
                    }
                }
            }

            stage('Test, lint, build & sign AAB') {
                options { timeout(time: config.buildTimeoutMinutes, unit: 'MINUTES') }
                steps {
                    script {
                        gradleRelease(":${config.module}:bundleRelease", androidReleaseGradleConfig(config, [
                            extraArgs: config.verifyTasks
                        ]))
                    }
                }
            }

            stage('Verify AAB') {
                options { timeout(time: 20, unit: 'MINUTES') }
                steps {
                    script {
                        def verify = {
                            withEnv([
                                "RELEASE_AAB=${config.aab}",
                                "RELEASE_PACKAGE=${config.packageName}",
                                "BUNDLETOOL_JAR=${config.bundletoolJar}",
                                "BUNDLETOOL_SHA256=${config.bundletoolSha256}",
                                "RELEASE_FORBIDDEN_AAB_ENTRY=${config.forbiddenAabEntry ?: ''}",
                                "RELEASE_VERIFY_SIGNER=${config.verifyUploadCertificate}"
                            ]) {
                                sh libraryResource('android/release/verify-aab.sh')
                            }
                        }
                        if (config.verifyUploadCertificate) {
                            withVault([vaultSecrets: [[
                                path: config.keystoreVaultPath,
                                engineVersion: 2,
                                secretValues: [[envVar: 'EXPECTED_UPLOAD_CERT_SHA256', vaultKey: 'certificate_sha256']]
                            ]]]) {
                                verify()
                            }
                        } else {
                            verify()
                        }
                        def manifest = [:]
                        readFile("${config.releaseDir}/release-manifest.txt").readLines().each { line ->
                            def separator = line.indexOf('=')
                            if (separator > 0) {
                                manifest[line.substring(0, separator)] = line.substring(separator + 1)
                            }
                        }
                        env.RELEASE_VERSION_CODE = manifest.versionCode
                        env.RELEASE_VERSION_NAME = manifest.versionName
                        env.RELEASE_SIGNER_SHA256 = manifest.signerSha256
                        env.RELEASE_AAB_SHA256 = readFile("${config.aab}.sha256").trim().split(/\s+/)[0]
                        currentBuild.description = "${config.repo} ${env.RELEASE_VERSION_NAME} (${env.RELEASE_VERSION_CODE}) ${env.RELEASE_COMMIT.take(12)}"
                    }
                    stash name: 'android-release-aab', includes: config.evidence
                    archiveArtifacts artifacts: config.evidence, fingerprint: true
                }
            }

            stage('Approve Google Play upload') {
                when { expression { params.PUBLISH_TO_PLAY } }
                options { timeout(time: config.approvalTimeoutMinutes, unit: 'MINUTES') }
                steps {
                    input message: "Upload ${config.packageName} ${env.RELEASE_VERSION_NAME} (${env.RELEASE_VERSION_CODE}) " +
                        "from ${env.RELEASE_COMMIT}, AAB SHA-256 ${env.RELEASE_AAB_SHA256}, signed by certificate " +
                        "SHA-256 ${env.RELEASE_SIGNER_SHA256}" +
                        (config.verifyUploadCertificate ? ' (matches Vault certificate_sha256)' : '') + ', to Google Play ' +
                        "${params.PLAY_TRACK} as ${params.PLAY_RELEASE_STATUS}" +
                        (config.publishListingsWithRelease ? ', together with the store listing from this commit?' : '?') +
                        (config.approvalNote ? " ${config.approvalNote}" : '')
                }
            }

            stage('Publish to Google Play') {
                when { expression { params.PUBLISH_TO_PLAY } }
                options { timeout(time: 30, unit: 'MINUTES') }
                steps {
                    script {
                        unstash 'android-release-aab'
                        withEnv(["RELEASE_AAB=${config.aab}", "PUBLICATION_DIR=${config.publicationDir}"]) {
                            sh libraryResource('android/release/stage-verified-aab.sh')
                        }
                        def publishTask = config.publishListingsWithRelease ? 'publishReleaseApps' : 'publishReleaseBundle'
                        gradleRelease(":${config.module}:${publishTask}", androidReleaseGradleConfig(config, [
                            playServiceVaultPath: 'secret/jenkins/mobile/shared/play-service',
                            playJsonPath: "/tmp/${config.repo}-play-service.json",
                            extraArgs: [
                                "--artifact-dir=${config.publicationDir}",
                                "--track=${params.PLAY_TRACK}",
                                "--release-status=${params.PLAY_RELEASE_STATUS}"
                            ]
                        ]))
                    }
                }
            }
        }

        post {
            always {
                cleanWs(deleteDirs: true, notFailBuild: true)
            }
        }
    }
}

def androidReleaseConfig(Map cfg) {
    def module = (cfg.module ?: 'app') as String
    def config = [
        repo                     : cfg.repo as String,
        branch                   : (cfg.branch ?: 'main') as String,
        packageName              : cfg.packageName as String,
        module                   : module,
        // The native apps share one upload key; a project registered in Play with its own key sets its path.
        keystoreVaultPath        : (cfg.keystoreVaultPath ?: 'secret/jenkins/mobile/shared/upload-key') as String,
        // Compare the signer with Vault certificate_sha256 at that path; only projects that opt in compare it.
        verifyUploadCertificate  : cfg.verifyUploadCertificate == true,
        passSigningProperties    : cfg.get('passSigningProperties', false) as boolean,
        verifyTasks              : (cfg.verifyTasks ?: [":${module}:testDebugUnitTest", ":${module}:lintDebug"]) as List,
        tracks                   : (cfg.tracks ?: ['internal']) as List,
        releaseStatuses          : (cfg.releaseStatuses ?: ['completed', 'draft']) as List,
        publishListingsWithRelease: cfg.publishListingsWithRelease == true,
        forbiddenPaths           : (cfg.forbiddenPaths ?: []) as List,
        forbiddenAabEntry        : cfg.forbiddenAabEntry as String,
        buildTimeoutMinutes      : (cfg.buildTimeoutMinutes ?: 90) as int,
        approvalTimeoutMinutes   : (cfg.approvalTimeoutMinutes ?: 60) as int,
        // A fixed sentence appended to the approval message, for a project-specific warning.
        approvalNote             : (cfg.approvalNote ?: '') as String,
        bundletoolJar            : '/home/administrator/jenkins/android-cache/tools/bundletool-all-1.18.3.jar',
        bundletoolSha256         : 'a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29'
    ]
    config.releaseDir = "${module}/build/outputs/bundle/release"
    config.aab = "${config.releaseDir}/${module}-release.aab"
    config.publicationDir = "${module}/build/outputs/play-publication"
    config.evidence = ['*.aab', '*.aab.sha256', 'signature-verification.txt', 'release-manifest.txt', 'bundletool-validate.txt']
        .collect { "${config.releaseDir}/${it}" }.join(',')

    if (!(config.approvalNote ==~ /^[A-Za-z0-9 .,;:()\/-]{0,300}$/)) {
        error 'androidRelease: approvalNote may hold only letters, digits, spaces and basic punctuation'
    }
    if (!(config.repo ==~ /^[A-Za-z0-9._-]+$/)) {
        error "androidRelease: invalid repo '${config.repo}'"
    }
    if (!(config.branch ==~ /^[A-Za-z0-9._\/-]+$/) || config.branch.contains('..')) {
        error "androidRelease: invalid branch '${config.branch}'"
    }
    if (!(config.packageName ==~ /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/)) {
        error "androidRelease: invalid packageName '${config.packageName}'"
    }
    if (!(module ==~ /^[A-Za-z0-9_-]+$/)) {
        error "androidRelease: invalid module '${module}'"
    }
    config.verifyTasks.each { task ->
        if (!(task.toString() ==~ /^[A-Za-z0-9:_.-]+$/)) {
            error "androidRelease: invalid verification task '${task}'"
        }
    }
    config.tracks.each { track ->
        if (!(track in ['internal', 'alpha', 'beta', 'production'])) {
            error "androidRelease: unsupported track '${track}'"
        }
    }
    config.releaseStatuses.each { status ->
        if (!(status in ['completed', 'draft'])) {
            error "androidRelease: unsupported release status '${status}'"
        }
    }
    config.forbiddenPaths.each { path ->
        if (!(path.toString() ==~ /^[A-Za-z0-9_][A-Za-z0-9_.\/-]*$/) || path.toString().contains('..')) {
            error "androidRelease: invalid forbidden path '${path}'"
        }
    }
    return config
}

def androidReleaseGradleConfig(Map config, Map overrides) {
    return [
        // Store listings are published only when the project opts in with publishListingsWithRelease, which
        // selects publishReleaseApps explicitly; the shared helper must never upgrade the task on its own.
        publishPlayListings: false,
        keystoreVaultPath: config.keystoreVaultPath,
        jksPath: "/tmp/${config.repo}-upload.jks",
        passSigningProperties: config.passSigningProperties
    ] + overrides
}
