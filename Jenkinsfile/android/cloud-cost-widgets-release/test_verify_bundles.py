import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_bundles as verifier
from verify_bundles import VerificationError, require_signature, signer_fingerprint, verify_manifest


def manifest(version_code, minimum, target, feature='', metadata='', version='1.0.6'):
    return f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
        package="com.maksonlee.cloudcost.aws" android:versionName="{version}" android:versionCode="{version_code}">
        <uses-sdk android:minSdkVersion="{minimum}" android:targetSdkVersion="{target}"/>
        {feature}<application>{metadata}</application>
    </manifest>'''


WATCH = '<uses-feature android:name="android.hardware.type.watch" android:required="true"/>'
STANDALONE = '<meta-data android:name="com.google.android.wearable.standalone" android:value="true"/>'


class ManifestTest(unittest.TestCase):
    def test_phone_and_standalone_watch(self):
        phone = verify_manifest(manifest('100007', '26', '36'), '1.0.6', '100007', 'phone')
        wear = verify_manifest(manifest('200006', '30', '35', WATCH, STANDALONE, version='1.0.5'), '1.0.5', '200006', 'wear')
        self.assertEqual((phone['form_factor'], wear['form_factor']), ('phone', 'wear'))

    def test_rejects_wrong_version_wrong_target_or_swapped_form_factor(self):
        for xml in (
            manifest('100007', '26', '36', version='1.0.5'),
            manifest('200006', '26', '36'),
            manifest('100007', '26', '35'),
            manifest('100007', '26', '36', WATCH),
        ):
            with self.subTest(manifest=xml), self.assertRaises(VerificationError):
                verify_manifest(xml, '1.0.6', '100007', 'phone')

    def test_rejects_optional_watch_hardware_or_missing_standalone_declaration(self):
        for xml in (
            manifest('200006', '30', '35', WATCH.replace('true', 'false'), STANDALONE, version='1.0.5'),
            manifest('200006', '30', '35', WATCH, version='1.0.5'),
        ):
            with self.subTest(manifest=xml), self.assertRaises(VerificationError):
                verify_manifest(xml, '1.0.5', '200006', 'wear')


class SignatureTest(unittest.TestCase):
    def test_allows_only_expected_self_signed_errors(self):
        self_signed = '''jar verified, with signer errors.
Error:
This jar contains entries whose certificate chain is invalid. Reason: PKIX path building failed: missing trust root
This jar contains entries whose signer certificate is self-signed.
'''
        require_signature(self_signed, 4)
        with self.assertRaises(VerificationError):
            require_signature(self_signed + 'This jar contains entries whose signer certificate expired.\n', 4)

    def test_rejects_unsigned_entries_even_after_jar_verified(self):
        with self.assertRaises(VerificationError):
            require_signature('jar verified.\nThis jar contains unsigned entries.', 0)

    def test_requires_exactly_one_signer(self):
        report = 'Signer #1:\nCertificate #1:\nSHA256: ' + ':'.join(['AE'] * 32)
        self.assertEqual(signer_fingerprint(report), 'ae' * 32)
        with self.assertRaises(VerificationError):
            signer_fingerprint(report + '\nSigner #2:\n')


class PhoneArchiveTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.bundle = self.root / 'apps/aws/build/outputs/bundle/release/aws-release.aab'
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_bytes(b'phone fixture')
        self.certificate = self.evidence / 'upload-certificate-sha256.txt'
        self.certificate.write_text(verifier.UPLOAD_CERTIFICATE_SHA256 + '\n', encoding='ascii')
        self.source_commit = 'ab' * 20
        tool = self.root / 'bundletool.jar'
        tool.write_bytes(b'bundletool fixture')
        self.enterContext(patch.multiple(verifier, BUNDLETOOL=tool, BUNDLETOOL_SHA=verifier.sha256(tool)))
        self.enterContext(patch.dict('os.environ', {
            'PRODUCT_SHA': self.source_commit, 'VERSION_NAME': '1.0.6', 'PHONE_VERSION_CODE': '100007',
        }))

    def test_only_phone_bundle_is_verified_and_archived(self):
        result = verify_manifest(manifest('100007', '26', '36'), '1.0.6', '100007', 'phone') | {
            'sha256': verifier.sha256(self.bundle),
            'upload_certificate_sha256': verifier.UPLOAD_CERTIFICATE_SHA256,
        }
        with patch.object(verifier, 'verify_bundle', return_value=result) as verify:
            verifier.archive_phone_bundle(self.root, self.evidence)
        verify.assert_called_once_with(self.bundle, 'phone', '1.0.6', '100007', verifier.UPLOAD_CERTIFICATE_SHA256)
        archive = self.evidence / 'bundles'
        self.assertEqual({path.name for path in archive.iterdir()}, {'phone-1.0.6-100007.aab', 'SHA256SUMS'})
        self.assertEqual((archive / 'phone-1.0.6-100007.aab').read_bytes(), self.bundle.read_bytes())
        self.assertEqual((archive / 'SHA256SUMS').read_text(encoding='ascii').splitlines(), [
            f"{result['sha256']}  phone-1.0.6-100007.aab",
        ])
        report = json.loads((self.evidence / 'signed-bundles.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source_commit'], self.source_commit)
        self.assertEqual(report['bundles'], [result | {'file': 'phone-1.0.6-100007.aab'}])

    def test_rejects_untrusted_signing_certificate_before_checking_bundle(self):
        self.certificate.write_text('0' * 64 + '\n', encoding='ascii')
        with patch.object(verifier, 'verify_bundle') as verify, self.assertRaisesRegex(VerificationError, 'pinned upload certificate'):
            verifier.archive_phone_bundle(self.root, self.evidence)
        verify.assert_not_called()
