// Push-triggered debug CI for one native Android project, called from a project's thin Jenkinsfile:
//
//     androidCi(repo: 'beepbeep', branch: 'master', gradleTasks: [...], debugApkModules: ['app'], ...)
//
// It checks out the tip of the project's branch, runs the configured Gradle tasks with --continue in the pinned
// Android image (no release signing material), and archives JUnit XML, debug lint reports, debug and
// instrumentation APKs with SHA256SUMS under evidence/. See docs/android.md for the project contract.
// With trace: true (learn-chess) it also keeps the plain Gradle log, run.json and summary.md, and with
// deviceRunner it runs the project's device suite on a disposable emulator.

def call(Map cfg) {
    def config = androidCiConfig(cfg)

    pipeline {
        agent { label 'ssh-agent-with-docker' }

        options {
            timestamps()
            disableConcurrentBuilds()
            skipDefaultCheckout()
            timeout(time: config.timeoutMinutes, unit: 'MINUTES')
            buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        }

        triggers {
            githubPush()
        }

        stages {
            stage('Fetch source') {
                steps {
                    script {
                        env.NATIVE_ANDROID_REPO = config.repo
                        env.NATIVE_ANDROID_BRANCH = config.branch
                        env.NATIVE_ANDROID_TRACE = config.trace.toString()
                        env.NATIVE_ANDROID_IMAGE = buildImages.android()
                        env.NATIVE_ANDROID_GRADLE_EXIT = 'not run'
                        env.NATIVE_ANDROID_BUILD_OUTCOME = 'pending'
                        deleteDir()
                        env.NATIVE_ANDROID_WORKSPACE_READY = 'true'
                        dir('source') {
                            // Polling stays enabled so that the GitHub push trigger matches this repository.
                            checkout(scmGit(
                                branches: [[name: "refs/heads/${config.branch}"]],
                                extensions: [cloneOption(honorRefspec: true, noTags: true, shallow: false)],
                                userRemoteConfigs: [[
                                    credentialsId: 'vault-github-ssh',
                                    refspec: "+refs/heads/${config.branch}:refs/remotes/origin/${config.branch}",
                                    url: "git@github.com:maksonlee/${config.repo}.git"
                                ]]
                            ))
                            env.NATIVE_ANDROID_SOURCE_COMMIT = sh(
                                returnStdout: true,
                                script: libraryResource('android/ci/checkout-verify.sh')
                            ).trim()
                        }
                        currentBuild.description = "${config.repo} ${config.branch} ${env.NATIVE_ANDROID_SOURCE_COMMIT.take(12)}"
                    }
                }
            }

            stage('Build and check debug') {
                steps {
                    script {
                        if (config.trace) {
                            def passed = false
                            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
                                androidCiBuild(config)
                                passed = true
                            }
                            if (!passed) {
                                env.NATIVE_ANDROID_BUILD_OUTCOME = 'failure'
                            }
                        } else {
                            androidCiBuild(config)
                        }
                    }
                }
            }

            stage('Collect build evidence') {
                when { expression { config.trace } }
                steps {
                    script {
                        env.NATIVE_ANDROID_OUTPUTS_COLLECTED = 'false'
                        catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
                            androidCiCollect(config)
                            env.NATIVE_ANDROID_OUTPUTS_COLLECTED = 'true'
                        }
                        env.NATIVE_ANDROID_SOURCE_UNCHANGED = 'false'
                        catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
                            sh libraryResource('android/ci/verify-unchanged.sh')
                            env.NATIVE_ANDROID_SOURCE_UNCHANGED = 'true'
                        }
                        if (env.NATIVE_ANDROID_OUTPUTS_COLLECTED != 'true' || env.NATIVE_ANDROID_SOURCE_UNCHANGED != 'true') {
                            env.NATIVE_ANDROID_BUILD_OUTCOME = 'failure'
                        } else if (env.NATIVE_ANDROID_BUILD_OUTCOME == 'pending') {
                            env.NATIVE_ANDROID_BUILD_OUTCOME = 'success'
                        }
                    }
                }
            }

            stage('Run Android device tests') {
                when { expression { config.deviceRunner && env.NATIVE_ANDROID_SOURCE_UNCHANGED == 'true' } }
                steps {
                    script {
                        env.NATIVE_ANDROID_DEVICE_OUTCOME = 'pending'
                        def exitCode = null
                        catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
                            timeout(time: config.deviceTimeoutMinutes, unit: 'MINUTES') {
                                withEnv(["NATIVE_ANDROID_DEVICE_RUNNER=${config.deviceRunner}"]) {
                                    exitCode = sh(returnStatus: true, script: libraryResource('android/ci/device.sh'))
                                }
                            }
                            env.NATIVE_ANDROID_DEVICE_EXIT = exitCode.toString()
                            if (exitCode == 3) {
                                env.NATIVE_ANDROID_DEVICE_OUTCOME = 'skipped'
                            } else if (exitCode in [130, 143]) {
                                env.NATIVE_ANDROID_DEVICE_OUTCOME = 'aborted'
                                currentBuild.result = 'ABORTED'
                            }
                            if (exitCode != 0) {
                                error "Android device verification exited with status ${exitCode}"
                            }
                        }
                        if (env.NATIVE_ANDROID_DEVICE_OUTCOME == 'pending') {
                            env.NATIVE_ANDROID_DEVICE_OUTCOME = exitCode == 0 ? 'success' : 'failure'
                        }
                    }
                }
            }
        }

        post {
            always {
                script {
                    if (config.trace) {
                        androidCiFinishTraced(config)
                    } else if (env.NATIVE_ANDROID_SOURCE_COMMIT) {
                        dir('evidence') {
                            writeFile file: 'source-commit.txt', text: env.NATIVE_ANDROID_SOURCE_COMMIT + '\n'
                            writeFile file: 'build.txt', text: androidCiBuildText(config)
                        }
                        catchError(buildResult: 'FAILURE', message: 'Could not collect all required CI outputs', catchInterruptions: false) {
                            androidCiCollect(config)
                        }
                        catchError(buildResult: 'FAILURE', message: 'Could not archive CI evidence', catchInterruptions: false) {
                            archiveArtifacts artifacts: 'evidence/**', fingerprint: true, followSymlinks: false
                        }
                        junit testResults: 'evidence/junit/**/*.xml', allowEmptyResults: true, skipPublishingChecks: true
                    }
                }
            }
            cleanup {
                cleanWs(deleteDirs: true, notFailBuild: true)
            }
        }
    }
}

