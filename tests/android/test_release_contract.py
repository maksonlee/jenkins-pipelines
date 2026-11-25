"""Exercise the androidRelease library scripts with fake Java tools; no Gradle, Play, Vault or real keys."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASH = os.environ.get("BASH", "bash")
STEP = (ROOT / "vars/androidRelease.groovy").read_text(encoding="utf-8")
BUNDLE = (ROOT / "vars/androidBundle.groovy").read_text(encoding="utf-8")
UPLOAD = (ROOT / "vars/playUpload.groovy").read_text(encoding="utf-8")
SCRIPTS = ROOT / "resources/android/release"
RESOLVE, NOTES, VERIFY, STAGE = (
    (SCRIPTS / name).read_text(encoding="utf-8")
    for name in ("resolve-commit.sh", "check-release-notes.sh", "verify-aab.sh", "stage-verified-aab.sh")
)
SIGNER = "AB" * 32


class AndroidReleaseContractTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.bin = self.workspace / "bin"
        self.bin.mkdir()

    def run_bash(self, script, settings=None, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RELEASE_", "PLAY_", "BUNDLETOOL_", "EXPECTED_"))}
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(settings or {})
        return subprocess.run([BASH], cwd=cwd or self.workspace, input=script.encode(), capture_output=True, env=env)

    def exe(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=ci@example.invalid", *args],
            cwd=self.workspace, capture_output=True, check=True, text=True,
        ).stdout.strip()

    def test_bash_syntax_and_step_loads_every_script(self):
        for script in (RESOLVE, NOTES, VERIFY, STAGE):
            result = subprocess.run([BASH, "-n"], input=script.encode(), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        # Every script is loaded by a library step or, by name, by a release Jenkinsfile.
        loaded = set()
        for user in list(ROOT.glob("vars/*.groovy")) + list(ROOT.glob("Jenkinsfile/android/*/release/Jenkinsfile")):
            loaded |= set(re.findall(r"libraryResource\('android/release/([^']+\.sh)'\)", user.read_text(encoding="utf-8")))
        self.assertEqual(loaded, {p.name for p in SCRIPTS.glob("*.sh")})

    def test_no_release_job_puts_signing_passwords_on_the_gradle_command_line(self):
        self.assertIn("passSigningProperties    : cfg.get('passSigningProperties', false) as boolean", STEP)
        jobs = sorted(ROOT.glob("Jenkinsfile/android/*/release/Jenkinsfile"))
        self.assertGreaterEqual(len(jobs), 5)
        for job in jobs:
            self.assertNotIn("passSigningProperties: true", job.read_text(encoding="utf-8"), job)

    def test_parameters_reach_scripts_explicitly(self):
        # A job's first build exports no parameter environment variables, so scripts get them through withEnv.
        self.assertIn('"PLAY_TRACK=${params.PLAY_TRACK}"', STEP)
        for argument in ("track: params.PLAY_TRACK", "releaseStatus: params.PLAY_RELEASE_STATUS",
                         "expectedSha256: env.RELEASE_AAB_SHA256"):
            self.assertIn(argument, STEP)
        for setting in ('"PLAY_TRACK=${cfg.track}"', '"PLAY_RELEASE_STATUS=${cfg.releaseStatus}"',
                        '"EXPECTED_AAB_SHA256=${cfg.expectedSha256}"', '"PLAY_ROLLOUT_FRACTION=${cfg.rolloutFraction ?: \'\'}"'):
            self.assertIn(setting, UPLOAD)

    def test_vault_certificate_is_read_only_when_the_project_enables_it(self):
        lookup = BUNDLE.index("vaultKey: 'certificate_sha256'")
        self.assertLess(BUNDLE.index("if (cfg.certificateVaultPath) {"), lookup)
        self.assertEqual(BUNDLE.count("certificate_sha256'"), 1)
        self.assertIn('"RELEASE_VERIFY_SIGNER=${cfg.certificateVaultPath ? \'true\' : \'false\'}"', BUNDLE)
        self.assertIn("certificateVaultPath: config.verifyUploadCertificate ? config.keystoreVaultPath : null", STEP)
        self.assertNotIn("vaultKey", STEP)
        # The jobs with their own Jenkinsfile only record the signer.
        for job in ROOT.glob("Jenkinsfile/android/*/release/Jenkinsfile"):
            self.assertNotIn("certificateVaultPath", job.read_text(encoding="utf-8"), job)
        self.assertIn("verifyUploadCertificate  : cfg.verifyUploadCertificate == true", STEP)
        self.assertNotIn("uploadCertificateSha256", STEP)

    def test_native_release_jobs_sign_with_the_shared_upload_key(self):
        shared = "secret/jenkins/mobile/shared/upload-key"
        self.assertIn(f"keystoreVaultPath        : (cfg.keystoreVaultPath ?: '{shared}') as String", STEP)
        self.assertNotIn("mobile/app", STEP)
        jobs = sorted(ROOT.glob("Jenkinsfile/android/*/release/Jenkinsfile"))
        self.assertGreaterEqual(len(jobs), 5)
        for job in jobs:
            text = job.read_text(encoding="utf-8")
            self.assertNotIn("secret/jenkins/mobile/app/", text, job)
            # termstay's own script reads its own key from a path kept outside this repository.
            if "androidRelease(" not in text and job.parent.parent.name != "termstay":
                self.assertIn(shared, text, job)

    def test_play_credentials_are_read_only_after_the_approval_input(self):
        # playUpload is the only place that reads the Play service account, and every caller reaches it after
        # its approval input.
        service = "secret/jenkins/mobile/shared/play-service"
        self.assertEqual(UPLOAD.count(service), 1)
        for step in ("play/play-preflight.sh", "play/play-upload.sh", "release_notes.py"):
            self.assertIn(step, UPLOAD)
        self.assertNotIn(service, STEP)
        self.assertNotIn("libraryResource('play/", STEP)
        self.assertGreater(STEP.index("playUpload("), STEP.index("input message:"))
        callers = 0
        for user in list(ROOT.glob("vars/*.groovy")) + list((ROOT / "Jenkinsfile").rglob("Jenkinsfile")):
            text = user.read_text(encoding="utf-8")
            for call in ("playUpload(", "flutterAndroid.playPublish("):
                if call in text and "input message:" in text:
                    callers += 1
                    self.assertGreater(text.index(call), text.index("input message:"), user)
                elif call in text:
                    self.assertEqual(user.name, "flutterAndroid.groovy", user)
        self.assertEqual(callers, 3)
        self.assertIn("publishPlayListings: false", STEP)
        # Gradle only builds; no Gradle Play Publisher task or option is left in the step.
        for gone in ("publishRelease", "--artifact-dir", "playServiceVaultPath"):
            self.assertNotIn(gone, STEP)
        # The store listing goes only for projects that opt in.
        self.assertIn("metadataRoot: config.publishListingsWithRelease ? playRoot : ''", STEP)
        self.assertIn('"PLAY_METADATA_ROOT=${cfg.metadataRoot ?: \'\'}"', UPLOAD)
        self.assertNotIn("githubPush", STEP)

    def test_resolve_commit_requires_branch_history_clean_tree_and_no_forbidden_paths(self):
        self.git("init", "--quiet", "-b", "main")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Release")
        release = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", release)

        ok = self.run_bash(RESOLVE, {"RELEASE_BRANCH": "main"})
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        self.assertEqual(ok.stdout.decode().strip(), release)

        self.git("commit", "--quiet", "--allow-empty", "-m", "Not on main")
        outside = self.run_bash(RESOLVE, {"RELEASE_BRANCH": "main"})
        self.assertNotEqual(outside.returncode, 0)
        self.assertIn("refusing to expose signing credentials", outside.stderr.decode())
        self.assertFalse(outside.stdout)
        self.git("checkout", "--quiet", "--detach", release)

        forbidden = self.workspace / "app/src/main/assets/offline_pack.zip"
        forbidden.parent.mkdir(parents=True)
        forbidden.write_bytes(b"pack")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Bundle a pack")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        rejected = self.run_bash(RESOLVE, {
            "RELEASE_BRANCH": "main", "RELEASE_FORBIDDEN_PATHS": "\napp/src/main/assets/offline_pack.zip\n"})
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must not contain app/src/main/assets/offline_pack.zip", rejected.stderr.decode())

        (self.workspace / "untracked.txt").write_text("x", encoding="utf-8")
        dirty = self.run_bash(RESOLVE, {"RELEASE_BRANCH": "main"})
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("dirty", dirty.stderr.decode())

    def test_release_notes_need_track_or_default_file_for_every_locale(self):
        notes = self.workspace / "notes"
        settings = {"RELEASE_NOTES_DIR": str(notes), "PLAY_TRACK": "internal"}
        missing = self.run_bash(NOTES, settings)
        self.assertNotEqual(missing.returncode, 0)

        (notes / "en-US").mkdir(parents=True)
        (notes / "en-US/internal.txt").write_text("Internal notes", encoding="utf-8")
        (notes / "zh-TW").mkdir()
        (notes / "zh-TW/default.txt").write_text("預設說明", encoding="utf-8")
        ok = self.run_bash(NOTES, settings)
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        self.assertEqual(ok.stdout.decode().splitlines(), ["en-US: internal.txt", "zh-TW: default.txt"])

        (notes / "zh-CN").mkdir()
        (notes / "zh-CN/production.txt").write_text("Only production", encoding="utf-8")
        (notes / "zh-CN/default.txt").write_text("", encoding="utf-8")
        rejected = self.run_bash(NOTES, settings)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("zh-CN", rejected.stderr.decode())

    def fake_bundle(self, package="com.example.app", signer=SIGNER, jar_report="jar verified.", entries="base/manifest/AndroidManifest.xml"):
        release_dir = self.workspace / "app/build/outputs/bundle/release"
        release_dir.mkdir(parents=True, exist_ok=True)
        aab = release_dir / "app-release.aab"
        aab.write_bytes(b"signed bundle fixture")
        bundletool = self.workspace / "bundletool.jar"
        bundletool.write_bytes(b"bundletool fixture")
        self.exe("java", f"""
