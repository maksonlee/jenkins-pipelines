import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
PUBLISHER = (HERE / 'Jenkinsfile').read_text(encoding='utf-8')
SOURCE = (HERE.parent / 'math-runner-android-build-in-docker' / 'Jenkinsfile').read_text(encoding='utf-8')
EXPECTED_STORED = {
    'SportsDayMath.aab', 'SportsDayMath.aab.sha256.txt',
    'SportsDayMath.aab.signer-sha256', 'release-verification.json',
    'AndroidManifest.xml', 'signature-verification.txt',
    'Android-AndroidBuildReport.json',
}

def extracted(pattern, text):
    match = re.search(pattern, text, re.S)
    if not match:
        raise AssertionError('Expected embedded adapter script is missing')
    return match.group(1)

COMPARE = extracted(r"cat > artifactory-compare\.py <<'PY'\n(.*?)\nPY", SOURCE)
RESTORE = extracted(r"python3 - <<'PY'\n(import pathlib\n\nstored_to_canonical = \{.*?\n)PY", PUBLISHER)


def files(directory, *, omit=None, replace=None):
    directory.mkdir()
    for name in EXPECTED_STORED - ({omit} if omit else set()):
        content = (b'a' * 84 if name == 'SportsDayMath.aab.sha256.txt'
                   else name.encode())
        if name == replace:
            content = b'different'
        (directory / name).write_bytes(content)


class ArtifactoryAdapterTests(unittest.TestCase):
    def run_compare(self, root, mode, other):
        return subprocess.run([sys.executable, '-c', COMPARE, mode, other],
                              cwd=root, capture_output=True, text=True, timeout=10)

    def test_absent_target_requests_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-upload')
            result = self.run_compare(root, 'pre', 'artifactory-existing')
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / 'artifactory-needs-upload').exists())

    def test_existing_same_bytes_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-upload')
            files(root / 'artifactory-existing')
            result = self.run_compare(root, 'pre', 'artifactory-existing')
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((root / 'artifactory-needs-upload').exists())

    def test_existing_different_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-upload')
            files(root / 'artifactory-existing', replace='SportsDayMath.aab')
            self.assertNotEqual(0, self.run_compare(root, 'pre', 'artifactory-existing').returncode)

    def test_partial_target_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-upload')
            files(root / 'artifactory-existing', omit='signature-verification.txt')
            self.assertNotEqual(0, self.run_compare(root, 'pre', 'artifactory-existing').returncode)

    def test_post_requires_complete_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-upload')
            self.assertNotEqual(0, self.run_compare(root, 'post', 'artifactory-readback').returncode)

    def test_publisher_restores_original_checksum_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-raw')
            result = subprocess.run([sys.executable, '-c', RESTORE], cwd=root,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            artifact = root / 'artifact'
            self.assertEqual(7, len(list(artifact.iterdir())))
            self.assertEqual(b'a' * 84, (artifact / 'SportsDayMath.aab.sha256').read_bytes())
            self.assertFalse((artifact / 'SportsDayMath.aab.sha256.txt').exists())

    def test_publisher_rejects_missing_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            files(root / 'artifactory-raw', omit='SportsDayMath.aab.sha256.txt')
            result = subprocess.run([sys.executable, '-c', RESTORE], cwd=root,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(0, result.returncode)

    def test_fixed_repository_and_no_archive_fallback(self):
        self.assertIn('android-snapshots/com/maksonlee/math-runner/jenkins-build-${env.BUILD_NUMBER}-sha256-${sha}-SNAPSHOT/', SOURCE)
        self.assertIn('android-snapshots/com/maksonlee/math-runner/jenkins-build-${params.SOURCE_BUILD_NUMBER}-sha256-${params.EXPECTED_AAB_SHA256}-SNAPSHOT/', PUBLISHER)
        self.assertIn("rtUpload(serverId: 'artifactory'", SOURCE)
        self.assertIn("rtDownload(serverId: 'artifactory'", SOURCE)
        self.assertIn("rtDownload(serverId: 'artifactory'", PUBLISHER)
        self.assertNotIn('copyArtifacts(', PUBLISHER)


if __name__ == '__main__':
    unittest.main()
