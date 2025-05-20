def call(Map config = [:]) {
    def ccachePath = config.ccachePath ?: "/home/administrator/.cache/ccache"
    def dockerImage = config.dockerImage ?: "aosp-builder"
    def sshCredential = config.sshCredential ?: "gerrit-ssh-maksonlee"
    def manifestUrl = config.manifestUrl
    def branch = config.branch
    def lunchTarget = config.lunchTarget

    docker.image(dockerImage).inside("-v ${ccachePath}:/ccache") {
        withVault([
            vaultSecrets: [[
                path: 'secret/jenkins/gerrit/maksonlee',
                engineVersion: 2,
                secretValues: [[envVar: 'GERRIT_SSH_KEY', vaultKey: 'ssh_key']]
            ]]
        ]) {
            sh '''#!/bin/bash
                mkdir -p ~/.ssh
                chmod 700 ~/.ssh
                echo "$GERRIT_SSH_KEY" > ~/.ssh/id_rsa
                chmod 600 ~/.ssh/id_rsa
                ssh-keyscan -p 29418 -H gerrit.maksonlee.com >> ~/.ssh/known_hosts
            '''

            sh """#!/bin/bash
                repo init -u ${manifestUrl} -b ${branch}
                repo sync -c -j1

                source build/envsetup.sh
                lunch ${lunchTarget}
                m
            """
        }
    }
}