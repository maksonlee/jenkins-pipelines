// Manual deployment of a static site that is served from one directory on its origin, called from a project's
// thin Jenkinsfile:
//
//     staticSiteDeploy(site: 'readerplay', repo: 'readerplay', url: 'https://readerplay.maksonlee.com',
//                      directory: 'public', statusChecks: ['/': 200], sameFiles: ['/': 'index.html'])
//
// verify-only builds the release from a commit on the branch and reads what is live. stage-only also unpacks it
// beside the site; the site does not change. activate-production then asks for approval, copies the live files
// aside, syncs the release into the live directory, checks the public site and syncs the copy back if a check
// fails. The origin helper resources/web/static_site_release.py knows each site's directories by its name and is
// sent over SSH on every run; the agent logs in with its own SSH key. nginx is not managed. See docs/jobs.md.
//   directory     the site is this top-level directory of the repository, or
//   flutterWeb    the site is the project's Flutter web build (resources/web/flutter-web-build.sh)
//   statusChecks  public path -> expected HTTP status, checked after activation
//   sameFiles     public path -> file of the release whose bytes the public site must then serve

def call(Map cfg) {
    def config = staticSiteDeployConfig(cfg)

    pipeline {
        agent none

        options {
            timestamps()
            disableConcurrentBuilds()
            skipDefaultCheckout()
            buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        }

        parameters {
            string(name: 'GIT_REF', defaultValue: config.branch, trim: true,
                description: "${config.branch}, or a full commit SHA already on ${config.branch}")
            choice(name: 'MODE', choices: ['verify-only', 'stage-only', 'activate-production'],
                description: 'Build and compare only, stage at the origin, or switch production')
            string(name: 'EXPECTED_RELEASE', defaultValue: '', trim: true,
                description: 'Required to stage or activate: the release ID a verify-only run printed for this commit')
        }

        stages {
            stage('Build and stage') {
                agent { label 'ssh-agent-with-docker' }
                stages {
                    stage('Checkout') {
                        steps {
                            script {
                                if (!(params.MODE in ['verify-only', 'stage-only', 'activate-production'])) {
                                    error('Choose a supported mode')
                                }
                                if (params.MODE != 'verify-only' && !(params.EXPECTED_RELEASE ==~ /[0-9a-f]{12}-[0-9a-f]{12}/)) {
                                    error('Enter the release ID from a verify-only run before using the origin')
                                }
                                deleteDir()
                                env.RELEASE_COMMIT = checkoutReleaseCommit(
                                    repo: config.repo, branch: config.branch, gitRef: params.GIT_REF)
                                writeFile file: config.helper, text: libraryResource('web/static_site_release.py')
                            }
                        }
                    }

                    stage('Build and package the site') {
                        options { timeout(time: config.buildTimeoutMinutes, unit: 'MINUTES') }
                        steps {
                            script {
                                if (config.flutterWeb) {
                                    withEnv([
                                        "FLUTTER_IMAGE=${buildImages.flutter()}",
                                        "BUILD_CONTAINER_RESOURCE_ARGS=${buildContainer.resourceArgs().join(' ')}",
                                        'FLUTTER_CACHE_ROOT=/home/administrator/jenkins/flutter-cache'
                                    ]) {
                                        sh libraryResource('web/flutter-web-build.sh')
                                    }
                                } else {
                                    staticSiteDeployStep(config, 'extract', ["SITE_DIRECTORY=${config.directory}"])
                                }
                                // Pass parameters explicitly: a job's first build has no parameter environment variables.
                                staticSiteDeployStep(config, 'package', [
                                    "SITE_TREE=${config.flutterWeb ? 'source/build/web' : 'build/site'}",
                                    "DEPLOY_MODE=${params.MODE}",
                                    "EXPECTED_RELEASE=${params.EXPECTED_RELEASE}"
                                ])
                                env.RELEASE_ID = jsonField("${config.evidence}/candidate.json", 'releaseId')
                                env.RELEASE_VERSION = jsonField("${config.evidence}/candidate.json", 'version')
                                env.ARCHIVE_SHA256 = jsonField("${config.evidence}/candidate.json", 'archiveSha256')
                                env.LIVE_VERSION = jsonField("${config.evidence}/origin-status.json", 'liveVersion')
                                if (!(env.RELEASE_ID ==~ /[0-9a-f]{12}-[0-9a-f]{12}/) || !(env.ARCHIVE_SHA256 ==~ /[0-9a-f]{64}/) ||
                                        !(env.RELEASE_VERSION ==~ /[0-9A-Za-z.+-]{1,40}/) || !(env.LIVE_VERSION ==~ /[0-9A-Za-z.+-]{1,40}/)) {
                                    error('Unexpected release identifiers')
                                }
                            }
                        }
                    }

                    stage('Stage at the origin') {
                        when { expression { params.MODE in ['stage-only', 'activate-production'] } }
                        options { timeout(time: 10, unit: 'MINUTES') }
                        steps {
                            script {
                                staticSiteDeployStep(config, 'stage')
                            }
                        }
                    }
                }
            }

            stage('Approve the production switch') {
                when { expression { params.MODE == 'activate-production' } }
                options { timeout(time: 30, unit: 'MINUTES') }
                steps {
                    input message: "Replace ${config.url} (now ${env.LIVE_VERSION}) with version ${env.RELEASE_VERSION}, " +
                        "release ${env.RELEASE_ID} (commit ${env.RELEASE_COMMIT})? A failed public check restores the previous files."
                }
            }

            stage('Switch production and check it') {
                agent { label 'ssh-agent-with-docker' }
                when { beforeAgent true; expression { params.MODE == 'activate-production' } }
                options { timeout(time: 10, unit: 'MINUTES') }
                steps {
                    script {
                        staticSiteDeployStep(config, 'activate', [
                            "SITE_URL=${config.url}",
                            'STATUS_CHECKS=' + config.statusChecks.collect { path, status -> "${path} ${status}" }.join('\n'),
                            'SAME_FILES=' + config.sameFiles.collect { path, file -> "${path} ${file}" }.join('\n')
                        ])
                    }
                }
            }
        }

        post {
            unsuccessful {
                node('ssh-agent-with-docker') {
                    script {
                        if (fileExists("${config.evidence}/activation.json") &&
                                jsonField("${config.evidence}/activation.json", 'state') == 'activated') {
                            staticSiteDeployStep(config, 'rollback')
                        }
                    }
                }
            }
            always {
                node('ssh-agent-with-docker') {
                    archiveArtifacts artifacts: "${config.evidence}/*.json,${config.evidence}/*.txt,${config.evidence}/manifest.sha256",
                        allowEmptyArchive: true
                }
            }
            cleanup {
                node('ssh-agent-with-docker') {
                    cleanWs(deleteDirs: true, notFailBuild: true)
                }
            }
        }
    }
}

