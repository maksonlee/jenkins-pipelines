"""Offline tests of the hongyu release helper: a temporary origin tree, no SSH, no sudo, no PHP."""

import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

import release_ops as ops

COMMIT = 'a' * 40


class ReleaseOpsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'hongyu'
        (self.root / 'releases/old-env').mkdir(parents=True)
        (self.root / 'releases/old-env/.env').write_text('APP_KEY=fixture\n')
        live = self.root / 'releases/live-1'
        live.mkdir()
        os.symlink(self.root / 'releases/old-env/.env', live / '.env')
        os.symlink(live, self.root / 'current')
        self.commands = []
        self.origin = ops.Origin(self.root, self.base / 'lock', runner=self.runner, privileged=False)

    def runner(self, command, **options):
        self.commands.append((command, options.get('cwd')))
        return ''

    def tree(self, name='tree', marker='one'):
        tree = self.base / name
        (tree / 'vendor/bin').mkdir(parents=True)
        (tree / 'public').mkdir()
        (tree / 'storage/framework').mkdir(parents=True)
        (tree / 'artisan').write_text('#!/usr/bin/env php\n')
        (tree / 'composer.lock').write_text('{}\n')
        (tree / 'vendor/autoload.php').write_text('<?php\n')
        (tree / 'vendor/tool.php').write_text('<?php // tool\n')
        os.symlink('../tool.php', tree / 'vendor/bin/tool')
        (tree / 'public/index.php').write_text(f'<?php // {marker}\n')
        (tree / 'storage/framework/.gitignore').write_text('*\n')
        return tree

    def packaged(self, **options):
        output = self.base / ('out-' + options.get('name', 'tree'))
        candidate = ops.package(self.tree(**options), COMMIT, output)
        return candidate, output / 'hongyu-release.tar.gz'

    def stage(self, candidate, archive):
        return self.origin.stage(candidate['releaseId'], COMMIT, archive, candidate['archiveSha256'])

    def live(self):
        return Path(os.readlink(self.root / 'current')).name

    def test_package_is_deterministic_and_names_the_commit_and_content(self):
        first, _ = self.packaged()
        second, _ = self.packaged(name='again')
        other, _ = self.packaged(name='other', marker='two')
        self.assertEqual(first['releaseId'], second['releaseId'])
        self.assertNotEqual(first['releaseId'], other['releaseId'])
        self.assertTrue(first['releaseId'].startswith(COMMIT[:12] + '-'))
        self.assertRegex(first['releaseId'], ops.RELEASE_ID)
        with self.assertRaisesRegex(ops.ReleaseError, 'environment file'):
            tree = self.tree(name='with-env')
            (tree / '.env').write_text('SECRET=1\n')
            ops.package(tree, COMMIT, self.base / 'out-env')

    def test_stage_builds_the_release_beside_the_live_one_without_switching(self):
        candidate, archive = self.packaged()
        receipt = self.stage(candidate, archive)
        release = self.root / 'releases' / candidate['releaseId']
        self.assertEqual(receipt, {'state': 'staged', 'releaseId': candidate['releaseId'], 'current': 'live-1', 'pruned': []})
        self.assertEqual(self.live(), 'live-1')
        self.assertEqual(os.path.realpath(release / '.env'), str(self.root / 'releases/old-env/.env'))
        self.assertEqual(json.loads((release / 'release.json').read_text())['previousRelease'], 'live-1')
        # The caches are built at the final path, then nginx is checked; nothing is left in staging.
        artisan = [command[-1] for command, cwd in self.commands if 'artisan' in command and cwd == release]
        self.assertEqual(artisan, list(ops.ARTISAN_CACHES))
        self.assertEqual(self.commands[-1][0], ['nginx', '-t'])
        self.assertEqual([p.name for p in (self.root / 'releases').iterdir() if p.name.startswith('.staging')], [])
        self.assertEqual(self.stage(candidate, archive)['state'], 'already-staged')

    def test_stage_refuses_a_wrong_archive_or_identifier_and_leaves_nothing_behind(self):
        candidate, archive = self.packaged()
        other, other_archive = self.packaged(name='other', marker='two')
        cases = [
            (candidate['releaseId'], archive, '0' * 64, 'SHA-256 mismatch'),
            (candidate['releaseId'], other_archive, other['archiveSha256'], 'Release ID differs'),
            ('../' + candidate['releaseId'], archive, candidate['archiveSha256'], 'Invalid release ID'),
        ]
        for identifier, path, digest, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ops.ReleaseError, message):
                self.origin.stage(identifier, COMMIT, path, digest)
        self.assertEqual(sorted(p.name for p in (self.root / 'releases').iterdir()), ['live-1', 'old-env'])
        self.assertEqual(self.live(), 'live-1')

    def test_stage_refuses_unsafe_archives(self):
        tree = self.tree(name='evil')
        bad = self.base / 'evil.tar.gz'
        with tarfile.open(bad, 'w:gz') as tar:
            tar.add(tree, arcname='.')
            info = tarfile.TarInfo('./escape')
            info.type = tarfile.SYMTYPE
            info.linkname = '../../outside'
            tar.addfile(info)
        with self.assertRaisesRegex(ops.ReleaseError, 'Unsafe link'):
            self.origin.stage('a' * 12 + '-' + 'b' * 12, COMMIT, bad, ops.file_digest(bad))

    def test_a_failed_cache_build_removes_the_release_and_keeps_the_site(self):
        candidate, archive = self.packaged()

        def failing(command, **options):
            if 'artisan' in command:
                raise ops.ReleaseError('php failed')
            return ''
        self.origin.run = failing
        with self.assertRaisesRegex(ops.ReleaseError, 'php failed'):
            self.stage(candidate, archive)
        self.assertFalse((self.root / 'releases' / candidate['releaseId']).exists())
        self.assertEqual(self.live(), 'live-1')

    def test_activate_switches_one_link_and_rollback_restores_it_only_while_it_owns_current(self):
        candidate, archive = self.packaged()
        with self.assertRaisesRegex(ops.ReleaseError, 'not staged'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.stage(candidate, archive)
        activated = self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual(activated, {'state': 'activated', 'releaseId': candidate['releaseId'], 'previousRelease': 'live-1',
                                     'pruned': []})
        self.assertEqual(self.live(), candidate['releaseId'])
        self.assertEqual([command for command, _ in self.commands[-2:]],
                         [['systemctl', 'reload', ops.PHP_SERVICE], ['systemctl', 'is-active', '--quiet', ops.PHP_SERVICE]])
        self.assertEqual(self.origin.activate(candidate['releaseId'], COMMIT)['state'], 'unchanged')
        self.assertEqual(self.origin.rollback(candidate['releaseId'], 'live-1')['state'], 'rolled-back')
        self.assertEqual(self.live(), 'live-1')
        self.assertEqual(self.origin.rollback(candidate['releaseId'], 'live-1')['state'], 'not-needed')
        # Someone else's release is live: this deployment must not touch it.
        other, other_archive = self.packaged(name='other', marker='two')
        self.stage(other, other_archive)
        self.origin.activate(other['releaseId'], COMMIT)
        with self.assertRaisesRegex(ops.ReleaseError, 'no longer belongs'):
            self.origin.rollback(candidate['releaseId'], 'old-env')
        self.assertEqual(self.live(), other['releaseId'])

    def test_a_failed_php_reload_puts_the_previous_release_back(self):
        candidate, archive = self.packaged()
        self.stage(candidate, archive)

        def failing(command, **options):
            raise ops.ReleaseError('reload failed')
        self.origin.run = failing
        with self.assertRaisesRegex(ops.ReleaseError, 'reload failed'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual(self.live(), 'live-1')

    def test_activate_refuses_a_release_that_was_modified_after_staging(self):
        candidate, archive = self.packaged()
        self.stage(candidate, archive)
        (self.root / 'releases' / candidate['releaseId'] / 'public/index.php').write_text('<?php // tampered\n')
        with self.assertRaisesRegex(ops.ReleaseError, 'differ from the manifest'):
            self.origin.activate(candidate['releaseId'], COMMIT)
        self.assertEqual(self.live(), 'live-1')

    def test_a_second_deployment_cannot_run_while_one_holds_the_lock(self):
        candidate, archive = self.packaged()
        with self.origin.locked():
            with self.assertRaisesRegex(ops.ReleaseError, 'holds the lock'):
                self.stage(candidate, archive)

    def test_old_releases_are_pruned_but_never_the_live_one_or_the_environment_holder(self):
        import os as _os
        releases = self.root / 'releases'
        for index in range(8):  # older than everything else, oldest first
            old = releases / f'old-{index}'
            old.mkdir()
            _os.utime(old, (1_000_000 + index, 1_000_000 + index))
        _os.utime(releases / 'old-env', (1, 1))   # the oldest of all, but it holds .env
        _os.utime(releases / 'live-1', (2, 2))    # old, but live
        candidate, archive = self.packaged()
        receipt = self.stage(candidate, archive)
        # Kept: live-1, old-env, the release just staged, and the newest five of the rest.
        self.assertEqual(receipt['pruned'], ['old-0', 'old-1', 'old-2'])
        self.assertEqual(sorted(p.name for p in releases.iterdir()),
                         sorted(['live-1', 'old-env', candidate['releaseId']] + [f'old-{i}' for i in range(3, 8)]))
        activated = self.origin.activate(candidate['releaseId'], COMMIT)
        # live-1 is now the rollback target: protected by name, so the oldest other release goes instead.
        self.assertEqual(activated['pruned'], [])
        self.assertTrue((releases / 'live-1').is_dir())
        self.assertEqual(self.origin.rollback(candidate['releaseId'], 'live-1')['state'], 'rolled-back')

    def test_status_reports_the_live_release_and_its_environment_source(self):
        status = self.origin.status()
        self.assertEqual(status['current'], 'live-1')
        self.assertEqual(status['environmentSource'], str(self.root / 'releases/old-env/.env'))


if __name__ == '__main__':
    unittest.main()
