"""Offline tests of the static-site release helper: temporary directories and the local rsync, no SSH or sudo."""

import json
import tarfile
import tempfile
import unittest
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'static_site_release', Path(__file__).resolve().parents[2] / 'resources/web/static_site_release.py')
ops = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ops)

COMMIT = 'a' * 40


class ReleaseOpsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.live = self.base / 'share-notes'
        self.site(self.live, '1.0.0', 'old')
        (self.live / 'removed-in-new.js').write_text('old only\n')
        self.origin = ops.Origin('share-notes', self.live, self.base / 'releases', self.base / 'lock', privileged=False)

    def site(self, tree, version, marker):
        (tree / 'assets').mkdir(parents=True)
        for name in ops.SITES['share-notes']['required']:
            (tree / name).write_text(f'{name} {marker}\n')
        (tree / 'version.json').write_text(json.dumps({'version': version, 'build_number': '7'}))
        (tree / 'assets/font.otf').write_text(f'font {marker}\n')
        return tree

    def packaged(self, name='new', version='1.1.0'):
        output = self.base / f'out-{name}'
        candidate = ops.package('share-notes', self.site(self.base / f'tree-{name}', version, name), COMMIT, output)
        return candidate, output / 'site-release.tar.gz'

    def stage(self, candidate, archive):
        return self.origin.stage(candidate['releaseId'], COMMIT, archive, candidate['archiveSha256'])

    def test_package_names_the_commit_content_and_version(self):
        first, _ = self.packaged()
        again, _ = self.packaged(name='again')
        self.assertRegex(first['releaseId'], ops.RELEASE_ID)
        self.assertTrue(first['releaseId'].startswith(COMMIT[:12]))
        self.assertNotEqual(first['releaseId'], again['releaseId'])
        self.assertEqual(first['version'], '1.1.0+7')
        tree = self.site(self.base / 'tree-short', '1.1.0', 'x')
        (tree / 'main.dart.js').unlink()
        with self.assertRaisesRegex(ops.ReleaseError, 'no main.dart.js'):
            ops.package('share-notes', tree, COMMIT, self.base / 'out-short')

    def test_a_site_without_a_version_file_is_named_by_its_content(self):
        tree = self.base / 'reader'
        tree.mkdir()
        for name in ops.SITES['readerplay']['required']:
            (tree / name).write_text(name)
        candidate = ops.package('readerplay', tree, COMMIT, self.base / 'out-reader')
        self.assertEqual(candidate['version'], 'content-' + candidate['releaseId'].split('-')[1])
        with self.assertRaisesRegex(ops.ReleaseError, 'Unknown site'):
            ops.package('elsewhere', tree, COMMIT, self.base / 'out-reader')

    def test_stage_leaves_the_live_site_alone(self):
        before = ops.tree_manifest(self.live)
        candidate, archive = self.packaged()
        receipt = self.stage(candidate, archive)
        self.assertEqual((receipt['state'], receipt['liveVersion']), ('staged', '1.0.0+7'))
        self.assertEqual(ops.tree_manifest(self.live), before)
        self.assertEqual(self.origin.status()['staged'], [candidate['releaseId']])
        self.assertEqual(self.stage(candidate, archive)['state'], 'already-staged')

    def test_stage_refuses_wrong_or_unsafe_archives(self):
        candidate, archive = self.packaged()
        with self.assertRaisesRegex(ops.ReleaseError, 'SHA-256 mismatch'):
            self.origin.stage(candidate['releaseId'], COMMIT, archive, '0' * 64)
        with self.assertRaisesRegex(ops.ReleaseError, 'differ from the release ID'):
            self.origin.stage('a' * 12 + '-' + 'b' * 12, COMMIT, archive, candidate['archiveSha256'])
        with self.assertRaisesRegex(ops.ReleaseError, 'Invalid release ID'):
            self.origin.stage('../x', COMMIT, archive, candidate['archiveSha256'])
        bad = self.base / 'bad.tar.gz'
        with tarfile.open(bad, 'w:gz') as tar:
            tar.add(self.base / 'tree-new', arcname='.')
            link = tarfile.TarInfo('./escape')
            link.type, link.linkname = tarfile.SYMTYPE, '/etc'
            tar.addfile(link)
        with self.assertRaisesRegex(ops.ReleaseError, 'Unsupported entry'):
            self.origin.stage(candidate['releaseId'], COMMIT, bad, ops.file_digest(bad))
        self.assertEqual(self.origin.status()['staged'], [])

    def test_activate_replaces_the_site_and_rollback_restores_every_file(self):
        before = ops.tree_manifest(self.live)
        candidate, archive = self.packaged()
        with self.assertRaisesRegex(ops.ReleaseError, 'not staged'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.stage(candidate, archive)
        receipt = self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual((receipt['state'], receipt['previousVersion']), ('activated', '1.0.0+7'))
        self.assertEqual(ops.release_id(COMMIT, ops.tree_manifest(self.live)), candidate['releaseId'])
        self.assertFalse((self.live / 'removed-in-new.js').exists())
        self.assertEqual(self.origin.activate(candidate['releaseId'], COMMIT)['state'], 'unchanged')
        self.assertEqual(self.origin.rollback(candidate['releaseId'], COMMIT)['state'], 'rolled-back')
        self.assertEqual(ops.tree_manifest(self.live), before)
        self.assertEqual(self.origin.rollback(candidate['releaseId'], COMMIT)['state'], 'not-needed')

    def test_old_staged_releases_are_pruned_but_never_the_live_one(self):
        import os as _os
        staged = []
        for index in range(7):
            candidate, archive = self.packaged(name=f'r{index}', version=f'1.{index}.0')
            self.stage(candidate, archive)
            if index == 0:
                self.origin.activate(candidate['releaseId'], COMMIT)   # the oldest one is live
            _os.utime(self.base / 'releases' / candidate['releaseId'], (1_000_000 + index, 1_000_000 + index))
            staged.append(candidate)
        newest, archive = self.packaged(name='newest', version='2.0.0')
        receipt = self.stage(newest, archive)
        # Kept: the live release, the one just staged and the newest five others; r1 is the one that goes.
        self.assertEqual(receipt['pruned'], [staged[1]['releaseId']])
        self.assertEqual(self.origin.status()['staged'],
                         sorted([newest['releaseId'], staged[0]['releaseId']] + [c['releaseId'] for c in staged[2:]]))
        self.assertEqual(self.origin.rollback(staged[0]['releaseId'], COMMIT)['state'], 'rolled-back')

    def test_rollback_refuses_when_something_else_is_live(self):
        candidate, archive = self.packaged()
        self.stage(candidate, archive)
        self.origin.activate(candidate['releaseId'], COMMIT)
        (self.live / 'index.html').write_text('a later deployment\n')
        with self.assertRaisesRegex(ops.ReleaseError, 'no longer belongs'):
            self.origin.rollback(candidate['releaseId'], COMMIT)
        self.assertEqual((self.live / 'index.html').read_text(), 'a later deployment\n')

    def test_a_failed_sync_restores_the_previous_site(self):
        before = ops.tree_manifest(self.live)
        candidate, archive = self.packaged()
        self.stage(candidate, archive)
        calls = []

        def flaky(command, **options):
            calls.append(command)
            if len(calls) == 2:  # the sync of the release into the live directory
                (self.live / 'index.html').write_text('half written\n')
                raise ops.ReleaseError('rsync failed')
            return ops.run(command, **options)
        self.origin.run = flaky
        with self.assertRaisesRegex(ops.ReleaseError, 'rsync failed'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual(ops.tree_manifest(self.live), before)

    def test_activate_refuses_a_release_modified_after_staging_and_a_held_lock(self):
        candidate, archive = self.packaged()
        self.stage(candidate, archive)
        with self.origin.locked():
            with self.assertRaisesRegex(ops.ReleaseError, 'holds the lock'):
                self.origin.activate(candidate['releaseId'], COMMIT)
        (self.base / 'releases' / candidate['releaseId'] / 'site/index.html').write_text('tampered\n')
        with self.assertRaisesRegex(ops.ReleaseError, 'differ from the release ID'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual(ops.version_of(self.live), '1.0.0+7')


if __name__ == '__main__':
    unittest.main()
