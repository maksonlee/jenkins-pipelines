import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import verify_bundles as verifier
from verify_bundles import VerificationError, require_signature, signer_fingerprint, verify_manifest


def manifest(version_code, minimum, target, feature='', metadata='', version='1.0.11'):
    return f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
        package="com.maksonlee.cloudcost.aws" android:versionName="{version}" android:versionCode="{version_code}">
        <uses-sdk android:minSdkVersion="{minimum}" android:targetSdkVersion="{target}"/>
        {feature}<application>{metadata}</application>
    </manifest>'''


WATCH = '<uses-feature android:name="android.hardware.type.watch" android:required="true"/>'
STANDALONE = '<meta-data android:name="com.google.android.wearable.standalone" android:value="true"/>'


class ManifestTest(unittest.TestCase):
    def test_phone_and_standalone_watch(self):
        phone = verify_manifest(manifest('100009', '26', '36'), '1.0.11', '100009', 'phone')
        wear = verify_manifest(manifest('200006', '30', '35', WATCH, STANDALONE, version='1.0.5'), '1.0.5', '200006', 'wear')
        self.assertEqual((phone['form_factor'], wear['form_factor']), ('phone', 'wear'))

    def test_rejects_wrong_version_wrong_target_or_swapped_form_factor(self):
        for xml in (
            manifest('100009', '26', '36', version='1.0.7'),
            manifest('100008', '26', '36'),
            manifest('200006', '26', '36'),
            manifest('100009', '26', '35'),
            manifest('100009', '26', '36', WATCH),
        ):
            with self.subTest(manifest=xml), self.assertRaises(VerificationError):
                verify_manifest(xml, '1.0.11', '100009', 'phone')

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

    def test_bundle_signer_must_be_the_vault_upload_key(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / 'aws-release.aab'
            with zipfile.ZipFile(bundle, 'w') as archive:
                archive.writestr('base/manifest/AndroidManifest.xml', b'manifest')
                archive.writestr('base/dex/classes.dex', b'dex')
            keytool = ('Signer #1:\n\nCertificate #1:\nSHA256: ' + ':'.join(['AE'] * 32) + '\n').encode()
            jarsigner = subprocess.CompletedProcess([], 0, 'jar verified.\n', '')
            with patch.object(verifier.subprocess, 'run', return_value=jarsigner), \
                    patch.object(verifier, 'capture', return_value=keytool) as capture:
                with self.assertRaisesRegex(VerificationError, 'phone AAB signer does not match'):
                    verifier.verify_bundle(bundle, 'phone', '1.0.11', '100013', 'cd' * 32)
            # Only keytool ran; bundletool is never reached for a bundle signed by another key.
            self.assertEqual(capture.call_count, 1)


class ReleaseArchiveTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.bundles = {}
        for form_factor, output, *_ in verifier.RELEASE_BUNDLES:
            bundle = self.root / output
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(f'{form_factor} fixture'.encode())
            self.bundles[form_factor] = bundle
        # Recorded by the Jenkinsfile from the Vault upload key used for the build.
        self.upload_certificate = 'ae' * 32
        self.certificate = self.evidence / 'upload-certificate-sha256.txt'
        self.certificate.write_text(self.upload_certificate + '\n', encoding='ascii')
        self.source_commit = 'ab' * 20
        tool = self.root / 'bundletool.jar'
        tool.write_bytes(b'bundletool fixture')
        self.enterContext(patch.multiple(verifier, BUNDLETOOL=tool, BUNDLETOOL_SHA=verifier.sha256(tool)))
        self.enterContext(patch.dict('os.environ', {
            'PRODUCT_SHA': self.source_commit, 'WEAR_VERSION_NAME': '1.0.11', 'WEAR_VERSION_CODE': '200012',
        }))

    def verified(self, form_factor):
        self.assertEqual(form_factor, 'wear')
        fields = verify_manifest(
            manifest('200012', '30', '35', WATCH, STANDALONE, version='1.0.11'), '1.0.11', '200012', 'wear',
        )
        return fields | {
            'sha256': verifier.sha256(self.bundles[form_factor]),
            'upload_certificate_sha256': self.upload_certificate,
        }

    def test_only_the_wear_bundle_is_released(self):
        self.assertEqual([entry[0] for entry in verifier.RELEASE_BUNDLES], ['wear'])
        self.assertEqual((verifier.WEAR_VERSION_NAME, verifier.WEAR_VERSION_CODE), ('1.0.11', '200012'))

    def test_the_wear_bundle_is_verified_and_archived(self):
        results = {form_factor: self.verified(form_factor) for form_factor in ('wear',)}
        with patch.object(verifier, 'verify_bundle', side_effect=lambda bundle, form_factor, *_: results[form_factor]) as verify:
            verifier.archive_release_bundles(self.root, self.evidence)
        self.assertEqual([call.args for call in verify.call_args_list], [
            (self.bundles['wear'], 'wear', '1.0.11', '200012', self.upload_certificate),
        ])
        archive = self.evidence / 'bundles'
        names = {'wear': 'wear-1.0.11-200012.aab'}
        self.assertEqual({path.name for path in archive.iterdir()}, {*names.values(), 'SHA256SUMS'})
        for form_factor, name in names.items():
            self.assertEqual((archive / name).read_bytes(), self.bundles[form_factor].read_bytes())
        self.assertEqual((archive / 'SHA256SUMS').read_text(encoding='ascii').splitlines(), [
            f"{results['wear']['sha256']}  {names['wear']}",
        ])
        report = json.loads((self.evidence / 'signed-bundles.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source_commit'], self.source_commit)
        self.assertEqual(report['bundles'], [results[form_factor] | {'file': names[form_factor]} for form_factor in names])

    def test_rejects_a_configured_version_that_differs_from_the_pinned_release(self):
        with patch.dict('os.environ', {'WEAR_VERSION_CODE': '200008'}), patch.object(verifier, 'verify_bundle') as verify:
            with self.assertRaisesRegex(VerificationError, 'pinned release'):
                verifier.archive_release_bundles(self.root, self.evidence)
        verify.assert_not_called()

    def test_no_upload_certificate_is_pinned_in_this_repository(self):
        self.assertFalse(hasattr(verifier, 'UPLOAD_CERTIFICATE_SHA256'))

    def test_rejects_an_unreadable_vault_certificate_before_checking_bundles(self):
        for content in ('', 'AE' * 32 + '\n', 'ae' * 31 + '\n', f'{self.upload_certificate}\n' * 2):
            self.certificate.write_text(content, encoding='ascii')
            with self.subTest(content=content), patch.object(verifier, 'verify_bundle') as verify, \
                    self.assertRaisesRegex(VerificationError, 'Vault upload key'):
                verifier.archive_release_bundles(self.root, self.evidence)
            verify.assert_not_called()

    def test_nothing_is_archived_when_the_wear_bundle_fails(self):
        def verify(bundle, form_factor, *_):
            raise VerificationError('Wear AAB must require watch hardware')
        with patch.object(verifier, 'verify_bundle', side_effect=verify), self.assertRaises(VerificationError):
            verifier.archive_release_bundles(self.root, self.evidence)
        self.assertFalse((self.evidence / 'bundles').exists())
        self.assertFalse((self.evidence / 'signed-bundles.json').exists())
