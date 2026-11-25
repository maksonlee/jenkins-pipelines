"""The shared deployment steps: the release checkout and the static-site deployment script, with a fake origin."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = (ROOT / "resources/git/checkout-release-commit.sh").read_text(encoding="utf-8")
DEPLOY = (ROOT / "resources/web/static-site-deploy.sh").read_text(encoding="utf-8")
STEP = (ROOT / "vars/staticSiteDeploy.groovy").read_text(encoding="utf-8")


class Workspace(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.bin = self.workspace / "bin"
        self.bin.mkdir()

    def run_bash(self, script, settings, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RELEASE_", "DEPLOY_", "SITE_", "EXPECTED_"))}
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(settings)
        return subprocess.run(["bash"], cwd=cwd or self.workspace, input=script.encode(), capture_output=True, env=env)

    def exe(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(0o755)


class CheckoutReleaseCommitTest(Workspace):
    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=ci@example.invalid", *args],
            cwd=self.workspace / "clone", capture_output=True, check=True, text=True,
        ).stdout.strip()

    def test_only_the_branch_tip_or_a_commit_on_the_branch_is_checked_out(self):
        (self.workspace / "clone").mkdir()
        self.git("init", "--quiet", "-b", "main")
        self.git("commit", "--quiet", "--allow-empty", "-m", "First")
        first = self.git("rev-parse", "HEAD")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Second")
        tip = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", tip)
        self.git("commit", "--quiet", "--allow-empty", "-m", "Not pushed")
        outside = self.git("rev-parse", "HEAD")
        clone = self.workspace / "clone"

        def checkout(ref):
            return self.run_bash(CHECKOUT, {"RELEASE_BRANCH": "main", "RELEASE_GIT_REF": ref}, cwd=clone)

        for ref, commit in (("main", tip), (first, first)):
            result = checkout(ref)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.decode().strip(), commit)
            self.assertEqual(self.git("rev-parse", "HEAD"), commit)

        rejected = checkout(outside)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("not contained in origin/main", rejected.stderr.decode())
        self.assertFalse(rejected.stdout)
        for ref in ("HEAD", first[:12], "refs/remotes/origin/main"):
            self.assertNotEqual(checkout(ref).returncode, 0, ref)

        (clone / "untracked.txt").write_text("x", encoding="utf-8")
        dirty = checkout("main")
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("dirty", dirty.stderr.decode())


class StaticSiteDeployTest(Workspace):
    SETTINGS = {
        "SITE_NAME": "readerplay", "HELPER": "helper.py", "ORIGIN": "ubuntu@origin.invalid", "EVIDENCE": "evidence",
        "RELEASE_ID": "a" * 12 + "-" + "b" * 12, "RELEASE_COMMIT": "a" * 40, "SITE_URL": "https://site.invalid",
    }

    def setUp(self):
        super().setUp()
        (self.workspace / "helper.py").write_text("# helper\n", encoding="utf-8")
        (self.workspace / "evidence").mkdir()
        # The fake origin records the remote command and that the helper arrived on standard input.
        self.exe("ssh", 'printf \'%s\\n\' "${@: -1}" >> ssh.log\ngrep -q helper || exit 9\necho \'{"state": "activated"}\'\n')

    def step(self, name, **settings):
        return self.run_bash(DEPLOY, {**self.SETTINGS, "DEPLOY_STEP": name, **settings})

    def test_the_step_loads_every_web_script_and_asks_before_activation(self):
        loaded = set(re.findall(r"libraryResource\('web/([^']+)'\)", STEP))
        self.assertEqual(loaded, {p.name for p in (ROOT / "resources/web").iterdir()})
        for script in (ROOT / "resources/web").glob("*.sh"):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        approval = STEP.index("input message:")
        self.assertGreater(STEP.index("staticSiteDeployStep(config, 'activate'"), approval)
        self.assertLess(STEP.index("staticSiteDeployStep(config, 'stage'"), approval)
        self.assertEqual(set(re.findall(r"staticSiteDeployStep\(config, '([a-z]+)'", STEP)),
                         set(re.findall(r"^([a-z]+)\)$", DEPLOY, re.M)))
        self.assertNotIn("githubPush", STEP)
        jobs = [p for p in (ROOT / "Jenkinsfile").rglob("Jenkinsfile") if "staticSiteDeploy(" in p.read_text(encoding="utf-8")]
        self.assertEqual(sorted(p.parent.parent.name for p in jobs), ["readerplay", "share-notes"])

    def test_activation_checks_the_public_site_and_fails_closed(self):
        built = "built bytes\n"
        digest = subprocess.run(["sha256sum"], input=built.encode(), capture_output=True).stdout.decode().split()[0]
        (self.workspace / "evidence/manifest.sha256").write_text(f"{digest}  index.html\n", encoding="utf-8")
        curl = ('url="${@: -1}"\ncase " $* " in\n  *" --write-out "*) case "$url" in */missing) echo 404 ;; *) echo 200 ;; esac ;;\n'
                '  *) printf \'%s\' "$BODY" ;;\nesac\n')
        self.exe("curl", curl)
        checks = {"STATUS_CHECKS": "/ 200\n/missing 404", "SAME_FILES": "/ index.html"}

        ok = self.step("activate", BODY=built, **checks)
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        self.assertEqual((self.workspace / "ssh.log").read_text().strip(),
                         f"sudo -n python3 - readerplay activate {self.SETTINGS['RELEASE_ID']} {'a' * 40}")
        self.assertEqual((self.workspace / "evidence/public-checks.txt").read_text(),
                         f"200 /\n404 /missing\n{digest} /\n")

        for name, settings, message in (
            ("status", {"STATUS_CHECKS": "/ 404", "SAME_FILES": "/ index.html", "BODY": built}, "Unexpected HTTP 200 for /"),
            ("bytes", {**checks, "BODY": "other bytes\n"}, "/ is not the built file"),
            ("unlisted", {"STATUS_CHECKS": "/ 200", "SAME_FILES": "/ absent.js", "BODY": built}, "/ is not the built file"),
        ):
            with self.subTest(name):
                failed = self.step("activate", **settings)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(message, failed.stderr.decode())

    def test_rollback_and_unknown_steps(self):
        rollback = self.step("rollback")
        self.assertEqual(rollback.returncode, 0, rollback.stderr.decode())
        self.assertIn("readerplay rollback", (self.workspace / "ssh.log").read_text())
        self.assertTrue((self.workspace / "evidence/rollback.json").exists())
        self.assertNotEqual(self.step("delete").returncode, 0)


if __name__ == "__main__":
    unittest.main()
