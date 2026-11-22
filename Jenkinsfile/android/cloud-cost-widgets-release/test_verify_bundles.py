import unittest

from verify_bundles import VerificationError, require_signature, signer_fingerprint, verify_manifest


def manifest(version_code, minimum, target, feature='', metadata=''):
    return f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
        package="com.maksonlee.cloudcost.aws" android:versionName="1.0.5" android:versionCode="{version_code}">
        <uses-sdk android:minSdkVersion="{minimum}" android:targetSdkVersion="{target}"/>
        {feature}<application>{metadata}</application>
    </manifest>'''


WATCH = '<uses-feature android:name="android.hardware.type.watch" android:required="true"/>'
STANDALONE = '<meta-data android:name="com.google.android.wearable.standalone" android:value="true"/>'


class ManifestTest(unittest.TestCase):
    def test_phone_and_standalone_watch(self):
        phone = verify_manifest(manifest('100006', '26', '36'), '1.0.5', '100006', 'phone')
        wear = verify_manifest(manifest('200006', '30', '35', WATCH, STANDALONE), '1.0.5', '200006', 'wear')
        self.assertEqual((phone['form_factor'], wear['form_factor']), ('phone', 'wear'))

    def test_rejects_wrong_version_wrong_target_or_swapped_form_factor(self):
        for xml in (
            manifest('200006', '26', '36'),
            manifest('100006', '26', '35'),
            manifest('100006', '26', '36', WATCH),
        ):
            with self.subTest(manifest=xml), self.assertRaises(VerificationError):
                verify_manifest(xml, '1.0.5', '100006', 'phone')

    def test_rejects_optional_watch_hardware_or_missing_standalone_declaration(self):
        for xml in (
            manifest('200006', '30', '35', WATCH.replace('true', 'false'), STANDALONE),
            manifest('200006', '30', '35', WATCH),
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
