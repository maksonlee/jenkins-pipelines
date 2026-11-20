"""Exercise the pipeline's Bash blocks with local Git and synthetic AGP outputs."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


BASH = os.environ.get("BASH", "bash")
JENKINSFILE = Path(__file__).with_name("Jenkinsfile").read_text(encoding="utf-8")
COLLECT, CHECKOUT, BUILD = re.findall(r"'''(#!/bin/bash\n.*?)'''", JENKINSFILE, re.S)

LEARN_CHESS = {
    "DEBUG_APK_MODULES": "app",
    "ANDROID_TEST_MODULES": "app",
    "LINT_MODULES": "app",
    "FLAT_APP_EVIDENCE": "true",
    "UNIT_TEST_TASKS": "app:testDebugUnitTest core/chess:test core/lesson:test",
}
CLOUD_COST_WIDGETS = {
    "DEBUG_APK_MODULES": "apps/aws apps/aws-wear",
    "ANDROID_TEST_MODULES": "providers/aws apps/aws-wear",
    "LINT_MODULES": "apps/aws apps/aws-wear shared/core providers/aws",
    "FLAT_APP_EVIDENCE": "false",
    "UNIT_TEST_TASKS": "shared/core:testDebugUnitTest providers/aws:testDebugUnitTest",
}


class ContractTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.source = self.workspace / "source"
        self.source.mkdir()
        self.evidence = self.workspace / "evidence"
        self.evidence.mkdir()

    def collect(self, config, gradle_exit=0):
        return self.run_bash(COLLECT, self.workspace, {**config, "GRADLE_EXIT": str(gradle_exit)})

    def run_bash(self, script, cwd, settings):
        env = dict(os.environ)
        env.update({f"NATIVE_ANDROID_{key}": value for key, value in settings.items()})
        return subprocess.run([BASH], cwd=cwd, input=script.encode(), capture_output=True, env=env)

    def add(self, source, archive, content):
        path = self.source / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
        return archive, content.encode()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=ci@example.invalid", *args],
            cwd=self.source, capture_output=True, check=True, text=True,
        ).stdout.strip()

    def assert_evidence(self, expected):
        actual = {
            path.relative_to(self.evidence).as_posix(): path.read_bytes()
            for path in self.evidence.rglob("*") if path.is_file()
        }
        checksums = actual.pop("SHA256SUMS").decode().splitlines()
        self.assertEqual(actual, dict(expected))
        self.assertCountEqual([re.split(r" [ *]", line, maxsplit=1) for line in checksums], [
            [hashlib.sha256(content).hexdigest(), path]
            for path, content in expected
            if path.endswith(".apk")
        ])
        check = self.run_bash("sha256sum --check SHA256SUMS", self.evidence, {})
        self.assertEqual(check.returncode, 0, check.stderr.decode())

    def test_embedded_bash_syntax(self):
        for script in (COLLECT, CHECKOUT, BUILD):
            result = subprocess.run([BASH, "-n"], input=script.encode(), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_learn_chess_keeps_existing_archive_paths(self):
        expected = [
            self.add("app/build/test-results/testDebugUnitTest/TEST-app.xml", "junit/app/TEST-app.xml", "app tests"),
            self.add("core/chess/build/test-results/test/TEST-core.xml", "junit/core/chess/TEST-core.xml", "chess tests"),
            self.add("core/lesson/build/test-results/test/TEST-core.xml", "junit/core/lesson/TEST-core.xml", "lesson tests"),
            self.add("app/build/reports/lint-results-debug.xml", "lint/lint-results-debug.xml", "app lint"),
            self.add("app/build/reports/lint-results-debug.html", "lint/lint-results-debug.html", "app lint HTML"),
            self.add("app/build/outputs/apk/debug/app-debug.apk", "apk/debug/app-debug.apk", "app apk"),
            self.add("app/build/outputs/apk/androidTest/debug/app-test.apk", "apk/androidTest/app-test.apk", "app test apk"),
        ]
        result = self.collect(LEARN_CHESS)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_evidence(expected)

    def add_cloud_cost_outputs(self):
        expected = []
        for module in ("apps/aws", "apps/aws-wear", "providers/aws", "shared/core"):
            for extension in ("xml", "html"):
                name = f"lint-results-debug.{extension}"
                expected.append(self.add(f"{module}/build/reports/{name}", f"lint/{module}/{name}", f"{module} {extension}"))
        for module in ("providers/aws", "shared/core"):
            name = "TEST-Suite.xml"
            expected.append(self.add(f"{module}/build/test-results/testDebugUnitTest/{name}", f"junit/{module}/{name}", module))
        for module in ("apps/aws", "apps/aws-wear"):
            expected.append(self.add(f"{module}/build/outputs/apk/debug/app.apk", f"apk/debug/{module}/app.apk", module))
        for module in ("providers/aws", "apps/aws-wear"):
            expected.append(self.add(f"{module}/build/outputs/apk/androidTest/debug/test.apk", f"apk/androidTest/{module}/test.apk", module))
        return expected

    def test_cloud_cost_keeps_same_named_artifacts_separate(self):
        expected = self.add_cloud_cost_outputs()
        result = self.collect(CLOUD_COST_WIDGETS)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_evidence(expected)

    def test_cloud_cost_requires_each_output_type_per_configured_module(self):
        expected = self.add_cloud_cost_outputs()
        missing_outputs = [
            ("apps/aws-wear/build/outputs/apk/debug/app.apk", "apk/debug/apps/aws-wear/app.apk"),
            ("providers/aws/build/outputs/apk/androidTest/debug/test.apk", "apk/androidTest/providers/aws/test.apk"),
            ("shared/core/build/test-results/testDebugUnitTest/TEST-Suite.xml", "junit/shared/core/TEST-Suite.xml"),
            ("apps/aws/build/reports/lint-results-debug.xml", "lint/apps/aws/lint-results-debug.xml"),
        ]
        for source, archive in missing_outputs:
            (self.source / source).unlink()
            expected = [item for item in expected if item[0] != archive]

        result = self.collect(CLOUD_COST_WIDGETS)
        self.assertEqual(result.returncode, 1)
        for _, archive in missing_outputs:
            self.assertIn(f"No CI output found for {Path(archive).parent.as_posix()}", result.stderr.decode())
        self.assert_evidence(expected)

        result = self.collect(CLOUD_COST_WIDGETS, gradle_exit=1)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_evidence(expected)

    def test_checkout_records_manual_tip_or_exact_verified_push(self):
        self.git("init", "--quiet", "-b", "main")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Approved push")
        pushed = self.git("rev-parse", "HEAD")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Current main")
        tip = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", tip)

        manual = self.run_bash(CHECKOUT, self.source, {"REQUESTED_COMMIT": ""})
        self.assertEqual(manual.returncode, 0, manual.stderr.decode())
        self.assertEqual(manual.stdout.decode().strip(), tip)

        webhook = self.run_bash(CHECKOUT, self.source, {"REQUESTED_COMMIT": pushed})
        self.assertEqual(webhook.returncode, 0, webhook.stderr.decode())
        self.assertEqual(webhook.stdout.decode().strip(), pushed)
        self.assertEqual(self.git("rev-parse", "HEAD"), pushed)

        stale_manual = self.run_bash(CHECKOUT, self.source, {"REQUESTED_COMMIT": ""})
        self.assertNotEqual(stale_manual.returncode, 0)
        self.assertFalse(stale_manual.stdout)

        self.git("checkout", "--quiet", "--detach", pushed)
        self.git("commit", "--quiet", "--allow-empty", "-m", "Outside main")
        outside = self.git("rev-parse", "HEAD")
        rejected = self.run_bash(CHECKOUT, self.source, {"REQUESTED_COMMIT": outside})
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(rejected.stdout)


if __name__ == "__main__":
    unittest.main()
