import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_submission_artifacts as verifier


class SubmissionArtifactTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'bundles').mkdir()
        self.input_bytes = {100006: b'phone artifact fixture', 200006: b'wear artifact fixture'}
        digests = {code: hashlib.sha256(data).hexdigest() for code, data in self.input_bytes.items()}
        digest_patch = patch.object(verifier, 'BUNDLE_SHA256', digests)
        digest_patch.start()
        self.addCleanup(digest_patch.stop)
        for name, value in (
            ('source-commit.txt', verifier.SOURCE_SHA),
            ('pipeline-commit.txt', verifier.ARCHIVE_PIPELINE_SHA),
            ('upload-certificate-sha256.txt', verifier.UPLOAD_CERTIFICATE_SHA256),
        ):
            (self.root / name).write_text(value + '\n', encoding='ascii')
        sums = []
        reports = []
        for code, (form, file, minimum, target) in verifier.BUNDLE_FILES.items():
            (self.root / 'bundles' / file).write_bytes(self.input_bytes[code])
            sums.append(f'{digests[code]}  {file}\n')
            reports.append({
                'package': verifier.PACKAGE_NAME, 'version_name': '1.0.5', 'version_code': str(code),
                'min_sdk': minimum, 'target_sdk': target, 'form_factor': form, 'sha256': digests[code],
                'upload_certificate_sha256': verifier.UPLOAD_CERTIFICATE_SHA256, 'file': file,
            })
        self.report = {'result': 'passed', 'source_commit': verifier.SOURCE_SHA, 'bundles': reports}
        (self.root / 'signed-bundles.json').write_text(json.dumps(self.report), encoding='utf-8')
        (self.root / 'bundles/SHA256SUMS').write_text(''.join(sums), encoding='ascii')

    def test_loads_exact_verified_bundle_bytes(self):
        self.assertEqual(verifier.load_bundles(self.root), self.input_bytes)

    def test_rejects_modified_bundle_even_when_all_reports_match(self):
        (self.root / 'bundles/phone-1.0.5-100006.aab').write_bytes(b'changed artifact')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'AAB bytes'):
            verifier.load_bundles(self.root)

    def test_rejects_mismatched_source_or_pipeline(self):
        for entry, value in (
            ('source-commit.txt', verifier.SOURCE_SHA), ('pipeline-commit.txt', verifier.ARCHIVE_PIPELINE_SHA),
        ):
            path = self.root / entry
            path.write_text('0' * 40, encoding='ascii')
            with self.subTest(entry=entry), self.assertRaisesRegex(verifier.ArtifactVerificationError, entry):
                verifier.load_bundles(self.root)
            path.write_text(value, encoding='ascii')

    def test_rejects_wrong_target_or_signer_in_report(self):
        self.report['bundles'][1]['form_factor'] = 'phone'
        (self.root / 'signed-bundles.json').write_text(json.dumps(self.report), encoding='utf-8')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
            verifier.load_bundles(self.root)
        self.report['bundles'][1]['form_factor'] = 'wear'
        self.report['bundles'][1]['upload_certificate_sha256'] = '0' * 64
        (self.root / 'signed-bundles.json').write_text(json.dumps(self.report), encoding='utf-8')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
            verifier.load_bundles(self.root)

    def test_rejects_other_entries_in_sha256sums(self):
        with (self.root / 'bundles/SHA256SUMS').open('a', encoding='ascii') as sums:
            sums.write('0' * 64 + '  different.aab\n')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'SHA256SUMS'):
            verifier.load_bundles(self.root)
