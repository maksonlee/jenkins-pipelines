"""Guard the IP Location project's Jenkins trust and release boundaries."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class IpLocationJobsTest(unittest.TestCase):
    def test_webhook_is_ci_only_and_release_artifacts_are_fingerprinted(self):
        project = ROOT / "Jenkinsfile/web/ip-location"
        ci = (project / "ci/Jenkinsfile").read_text()
        release = (project / "release/Jenkinsfile").read_text()

        self.assertIn("githubPush()", ci)
        self.assertNotIn("githubPush()", release)
        self.assertIn("fingerprint: true", release)
        self.assertIn("bundle + '.sha256'", release)
        self.assertIn("copyArtifactPermission('/web/ip-location/deploy')", release)

    def test_product_source_is_pinned_by_the_operator(self):
        project = ROOT / "Jenkinsfile/web/ip-location"
        ci = (project / "ci/Jenkinsfile").read_text()
        release = (project / "release/Jenkinsfile").read_text()
        for job in (ci, release):
            self.assertIn("checkoutReleaseCommit(repo: 'ip-location', branch: 'main'", job)
        self.assertIn("gitRef: 'main'", ci)
        self.assertIn("gitRef: params.GIT_REF ?: 'main'", release)

        build = (ROOT / "vars/ipLocationBuild.groovy").read_text()
        self.assertNotIn("withCredentials", build)
        self.assertIn("--network none", build)
        self.assertIn("bash scripts/build-release.sh", build)
        self.assertNotIn("ssh ", build)


if __name__ == "__main__":
    unittest.main()
