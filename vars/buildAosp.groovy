def call(Map config = [:]) {
    def ccachePath = config.ccachePath ?: "/home/administrator/.cache/ccache"
    def dockerImage = config.dockerImage ?: "aosp-builder"
    def sshCredential = config.sshCredential ?: "gerrit-ssh-maksonlee"
    def manifestUrl = config.manifestUrl
    def branch = config.branch
    def lunchTarget = config.lunchTarget

    docker.image(dockerImage).inside("-v ${ccachePath}:/ccache") {
        sshagent([sshCredential]) {
            sh """#!/bin/bash
                mkdir -p ~/.ssh
                chmod 700 ~/.ssh
                ssh-keyscan -p 29418 -H gerrit.maksonlee.com >> ~/.ssh/known_hosts

                repo init -u ${manifestUrl} -b ${branch}
                repo sync -c -j1

                source build/envsetup.sh
                lunch ${lunchTarget}
                m
            """
        }
    }
}