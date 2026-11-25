import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_submission_artifacts as verifier


PHONE_FILE = 'phone-1.0.12-100013.aab'


class SubmissionArtifactTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'bundles').mkdir()
        self.input_bytes = {'phone': b'phone artifact fixture'}
        self.digests = {name: hashlib.sha256(data).hexdigest() for name, data in self.input_bytes.items()}
        self.source_commit = 'ab' * 20
        self.pipeline_commit = 'cd' * 20
        self.upload_certificate = 'ae' * 32
        self.files = {'phone': self.root / 'bundles' / PHONE_FILE}
        for name, value in (
            ('source-commit.txt', self.source_commit),
            ('pipeline-commit.txt', self.pipeline_commit),
            ('upload-certificate-sha256.txt', self.upload_certificate),
        ):
            (self.root / name).write_text(value + '\n', encoding='ascii')
        for form_factor, path in self.files.items():
            path.write_bytes(self.input_bytes[form_factor])
        self.report = {
            'result': 'passed',
            'source_commit': self.source_commit,
            'bundletool_sha256': verifier.BUNDLETOOL_SHA,
            'bundles': [
                {
                    'package': 'com.maksonlee.cloudcost.aws', 'version_name': '1.0.12', 'version_code': '100013',
                    'min_sdk': '26', 'target_sdk': '36', 'form_factor': 'phone', 'sha256': self.digests['phone'],
                    'upload_certificate_sha256': self.upload_certificate, 'file': PHONE_FILE,
                },
            ],
        }
        self.write_report()
        (self.root / 'bundles/SHA256SUMS').write_text(
            f"{self.digests['phone']}  {PHONE_FILE}\n", encoding='ascii',
        )

    def write_report(self):
        (self.root / 'signed-bundles.json').write_text(json.dumps(self.report), encoding='utf-8')

    def load(self, digests=None):
        return verifier.load_release_bundles(
            self.root, self.source_commit, self.pipeline_commit, self.digests if digests is None else digests,
        )

    def test_loads_exact_verified_bundle_bytes(self):
        self.assertEqual(self.load(), self.input_bytes)

    def test_rejects_a_modified_bundle_even_when_all_reports_match(self):
        for form_factor, path in self.files.items():
            with self.subTest(form_factor=form_factor):
                path.write_bytes(b'changed artifact')
                with self.assertRaisesRegex(verifier.ArtifactVerificationError, f'{form_factor} AAB bytes'):
                    self.load()
                path.write_bytes(self.input_bytes[form_factor])

    def test_rejects_mismatched_source_or_pipeline(self):
        for entry, value in (
            ('source-commit.txt', self.source_commit), ('pipeline-commit.txt', self.pipeline_commit),
        ):
            path = self.root / entry
            for invalid in ('0' * len(value), f' {value}\n'):
                path.write_text(invalid, encoding='ascii')
                with self.subTest(entry=entry), self.assertRaisesRegex(verifier.ArtifactVerificationError, entry):
                    self.load()
            path.write_text(value, encoding='ascii')

    def test_bundles_must_name_the_archived_vault_certificate(self):
        path = self.root / 'upload-certificate-sha256.txt'
        for invalid in ('', 'AE' * 32, f' {self.upload_certificate}\n', f'{self.upload_certificate}\n' * 2):
            path.write_text(invalid, encoding='ascii')
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                verifier.ArtifactVerificationError, 'upload-certificate-sha256.txt',
            ):
                self.load()
        path.write_text('cd' * 32 + '\n', encoding='ascii')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
            self.load()

    def test_rejects_wrong_target_or_signer_in_report(self):
        for index in (0,):
            bundle = self.report['bundles'][index]
            for field, wrong_value in (
                ('form_factor', 'wear'), ('version_name', '1.0.9'), ('version_code', '100012'), ('target_sdk', '35'),
                ('upload_certificate_sha256', '0' * 64),
            ):
                original = bundle[field]
                bundle[field] = wrong_value
                self.write_report()
                with self.subTest(index=index, field=field), self.assertRaisesRegex(
                    verifier.ArtifactVerificationError, 'manifest or signature',
                ):
                    self.load()
                bundle[field] = original
        self.write_report()

    def test_rejects_swapped_or_mismatched_operator_digests(self):
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'manifest or signature'):
            self.load({'phone': '0' * 64})
        # A Wear OS digest has no place in a phone only release.
        for digests in ({'phone': self.digests['phone'], 'wear': self.digests['phone']}, {'wear': self.digests['phone']}):
            with self.subTest(digests=digests), self.assertRaisesRegex(
                verifier.ArtifactVerificationError, 'phone digest',
            ):
                self.load(digests)

    def test_rejects_malformed_operator_identifiers(self):
        for source, pipeline, digests, field in (
            (self.source_commit + '\n', self.pipeline_commit, self.digests, 'PRODUCT_SHA'),
            (self.source_commit, 'refs/heads/main', self.digests, 'PIPELINE_SHA'),
            (self.source_commit, self.pipeline_commit, {'phone': self.digests['phone'].upper()}, 'SIGNED_PHONE_SHA256'),
            (self.source_commit, self.pipeline_commit, {'phone': ''}, 'SIGNED_PHONE_SHA256'),
            (self.source_commit, self.pipeline_commit, {}, 'phone digest'),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(verifier.ArtifactVerificationError, field):
                verifier.load_release_bundles(self.root, source, pipeline, digests)

    def test_rejects_other_or_reordered_entries_in_sha256sums(self):
        sums = self.root / 'bundles/SHA256SUMS'
        original = sums.read_text(encoding='ascii')
        for content in (
            original + '0' * 64 + '  different.aab\n',
            original * 2,
        ):
            sums.write_text(content, encoding='ascii')
            with self.subTest(content=content), self.assertRaisesRegex(verifier.ArtifactVerificationError, 'SHA256SUMS'):
                self.load()
        sums.write_text(original, encoding='ascii')

    def test_rejects_a_missing_or_extra_bundle(self):
        extra = self.root / 'bundles/wear-1.0.10-200011.aab'
        extra.write_bytes(b'wear artifact fixture')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'exactly the phone AAB'):
            self.load()
        extra.unlink()
        self.files['phone'].unlink()
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'exactly the phone AAB'):
            self.load()

    def test_cli_reports_verified_archive_identity(self):
        report_path = self.root / 'verified-input.json'
        with patch.dict('os.environ', {
            'PRODUCT_SHA': self.source_commit, 'PIPELINE_SHA': self.pipeline_commit,
            'SIGNED_BUILD': '19', 'SIGNED_PHONE_SHA256': self.digests['phone'],
        }):
            verifier.main(['verify_submission_artifacts.py', str(self.root), str(report_path)])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(report['source_commit'], self.source_commit)
        self.assertEqual(report['source_pipeline_commit'], self.pipeline_commit)
        self.assertEqual(report['source_build'], 19)
        self.assertEqual(report['version_names'], {'phone': '1.0.12'})
        self.assertEqual(report['bundle_sha256'], {'100013': self.digests['phone']})
