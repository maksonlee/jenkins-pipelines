// Clone a project for a manual release or deployment and check out the commit to build: the tip of the branch, or
// a full commit SHA already contained in it. Returns the commit SHA. The clone is complete (not shallow) and the
// checkout is detached and clean.
//
//     env.RELEASE_COMMIT = checkoutReleaseCommit(repo: 'readerplay', gitRef: params.GIT_REF)
//
// branch defaults to main and directory to source.

def call(Map cfg) {
    def repo = cfg.repo as String
    def branch = (cfg.branch ?: 'main') as String
    def gitRef = (cfg.gitRef ?: '') as String
    def directory = (cfg.directory ?: 'source') as String
    if (!(repo ==~ /^[A-Za-z0-9._-]+$/)) {
        error "checkoutReleaseCommit: invalid repo '${repo}'"
    }
    if (!(branch ==~ /^[A-Za-z0-9._\/-]+$/) || branch.contains('..')) {
        error "checkoutReleaseCommit: invalid branch '${branch}'"
    }
    if (gitRef != branch && !(gitRef ==~ /^[0-9a-f]{40}$/)) {
        error "GIT_REF must be ${branch} or a full commit SHA"
    }
    def commit = ''
    dir(directory) {
        git branch: branch, credentialsId: 'vault-github-ssh', url: "git@github.com:maksonlee/${repo}.git"
        withEnv(["RELEASE_BRANCH=${branch}", "RELEASE_GIT_REF=${gitRef}"]) {
            commit = sh(returnStdout: true, script: libraryResource('git/checkout-release-commit.sh')).trim()
        }
    }
    if (!(commit ==~ /^[0-9a-f]{40}$/)) {
        error 'checkoutReleaseCommit: could not resolve the release commit'
    }
    echo "${repo} ${branch}: ${commit}"
    return commit
}
