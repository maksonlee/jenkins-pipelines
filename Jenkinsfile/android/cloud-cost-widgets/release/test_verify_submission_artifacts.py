import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_submission_artifacts as verifier


WEAR_FILE = 'wear-1.0.8-200009.aab'


class SubmissionArtifactTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'bundles').mkdir()
        self.input_bytes = {'wear': b'wear artifact fixture'}
        self.digests = {name: hashlib.sha256(data).hexdigest() for name, data in self.input_bytes.items()}
        self.source_commit = 'ab' * 20
        self.pipeline_commit = 'cd' * 20
        self.upload_certificate = 'ae' * 32
        self.files = {'wear': self.root / 'bundles' / WEAR_FILE}
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
                    'package': 'com.maksonlee.cloudcost.aws', 'version_name': '1.0.8', 'version_code': '200009',
                    'min_sdk': '30', 'target_sdk': '35', 'form_factor': 'wear', 'sha256': self.digests['wear'],
                    'upload_certificate_sha256': self.upload_certificate, 'file': WEAR_FILE,
                },
            ],
        }
        self.write_report()
        (self.root / 'bundles/SHA256SUMS').write_text(
            f"{self.digests['wear']}  {WEAR_FILE}\n", encoding='ascii',
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
                ('form_factor', 'phone'), ('version_name', '1.0.7'), ('version_code', '200008'), ('target_sdk', '34'),
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
            self.load({'wear': '0' * 64})
        # A phone digest has no place in a Wear OS only release.
        for digests in ({'phone': self.digests['wear'], 'wear': self.digests['wear']}, {'phone': self.digests['wear']}):
            with self.subTest(digests=digests), self.assertRaisesRegex(
                verifier.ArtifactVerificationError, 'Wear OS digest',
            ):
                self.load(digests)

    def test_rejects_malformed_operator_identifiers(self):
        for source, pipeline, digests, field in (
            (self.source_commit + '\n', self.pipeline_commit, self.digests, 'PRODUCT_SHA'),
            (self.source_commit, 'refs/heads/main', self.digests, 'PIPELINE_SHA'),
            (self.source_commit, self.pipeline_commit, {'wear': self.digests['wear'].upper()}, 'SIGNED_WEAR_SHA256'),
            (self.source_commit, self.pipeline_commit, {'wear': ''}, 'SIGNED_WEAR_SHA256'),
            (self.source_commit, self.pipeline_commit, {}, 'Wear OS digest'),
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
        extra = self.root / 'bundles/phone-1.0.10-100011.aab'
        extra.write_bytes(b'phone artifact fixture')
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'exactly the Wear OS AAB'):
            self.load()
        extra.unlink()
        self.files['wear'].unlink()
        with self.assertRaisesRegex(verifier.ArtifactVerificationError, 'exactly the Wear OS AAB'):
            self.load()

    def test_cli_reports_verified_archive_identity(self):
        report_path = self.root / 'verified-input.json'
        with patch.dict('os.environ', {
            'PRODUCT_SHA': self.source_commit, 'PIPELINE_SHA': self.pipeline_commit,
            'SIGNED_BUILD': '19', 'SIGNED_WEAR_SHA256': self.digests['wear'],
        }):
            verifier.main(['verify_submission_artifacts.py', str(self.root), str(report_path)])
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(report['source_commit'], self.source_commit)
        self.assertEqual(report['source_pipeline_commit'], self.pipeline_commit)
        self.assertEqual(report['source_build'], 19)
        self.assertEqual(report['version_names'], {'wear': '1.0.8'})
        self.assertEqual(report['bundle_sha256'], {'200009': self.digests['wear']})
