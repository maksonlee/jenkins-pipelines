"""Exercise the publication shell from the Jenkinsfile without Gradle or Play."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


PIPELINE = Path(os.environ.get("JENKINSFILE_UNDER_TEST", Path(__file__).with_name("Jenkinsfile")))
PUBLICATION_STAGE = PIPELINE.read_text().split("stage('Publish to Google Play test track')", 1)[1]
PUBLICATION_SHELL = PUBLICATION_STAGE.split("sh '''", 1)[1].split("'''", 1)[0]
ARTIFACT_DIR = re.search(r"--artifact-dir=([^']+)", PUBLICATION_STAGE)[1]


class ArtifactSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.source = self.workspace / "apps/android/app/build/outputs/bundle/release/app-release.aab"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"verified AAB fixture")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.source.with_suffix(".aab.sha256").write_text(
            f"{self.digest}  {self.source.relative_to(self.workspace)}\n"
        )
        self.artifact_dir = self.workspace / "apps/android" / ARTIFACT_DIR

    def publish_shell(self, **environment):
        return subprocess.run(
            ["bash", "-c", PUBLICATION_SHELL],
            cwd=self.workspace,
            env={**os.environ, "REUSE_AAB_BUILD43": "false", **environment},
            capture_output=True,
            text=True,
        )

    def test_unrelated_aabs_are_not_candidates(self):
        (self.source.parent / "unverified-retained.aab").write_bytes(b"not verified")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.artifact_dir / "stale-publication.aab").write_bytes(b"old candidate")

        result = self.publish_shell()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [path.relative_to(self.artifact_dir).as_posix() for path in self.artifact_dir.rglob("*.aab")],
            ["app-release.aab"],
        )
        self.assertEqual((self.artifact_dir / "app-release.aab").read_bytes(), self.source.read_bytes())
        self.assertTrue((self.source.parent / "unverified-retained.aab").exists())

    def test_changed_source_is_rejected(self):
        self.source.write_bytes(b"changed after verification")

        result = self.publish_shell()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED", result.stdout)

    def test_build43_exception_checks_its_exact_digest(self):
        for digest, accepted in [(self.digest, True), ("0" * 64, False)]:
            with self.subTest(accepted=accepted):
                result = self.publish_shell(
                    REUSE_AAB_BUILD43="true", RELEASE_EXPECTED_AAB_SHA256=digest
                )
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_changed_staged_bytes_are_rejected(self):
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        installer = fake_bin / "install"
        installer.write_text(
            "#!/bin/sh\nset -eu\n"
            f"{shutil.which('install')} \"$@\"\n"
            "for destination; do :; done\n"
            "printf tampered > \"$destination\"\n"
        )
        installer.chmod(0o755)

        result = self.publish_shell(PATH=f"{fake_bin}:{os.environ['PATH']}")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("play-publication/app-release.aab: FAILED", result.stdout)


if __name__ == "__main__":
    unittest.main()
