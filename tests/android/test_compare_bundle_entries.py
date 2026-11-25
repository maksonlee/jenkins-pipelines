"""Offline tests of the check that signing outside Gradle changes only the signature files."""

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'compare_bundle_entries', Path(__file__).resolve().parents[2] / 'resources/android/release/compare_bundle_entries.py')
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)

BASE = {'BundleConfig.pb': b'config', 'base/dex/classes.dex': b'dex', 'META-INF/MANIFEST.MF': b'Manifest-Version: 1.0\n'}
SIGNATURE = {'META-INF/UPLOAD.SF': b'sf', 'META-INF/UPLOAD.RSA': b'rsa'}


class CompareBundleEntriesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)

    def bundle(self, name, entries):
        path = self.base / name
        with zipfile.ZipFile(path, 'w') as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return path

    def test_a_signed_copy_with_only_signature_files_added_passes(self):
        unsigned = self.bundle('unsigned.aab', BASE)
        signed = self.bundle('signed.aab', {**BASE, 'META-INF/MANIFEST.MF': b'with digests', **SIGNATURE})
        self.assertEqual(tool.compare(unsigned, signed), (3, ['META-INF/UPLOAD.RSA', 'META-INF/UPLOAD.SF']))

    def test_changed_dropped_or_added_content_is_refused(self):
        unsigned = self.bundle('unsigned.aab', BASE)
        cases = {
            'changed or dropped 1 entries': {**BASE, 'base/dex/classes.dex': b'other', **SIGNATURE},
            'changed or dropped 1 entries, first: BundleConfig.pb': {k: v for k, v in {**BASE, **SIGNATURE}.items() if k != 'BundleConfig.pb'},
            'added unexpected entries': {**BASE, **SIGNATURE, 'base/lib/extra.so': b'x'},
            'no signature files': BASE,
        }
        for index, (message, entries) in enumerate(cases.items()):
            with self.subTest(message=message), self.assertRaisesRegex(SystemExit, message):
                tool.compare(unsigned, self.bundle(f'signed-{index}.aab', entries))
        with self.assertRaisesRegex(SystemExit, 'already carries signature files'):
            tool.compare(self.bundle('presigned.aab', {**BASE, **SIGNATURE}), self.bundle('s.aab', {**BASE, **SIGNATURE}))


if __name__ == '__main__':
    unittest.main()