def androidCiConfig(Map cfg) {
    def config = [
        repo                : cfg.repo as String,
        branch              : (cfg.branch ?: 'main') as String,
        gradleTasks         : (cfg.gradleTasks ?: ['nativeAndroidCi']) as List,
        maxWorkers          : cfg.maxWorkers,
        debugApkModules     : (cfg.debugApkModules ?: []) as List,
        androidTestModules  : (cfg.androidTestModules ?: []) as List,
        lintModules         : (cfg.lintModules ?: []) as List,
        unitTestTasks       : (cfg.unitTestTasks ?: [:]) as Map,
        flatAppEvidence     : cfg.flatAppEvidence == true,
        trace               : cfg.trace == true,
        summaryScript       : cfg.summaryScript as String,
        deviceRunner        : cfg.deviceRunner as String,
        timeoutMinutes      : (cfg.timeoutMinutes ?: 60) as int,
        deviceTimeoutMinutes: (cfg.deviceTimeoutMinutes ?: 70) as int
    ]
    if (!(config.repo ==~ /^[A-Za-z0-9._-]+$/)) {
        error "androidCi: invalid repo '${config.repo}'"
    }
    if (!(config.branch ==~ /^[A-Za-z0-9._\/-]+$/) || config.branch.contains('..')) {
        error "androidCi: invalid branch '${config.branch}'"
    }
    config.gradleTasks.each { task ->
        if (!(task.toString() ==~ /^[A-Za-z0-9:_.-]+$/)) {
            error "androidCi: invalid Gradle task '${task}'"
        }
    }
    if (config.maxWorkers != null && !(config.maxWorkers.toString() ==~ /^[1-9][0-9]?$/)) {
        error "androidCi: invalid maxWorkers '${config.maxWorkers}'"
    }
    (config.debugApkModules + config.androidTestModules + config.lintModules + config.unitTestTasks.keySet()).each { module ->
        if (!(module.toString() ==~ /^[A-Za-z0-9_][A-Za-z0-9_\/-]*$/)) {
            error "androidCi: invalid module path '${module}'"
        }
    }
    config.unitTestTasks.values().each { task ->
        if (!(task.toString() ==~ /^[A-Za-z0-9_]+$/)) {
            error "androidCi: invalid unit test task '${task}'"
        }
    }
    [config.summaryScript, config.deviceRunner].findAll { it }.each { script ->
        if (!(script ==~ /^[A-Za-z0-9_][A-Za-z0-9_.\/-]*\.py$/) || script.contains('..')) {
            error "androidCi: invalid repository script '${script}'"
        }
    }
    if (config.deviceRunner && !config.trace) {
        error 'androidCi: deviceRunner requires trace: true'
    }
    return config
}