case "$*" in
  *" validate "*) echo 'The App Bundle is valid.' ;;
  *"--xpath=/manifest/@package") echo '{package}' ;;
  *"--xpath=/manifest/@android:versionCode") echo '42' ;;
  *"--xpath=/manifest/@android:versionName") echo '1.2.3' ;;
  *) exit 64 ;;
esac
""")
        self.exe("jarsigner", f"echo '{jar_report}'\n")
        self.exe("keytool", f"echo 'Certificate fingerprints:'\necho '         SHA256: {':'.join(signer[i:i + 2] for i in range(0, 64, 2))}'\n")
        self.exe("unzip", f"printf '%s\\n' {entries}\n")
        return aab, {
            "RELEASE_AAB": str(aab.relative_to(self.workspace)),
            "RELEASE_PACKAGE": "com.example.app",
            "BUNDLETOOL_JAR": str(bundletool),
            "BUNDLETOOL_SHA256": hashlib.sha256(bundletool.read_bytes()).hexdigest(),
            "EXPECTED_UPLOAD_CERT_SHA256": SIGNER.lower(),
            "RELEASE_VERIFY_SIGNER": "true",
        }

    def test_verify_records_manifest_signer_and_checksum(self):
        aab, settings = self.fake_bundle()
        result = self.run_bash(VERIFY, settings)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        release_dir = aab.parent
        self.assertEqual((release_dir / "release-manifest.txt").read_text(),
                         f"package=com.example.app\nversionCode=42\nversionName=1.2.3\nsignerSha256={SIGNER}\n")
        self.assertEqual((release_dir / "app-release.aab.sha256").read_text(),
                         f"{hashlib.sha256(aab.read_bytes()).hexdigest()}  app-release.aab\n")
        self.assertIn("matches the approved upload certificate", result.stdout.decode())

    def test_verify_without_an_approved_certificate_records_the_signer_only(self):
        aab, settings = self.fake_bundle(signer="CD" * 32)
        del settings["EXPECTED_UPLOAD_CERT_SHA256"]
        settings["RELEASE_VERIFY_SIGNER"] = "false"
        result = self.run_bash(VERIFY, settings)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("Google Play checks the upload key", result.stdout.decode())
        self.assertIn(f"signerSha256={'CD' * 32}", (aab.parent / "release-manifest.txt").read_text())
        self.assertTrue((aab.parent / "app-release.aab.sha256").exists())

        unsigned = self.fake_bundle(jar_report="jar verified. Warning: unsigned entries")[1]
        unsigned["RELEASE_VERIFY_SIGNER"] = "false"
        result = self.run_bash(VERIFY, unsigned)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsigned entries", result.stderr.decode())

    def test_verify_fails_closed(self):
        cases = {
            "package": ({"package": "com.example.other"}, {}, "does not match com.example.app"),
            "signer": ({"signer": "CD" * 32}, {}, "does not match the approved upload certificate"),
            "unsigned": ({"jar_report": "jar verified. Warning: unsigned entries"}, {}, "unsigned entries"),
            "forbidden": ({"entries": "base/assets/offline_pack.zip"}, {"RELEASE_FORBIDDEN_AAB_ENTRY": r"offline_pack\.zip$"}, "matching"),
            "bundletool": ({}, {"BUNDLETOOL_SHA256": "0" * 64}, "pinned SHA-256"),
            "no signer": ({}, {"EXPECTED_UPLOAD_CERT_SHA256": ""}, "expected upload certificate"),
        }
        for name, (bundle, overrides, message) in cases.items():
            with self.subTest(name):
                aab, settings = self.fake_bundle(**bundle)
                result = self.run_bash(VERIFY, {**settings, **overrides})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr.decode())
                self.assertFalse((aab.parent / "app-release.aab.sha256").exists())

        aab, settings = self.fake_bundle()
        (aab.parent / "other.aab").write_bytes(b"second bundle")
        result = self.run_bash(VERIFY, settings)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one AAB", result.stderr.decode())

    def staged(self):
        aab, settings = self.fake_bundle()
        verified = self.run_bash(VERIFY, settings)
        self.assertEqual(verified.returncode, 0, verified.stderr.decode())
        stale = self.workspace / "app/build/outputs/play-publication/stale.aab"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale bundle")
        return aab, {"RELEASE_AAB": settings["RELEASE_AAB"], "PUBLICATION_DIR": "app/build/outputs/play-publication"}

    def test_stage_copies_only_the_verified_bundle(self):
        aab, settings = self.staged()
        result = self.run_bash(STAGE, settings)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        publication = self.workspace / "app/build/outputs/play-publication"
        self.assertEqual(sorted(p.name for p in publication.iterdir()), ["app-release.aab"])
        self.assertEqual((publication / "app-release.aab").read_bytes(), aab.read_bytes())

    def test_stage_rejects_a_changed_bundle_or_copy(self):
        aab, settings = self.staged()
        aab.write_bytes(b"changed after verification")
        changed = self.run_bash(STAGE, settings)
        self.assertNotEqual(changed.returncode, 0)

        aab.write_bytes(b"signed bundle fixture")
        self.exe("install", 'for destination; do :; done\nprintf tampered > "$destination"\n')
        tampered = self.run_bash(STAGE, settings)
        self.assertNotEqual(tampered.returncode, 0)


if __name__ == "__main__":
    unittest.main()
