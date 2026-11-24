"""Exercise the androidCi library scripts and Jenkins evidence contract without Gradle, Docker or Jenkins."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASH = os.environ.get("BASH", "bash")
STEP = (ROOT / "vars/androidCi.groovy").read_text(encoding="utf-8")
SCRIPTS = ROOT / "resources/android/ci"
COLLECT, BUILD, DEVICE, CHECKOUT, UNCHANGED = (
    (SCRIPTS / name).read_text(encoding="utf-8")
    for name in ("collect-evidence.sh", "gradle.sh", "device.sh", "checkout-verify.sh", "verify-unchanged.sh")
)

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


class AndroidCiContractTest(unittest.TestCase):
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
        env = {name: value for name, value in os.environ.items() if not name.startswith("NATIVE_ANDROID_")}
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

    def test_bash_syntax(self):
        for script in (COLLECT, BUILD, DEVICE, CHECKOUT, UNCHANGED):
            result = subprocess.run([BASH, "-n"], input=script.encode(), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_step_loads_every_script_and_uses_no_signing_material(self):
        loaded = set(re.findall(r"libraryResource\('android/ci/([^']+)'\)", STEP))
        self.assertEqual(loaded, {p.name for p in SCRIPTS.glob("*.sh")})
        for forbidden in ("keystoreVaultPath", "withVault", "upload_jks_b64", "rtUpload", "api.github.com"):
            self.assertNotIn(forbidden, STEP)
        self.assertIn("githubPush()", STEP)
        self.assertIn("archiveArtifacts artifacts: 'evidence/**,ci-evidence.tar.gz'", STEP)
        junit_steps = re.findall(r"^\s*junit ([^\n]+)$", STEP, re.M)
        self.assertEqual(len(junit_steps), 2)
        for step in junit_steps:
            self.assertIn("skipPublishingChecks: true", step)

    def test_skip_ci_applies_only_to_opted_in_push_builds(self):
        self.assertIn("honorSkipCi         : cfg.honorSkipCi == true", STEP)
        self.assertIn("config.honorSkipCi && !currentBuild.getBuildCauses('com.cloudbees.jenkins.GitHubPushCause').isEmpty()", STEP)
        for stage in ("stage('Build and check debug')", "stage('Collect build evidence')"):
            when = STEP[STEP.index(stage):].split("steps {", 1)[0]
            self.assertIn("env.NATIVE_ANDROID_SKIPPED != 'true'", when)
        self.assertIn("currentBuild.result = 'NOT_BUILT'", STEP)

    def test_in_process_kotlin_is_opt_in(self):
        self.assertIn("kotlinInProcess     : cfg.kotlinInProcess == true", STEP)
        self.assertIn("if (config.kotlinInProcess) {\n        gradleArgs << '-Pkotlin.compiler.execution.strategy=in-process'", STEP)
        jobs = {p.parent.parent.name: p.read_text(encoding="utf-8") for p in ROOT.glob("Jenkinsfile/android/*/ci/Jenkinsfile")}
        self.assertEqual(sorted(name for name, text in jobs.items() if "kotlinInProcess: true" in text), ["cloud-cost-widgets"])

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

    def test_single_module_app_without_android_tests(self):
        expected = [
            self.add("app/build/test-results/testDebugUnitTest/TEST-a.xml", "junit/app/TEST-a.xml", "tests"),
            self.add("app/build/reports/lint-results-debug.xml", "lint/app/lint-results-debug.xml", "lint"),
            self.add("app/build/outputs/apk/debug/app-debug.apk", "apk/debug/app/app-debug.apk", "apk"),
        ]
        result = self.collect({
            "DEBUG_APK_MODULES": "app", "ANDROID_TEST_MODULES": "", "LINT_MODULES": "app",
            "FLAT_APP_EVIDENCE": "false", "UNIT_TEST_TASKS": "app:testDebugUnitTest",
        })
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_evidence(expected)

    def test_checkout_requires_clean_fetched_branch_tip(self):
        self.git("init", "--quiet", "-b", "master")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Older")
        older = self.git("rev-parse", "HEAD")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Tip")
        tip = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/master", tip)

        result = self.run_bash(CHECKOUT, self.source, {"BRANCH": "master"})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), tip)

        missing = self.run_bash(CHECKOUT, self.source, {})
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(missing.stdout)

        other_branch = self.run_bash(CHECKOUT, self.source, {"BRANCH": "main"})
        self.assertNotEqual(other_branch.returncode, 0)
        self.assertFalse(other_branch.stdout)

        self.git("checkout", "--quiet", "--detach", older)
        stale = self.run_bash(CHECKOUT, self.source, {"BRANCH": "master"})
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("does not match", stale.stderr.decode())
        self.assertFalse(stale.stdout)

        self.git("checkout", "--quiet", "--detach", tip)
        (self.source / "unexpected.txt").write_text("unexpected workspace output", encoding="utf-8")
        dirty = self.run_bash(CHECKOUT, self.source, {"BRANCH": "master"})
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("dirty before build", dirty.stderr.decode())

    def test_unchanged_check_rejects_moved_head_or_build_output_in_source(self):
        self.git("init", "--quiet", "-b", "main")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Tip")
        tip = self.git("rev-parse", "HEAD")
        ok = self.run_bash(UNCHANGED, self.workspace, {"SOURCE_COMMIT": tip})
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        (self.source / "generated.txt").write_text("written by the build", encoding="utf-8")
        dirty = self.run_bash(UNCHANGED, self.workspace, {"SOURCE_COMMIT": tip})
        self.assertNotEqual(dirty.returncode, 0)

    def fake_gradle(self, body):
        android_home = self.workspace / "android-user-home"
        android_home.mkdir(exist_ok=True)
        (android_home / "debug.keystore").touch()
        gradlew = self.source / "gradlew"
        gradlew.write_bytes(body)
        gradlew.chmod(0o755)
        return f'export ANDROID_USER_HOME="{android_home.as_posix()}"\n{BUILD}'

    def test_gradle_arguments_pass_one_per_line_without_word_splitting(self):
        script = self.fake_gradle(b"#!/bin/bash\nprintf '<%s>\\n' \"$@\"\n")
        result = self.run_bash(script, self.source, {
            "GRADLE_ARGS": ":app:testDebugUnitTest\n:app:lintDebug\n\n--continue\n--max-workers=2",
        })
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().splitlines(), [
            "<:app:testDebugUnitTest>", "<:app:lintDebug>", "<--continue>", "<--max-workers=2>",
        ])
        missing = self.run_bash(script, self.source, {})
        self.assertNotEqual(missing.returncode, 0)

    def test_traced_build_retains_plain_log_and_gradle_failure(self):
        script = self.fake_gradle(
            b"#!/bin/bash\nif [[ $1 == --version ]]; then echo 'Gradle 9.5.1'; exit; fi\necho 'check failed' >&2\nexit 7\n")

        result = self.run_bash(script, self.source, {"TRACE": "true", "GRADLE_ARGS": "nativeAndroidCi\n--continue"})

        self.assertEqual(result.returncode, 7, result.stderr.decode())
        self.assertEqual((self.evidence / "gradle.log").read_text().strip(), "check failed")
        self.assertEqual((self.evidence / "gradle-version.txt").read_text().strip(), "Gradle 9.5.1")
        self.assertIn("nativeAndroidCi --continue", (self.evidence / "build-command.txt").read_text())

    def test_device_skips_without_both_apks_or_runner_with_evidence(self):
        settings = {"REPO": "learn-chess", "DEVICE_RUNNER": "tools/native-android-ci/run_device.py"}
        for app, tests, reason in (
            (False, False, "Exactly one app APK and one instrumentation APK"),
            (True, True, "does not contain the device runner"),
        ):
            with self.subTest(app=app, tests=tests):
                for enabled, name in ((app, "apk/debug/app.apk"), (tests, "apk/androidTest/test.apk")):
                    if enabled:
                        path = self.evidence / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.touch()
                result = self.run_bash(DEVICE, self.workspace, settings)
                self.assertEqual(result.returncode, 3, result.stderr.decode())
                self.assertIn(reason, (self.evidence / "device/preflight.log").read_text())


if __name__ == "__main__":
    unittest.main()