def androidCiBuild(Map config) {
    def cache = '/home/administrator/jenkins/android-cache'
    def androidUserHome = "${pwd()}/android-user-home"
    // AGP writes preferences and lock files alongside the read-only keystore.
    sh 'mkdir -p -- android-user-home'
    def args = buildContainer.resourceArgs() + [
        "--mount type=bind,src=${cache}/gradle,dst=/home/ubuntu/.gradle",
        "--mount type=bind,src=${cache}/flutter-android-sdk,dst=/home/ubuntu/Android,readonly",
        "--mount 'type=bind,src=/home/administrator/.android/debug.keystore,dst=${androidUserHome}/debug.keystore,readonly'"
    ]
    def gradleArgs = config.gradleTasks + ['--continue', '--console=plain', '--no-daemon']
    if (config.maxWorkers != null) {
        gradleArgs << "--max-workers=${config.maxWorkers}"
    }
    docker.image(env.NATIVE_ANDROID_IMAGE).inside(args.join(' ')) {
        withEnv([
            'GRADLE_USER_HOME=/home/ubuntu/.gradle',
            "ANDROID_USER_HOME=${androidUserHome}",
            'NATIVE_ANDROID_GRADLE_ARGS=' + gradleArgs.join('\n')
        ]) {
            dir('source') {
                def gradleExit = sh(returnStatus: true, script: libraryResource('android/ci/gradle.sh'))
                env.NATIVE_ANDROID_GRADLE_EXIT = gradleExit.toString()
                if (gradleExit != 0) {
                    error "Debug CI Gradle tasks exited with status ${gradleExit}"
                }
            }
        }
    }
}

def androidCiCollect(Map config) {
    withEnv([
        "NATIVE_ANDROID_DEBUG_APK_MODULES=${config.debugApkModules.join(' ')}",
        "NATIVE_ANDROID_ANDROID_TEST_MODULES=${config.androidTestModules.join(' ')}",
        "NATIVE_ANDROID_LINT_MODULES=${config.lintModules.join(' ')}",
        "NATIVE_ANDROID_FLAT_APP_EVIDENCE=${config.flatAppEvidence}",
        "NATIVE_ANDROID_UNIT_TEST_TASKS=${config.unitTestTasks.collect { module, task -> "${module}:${task}" }.join(' ')}"
    ]) {
        sh libraryResource('android/ci/collect-evidence.sh')
    }
}

def androidCiBuildText(Map config) {
    return "Repository: maksonlee/${config.repo}\nBranch: ${config.branch}\nImage: ${env.NATIVE_ANDROID_IMAGE}\n" +
        "Build command exit: ${env.NATIVE_ANDROID_GRADLE_EXIT}\n"
}

