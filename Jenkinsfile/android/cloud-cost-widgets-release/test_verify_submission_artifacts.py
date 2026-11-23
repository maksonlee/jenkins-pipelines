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
        self.input_bytes = b'phone artifact fixture'
        self.digest = hashlib.sha256(self.input_bytes).hexdigest()
        self.source_commit = 'ab' * 20
        self.pipeline_commit = 'cd' * 20
        self.bundle = self.root / 'bundles' / 'phone-1.0.7-100008.aab'
        for name, value in (
            ('source-commit.txt', self.source_commit),
            ('pipeline-commit.txt', self.pipeline_commit),
            ('upload-certificate-sha256.txt', verifier.UPLOAD_CERTIFICATE_SHA256),
        ):
            (self.root / name).write_text(value + '\n', encoding='ascii')
        self.bundle.write_bytes(self.input_bytes)
        self.report = {
            'result': 'passed',
            'source_commit': self.source_commit,
            'bundletool_sha256': verifier.BUNDLETOOL_SHA,
            'bundles': [{
                'package': 'com.maksonlee.cloudcost.aws', 'version_name': '1.0.7', 'version_code': '100008',
                'min_sdk': '26', 'target_sdk': '36', 'form_factor': 'phone', 'sha256': self.digest,
                'upload_certificate_sha256': verifier.UPLOAD_CERTIFICATE_SHA256, 'file': self.bundle.name,
            }],
        }
        self.write_report()
        (self.root / 'bundles/SHA256SUMS').write_text(f'{self.digest}  {self.bundle.name}\n', encoding='ascii')

    def write_report(self):
        (self.root / 'signed-bundles.json').write_text(json.dumps(self.report), encoding='utf-8')

    def load(self):
        return verifier.load_phone_bundle(self.root, self.source_commit, self.pipeline_commit, self.digest)

    def test_loads_exact_verified_bundle_bytes(self):
        self.assertEqual(self.load(), self.input_bytes)

    def test_rejects_modified_bundle_even_when_all_reports_match(self):
        self.bundle.write_bytes(b'changed artifact')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'AAB bytes'):
            self.load()

    def test_rejects_mismatched_source_or_pipeline(self):
        for entry, value in (
            ('source-commit.txt', self.source_commit), ('pipeline-commit.txt', self.pipeline_commit),
            ('upload-certificate-sha256.txt', verifier.UPLOAD_CERTIFICATE_SHA256),
        ):
            path = self.root / entry
            for invalid in ('0' * len(value), f' {value}\n'):
                path.write_text(invalid, encoding='ascii')
                with self.subTest(entry=entry), self.assertRaisesRegex(verifier.ArtifactVerificationError, entry):
                    self.load()
            path.write_text(value, encoding='ascii')

    def test_rejects_wrong_target_or_signer_in_report(self):
        bundle = self.report['bundles'][0]
        for field, wrong_value in (
            ('form_factor', 'wear'), ('version_name', '1.0.6'), ('version_code', '100007'), ('target_sdk', '35'),
            ('upload_certificate_sha256', '0' * 64),
        ):
            original = bundle[field]
            bundle[field] = wrong_value
            self.write_report()
            with self.subTest(field=field), self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
                self.load()
            bundle[field] = original

    def test_rejects_operator_digest_mismatch(self):
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
            verifier.load_phone_bundle(self.root, self.source_commit, self.pipeline_commit, '0' * 64)

    def test_rejects_malformed_operator_identifiers(self):
        for source, pipeline, digest, field in (
            (self.source_commit + '\n', self.pipeline_commit, self.digest, 'PRODUCT_SHA'),
            (self.source_commit, 'refs/heads/main', self.digest, 'PIPELINE_SHA'),
            (self.source_commit, self.pipeline_commit, self.digest.upper(), 'SIGNED_SHA256'),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(verifier.ArtifactVerificationError, field):
                verifier.load_phone_bundle(self.root, source, pipeline, digest)

    def test_rejects_other_entries_in_sha256sums(self):
        with (self.root / 'bundles/SHA256SUMS').open('a', encoding='ascii') as sums:
            sums.write('0' * 64 + '  different.aab\n')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'SHA256SUMS'):
            self.load()

    def test_rejects_extra_bundle_even_when_reports_match(self):
        (self.root / 'bundles/wear-1.0.6-200007.aab').write_bytes(b'wear artifact fixture')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'exactly one phone AAB'):
            self.load()

    def test_cli_reports_verified_archive_identity(self):
        report_path = self.root / 'verified-input.json'
        with patch.dict('os.environ', {
            'PRODUCT_SHA': self.source_commit, 'PIPELINE_SHA': self.pipeline_commit,
            'SIGNED_BUILD': '17', 'SIGNED_SHA256': self.digest,
        }):
            verifier.main(['verify_submission_artifacts.py', str(self.root), str(report_path)])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(report['source_commit'], self.source_commit)
        self.assertEqual(report['source_pipeline_commit'], self.pipeline_commit)
        self.assertEqual(report['source_build'], 17)
        self.assertEqual(report['bundle_sha256'], {'100008': self.digest})