def staticSiteDeployConfig(Map cfg) {
    def config = [
        site               : cfg.site as String,
        repo               : cfg.repo as String,
        branch             : (cfg.branch ?: 'main') as String,
        url                : cfg.url as String,
        origin             : (cfg.origin ?: 'ubuntu@app.maksonlee.com') as String,
        directory          : cfg.directory as String,
        flutterWeb         : cfg.flutterWeb == true,
        statusChecks       : (cfg.statusChecks ?: [:]) as Map,
        sameFiles          : (cfg.sameFiles ?: [:]) as Map,
        buildTimeoutMinutes: (cfg.buildTimeoutMinutes ?: (cfg.flutterWeb == true ? 40 : 10)) as int,
        evidence           : 'deploy-evidence',
        helper             : '.ci/static_site_release.py'
    ]
    if (!(config.site ==~ /^[a-z0-9][a-z0-9-]*$/)) {
        error "staticSiteDeploy: invalid site '${config.site}'"
    }
    if (!(config.repo ==~ /^[A-Za-z0-9._-]+$/)) {
        error "staticSiteDeploy: invalid repo '${config.repo}'"
    }
    if (!(config.branch ==~ /^[A-Za-z0-9._\/-]+$/) || config.branch.contains('..')) {
        error "staticSiteDeploy: invalid branch '${config.branch}'"
    }
    if (!(config.url ==~ /^https:\/\/[a-z0-9][a-z0-9.-]*$/)) {
        error "staticSiteDeploy: invalid url '${config.url}'"
    }
    if (!(config.origin ==~ /^[a-z_][a-z0-9_-]*@[a-z0-9][a-z0-9.-]*$/)) {
        error "staticSiteDeploy: invalid origin '${config.origin}'"
    }
    if (config.flutterWeb == (config.directory != null)) {
        error 'staticSiteDeploy: set either directory or flutterWeb: true'
    }
    if (config.directory != null && !(config.directory ==~ /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/)) {
        error "staticSiteDeploy: invalid directory '${config.directory}'"
    }
    if (!config.statusChecks || !config.sameFiles) {
        error 'staticSiteDeploy: statusChecks and sameFiles are required'
    }
    [config.statusChecks, config.sameFiles].each { checks ->
        checks.keySet().each { path ->
            if (!(path.toString() ==~ /^\/[A-Za-z0-9._\/-]*$/)) {
                error "staticSiteDeploy: invalid public path '${path}'"
            }
        }
    }
    config.statusChecks.values().each { status ->
        if (!(status.toString() ==~ /^[1-5][0-9]{2}$/)) {
            error "staticSiteDeploy: invalid HTTP status '${status}'"
        }
    }
    config.sameFiles.values().each { file ->
        if (!(file.toString() ==~ /^[A-Za-z0-9_][A-Za-z0-9_.\/-]*$/) || file.toString().contains('..')) {
            error "staticSiteDeploy: invalid release file '${file}'"
        }
    }
    return config
}

// One step of resources/web/static-site-deploy.sh. The release identifiers the earlier stages put in env
// (RELEASE_COMMIT, RELEASE_ID, ARCHIVE_SHA256) reach the script as environment variables.
def staticSiteDeployStep(Map config, String step, List settings = []) {
    withEnv([
        "DEPLOY_STEP=${step}",
        "SITE_NAME=${config.site}",
        "HELPER=${config.helper}",
        "ORIGIN=${config.origin}",
        "EVIDENCE=${config.evidence}"
    ] + settings) {
        sh libraryResource('web/static-site-deploy.sh')
    }
}