def androidCiFinishTraced(Map config) {
    if (env.NATIVE_ANDROID_WORKSPACE_READY != 'true') {
        return
    }

    def aborted = currentBuild.currentResult == 'ABORTED'
    def buildOutcome = env.NATIVE_ANDROID_BUILD_OUTCOME
    if (buildOutcome == 'pending') {
        buildOutcome = aborted ? 'aborted' : 'failure'
    }
    def deviceOutcome = env.NATIVE_ANDROID_DEVICE_OUTCOME ?: 'skipped'
    if (deviceOutcome == 'pending') {
        deviceOutcome = aborted ? 'aborted' : 'failure'
    }

    catchError(buildResult: 'FAILURE', message: 'Could not archive Android CI run evidence', catchInterruptions: false) {
        sh 'mkdir -p evidence'
        if (env.NATIVE_ANDROID_SOURCE_COMMIT && env.NATIVE_ANDROID_OUTPUTS_COLLECTED != 'true') {
            catchError(buildResult: 'FAILURE', message: 'Could not collect partial build outputs', catchInterruptions: false) {
                androidCiCollect(config)
            }
        }
        if (env.NATIVE_ANDROID_SOURCE_COMMIT && config.summaryScript && fileExists("source/${config.summaryScript}")) {
            def summarize = "python3 source/${config.summaryScript} --source source --evidence evidence"
            if (buildOutcome == 'success') {
                summarize += ' --require-complete'
            }
            if (sh(returnStatus: true, script: summarize) != 0 && buildOutcome == 'success') {
                buildOutcome = 'failure'
                currentBuild.result = 'FAILURE'
            }
        }

        dir('evidence') {
            if (env.NATIVE_ANDROID_SOURCE_COMMIT) {
                writeFile file: 'source-commit.txt', text: env.NATIVE_ANDROID_SOURCE_COMMIT + '\n'
            }
            writeFile file: 'build.txt', text: androidCiBuildText(config)
            def report = [
                repository: "maksonlee/${config.repo}", branch: config.branch, jenkinsBuildUrl: env.BUILD_URL,
                verifiedCommit: env.NATIVE_ANDROID_SOURCE_COMMIT ?: null,
                cleanBeforeBuild: env.NATIVE_ANDROID_SOURCE_COMMIT ? true : null,
                cleanAfterBuild: env.NATIVE_ANDROID_SOURCE_UNCHANGED ? env.NATIVE_ANDROID_SOURCE_UNCHANGED == 'true' : null,
                buildImage: env.NATIVE_ANDROID_IMAGE, gradleExit: env.NATIVE_ANDROID_GRADLE_EXIT,
                build: buildOutcome, device: deviceOutcome, deviceExit: env.NATIVE_ANDROID_DEVICE_EXIT ?: 'not run',
                buildSummary: fileExists('build-summary.json') ? 'build-summary.json' : null,
                deviceSummary: fileExists('device/results/summary.json') ? 'device/results/summary.json' : null
            ]
            writeFile file: 'run.json', text: groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(report)) + '\n'
            writeFile file: 'summary.md', text: """# Android CI

- Repository: ${report.repository}
- Jenkins build: ${report.jenkinsBuildUrl}
- Verified checkout: ${report.verifiedCommit ?: 'unavailable'} (${config.branch} tip)
- Checkout clean before / after build: ${report.cleanBeforeBuild} / ${report.cleanAfterBuild}
- Build: ${report.build} (Gradle exit ${report.gradleExit})
- Device: ${report.device} (runner exit ${report.deviceExit})

See `build-summary.json` for build verification details, `gradle.log` for build output, `device/preflight.log` for device setup, and `device/results/summary.md` for device results when available. The APKs are available in `apk/` with checksums in `SHA256SUMS`.
"""
        }
        sh 'tar -C evidence -czf ci-evidence.tar.gz .'
        archiveArtifacts artifacts: 'evidence/**,ci-evidence.tar.gz', fingerprint: true, followSymlinks: false
    }
    catchError(buildResult: 'FAILURE', message: 'Could not publish Android CI JUnit reports', catchInterruptions: false) {
        junit testResults: 'evidence/junit/**/*.xml,evidence/device/results/junit/*.xml', allowEmptyResults: true, skipPublishingChecks: true
    }
}
