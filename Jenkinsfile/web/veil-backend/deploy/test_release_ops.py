"""Offline tests of the veil-server release helper: temporary directories, a fake systemctl and a fake health check."""

import json
import tempfile
import unittest
from pathlib import Path

import release_ops as ops

COMMIT = 'c' * 40
ELF = b'\x7fELF' + b'\x02\x01\x01' + b'\x00' * 11 + b'\xb7\x00'


class ReleaseOpsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.home = self.base / 'veil-server'
        self.home.mkdir()
        (self.home / 'veil-server').write_bytes(ELF + b'live')
        self.commands, self.health = [], [True]
        self.origin = ops.Origin(self.home, self.base / 'lock', runner=self.runner,
                                 health=lambda: self.health[0], pause=lambda seconds: None)

    def runner(self, command, **options):
        self.commands.append(command)
        return ''

    def candidate(self, payload=b'new', glibc='GLIBC_2.17 GLIBC_2.38'):
        binary = self.base / f'binary-{payload.decode()}'
        binary.write_bytes(ELF + payload)
        migrations = self.base / 'migrations'
        migrations.mkdir(exist_ok=True)
        for name in ('0001_init.sql', '0002_reports.sql'):
            (migrations / name).write_text('-- sql\n')
        symbols = self.base / 'symbols.txt'
        symbols.write_text(glibc)
        return ops.package(binary, COMMIT, migrations, symbols, self.base / 'out'), binary

    def stage(self, candidate, binary):
        return self.origin.stage(candidate['releaseId'], COMMIT, binary, candidate['binarySha256'],
                                 ','.join(candidate['migrations']))

    def test_package_records_the_binary_and_refuses_what_the_origin_cannot_run(self):
        candidate, _ = self.candidate()
        self.assertEqual(candidate['releaseId'], f"{COMMIT[:12]}-{candidate['binarySha256'][:12]}")
        self.assertEqual((candidate['glibc'], candidate['migrations']), ('2.38', ['0001_init.sql', '0002_reports.sql']))
        with self.assertRaisesRegex(ops.ReleaseError, 'needs glibc 2.40'):
            self.candidate(glibc='GLIBC_2.40')
        x86 = self.base / 'x86'
        x86.write_bytes(ELF[:18] + b'\x3e\x00')
        with self.assertRaisesRegex(ops.ReleaseError, 'not an aarch64 ELF'):
            ops.package(x86, COMMIT, self.base / 'migrations', self.base / 'symbols.txt', self.base / 'out')

    def test_stage_stores_a_file_and_never_touches_the_service(self):
        candidate, binary = self.candidate()
        receipt = self.stage(candidate, binary)
        staged = self.home / 'releases' / candidate['releaseId'] / 'veil-server'
        self.assertEqual(receipt['state'], 'staged')
        self.assertEqual(staged.stat().st_mode & 0o111, 0)
        self.assertEqual(self.commands, [])
        self.assertEqual((self.home / 'veil-server').read_bytes(), ELF + b'live')
        self.assertEqual(self.stage(candidate, binary)['state'], 'already-staged')
        with self.assertRaisesRegex(ops.ReleaseError, 'does not name this commit and binary'):
            self.origin.stage(candidate['releaseId'], 'd' * 40, binary, candidate['binarySha256'], '0001_init.sql')
        with self.assertRaisesRegex(ops.ReleaseError, 'Unexpected migration names'):
            self.origin.stage(candidate['releaseId'], COMMIT, binary, candidate['binarySha256'], '../x.sql')

    def test_activate_backs_up_the_database_installs_restarts_and_records_the_release(self):
        candidate, binary = self.candidate()
        with self.assertRaisesRegex(ops.ReleaseError, 'not staged'):
            self.origin.activate(candidate['releaseId'])
        self.stage(candidate, binary)
        receipt = self.origin.activate(candidate['releaseId'])
        self.assertEqual(receipt['state'], 'activated')
        self.assertEqual(self.commands, [['systemctl', 'start', ops.BACKUP_UNIT], ['systemctl', 'restart', ops.SERVICE]])
        live = self.home / 'veil-server'
        self.assertEqual(live.read_bytes(), ELF + b'new')
        self.assertEqual(live.stat().st_mode & 0o777, 0o755)
        status = self.origin.status()
        self.assertEqual((status['liveRelease'], status['liveMigrations']), (candidate['releaseId'], candidate['migrations']))
        self.assertEqual(self.origin.activate(candidate['releaseId'])['state'], 'unchanged')
        self.assertEqual(self.origin.rollback(candidate['releaseId'])['state'], 'rolled-back')
        self.assertEqual(live.read_bytes(), ELF + b'live')
        self.assertIsNone(self.origin.status()['liveRelease'])
        self.assertEqual(self.origin.rollback(candidate['releaseId'])['state'], 'not-needed')

    def test_an_unhealthy_new_binary_is_replaced_by_the_previous_one(self):
        candidate, binary = self.candidate()
        self.stage(candidate, binary)
        restarts = []

        def runner(command, **options):
            if command[1] == 'restart':
                restarts.append(command)
                self.health[0] = len(restarts) > 1  # healthy again only after the previous binary is back
            return ''
        self.origin.run = runner
        with self.assertRaisesRegex(ops.ReleaseError, 'previous binary was put back and is healthy'):
            self.origin.activate(candidate['releaseId'])
        self.assertEqual((self.home / 'veil-server').read_bytes(), ELF + b'live')
        self.assertFalse((self.home / 'current.json').exists())

    def test_activate_refuses_an_unhealthy_server_a_tampered_binary_and_a_held_lock(self):
        candidate, binary = self.candidate()
        self.stage(candidate, binary)
        self.health[0] = False
        with self.assertRaisesRegex(ops.ReleaseError, 'not healthy'):
            self.origin.activate(candidate['releaseId'])
        self.health[0] = True
        with self.origin.locked():
            with self.assertRaisesRegex(ops.ReleaseError, 'holds the lock'):
                self.origin.activate(candidate['releaseId'])
        (self.home / 'releases' / candidate['releaseId'] / 'veil-server').write_bytes(ELF + b'tampered')
        with self.assertRaisesRegex(ops.ReleaseError, 'differs from its release ID'):
            self.origin.activate(candidate['releaseId'])
        self.assertEqual(self.commands, [])

    def test_old_staged_binaries_are_pruned_but_never_the_live_one(self):
        import os as _os
        staged = []
        for index in range(7):
            candidate, binary = self.candidate(payload=f'b{index}'.encode())
            self.stage(candidate, binary)
            if index == 0:
                self.origin.activate(candidate['releaseId'])   # the oldest one is live
            _os.utime(self.home / 'releases' / candidate['releaseId'], (1_000_000 + index, 1_000_000 + index))
            staged.append(candidate)
        newest, binary = self.candidate(payload=b'newest')
        receipt = self.stage(newest, binary)
        self.assertEqual(receipt['pruned'], [staged[1]['releaseId']])
        self.assertEqual(self.origin.status()['staged'],
                         sorted([newest['releaseId'], staged[0]['releaseId']] + [c['releaseId'] for c in staged[2:]]))
        # The live release still holds the binary it replaced.
        self.assertEqual(self.origin.rollback(staged[0]['releaseId'])['state'], 'rolled-back')

    def test_rollback_refuses_when_another_binary_is_live(self):
        candidate, binary = self.candidate()
        self.stage(candidate, binary)
        self.origin.activate(candidate['releaseId'])
        (self.home / 'veil-server').write_bytes(ELF + b'later')
        with self.assertRaisesRegex(ops.ReleaseError, 'no longer belongs'):
            self.origin.rollback(candidate['releaseId'])

    def test_a_hand_installed_binary_is_not_reported_as_a_known_release(self):
        candidate, binary = self.candidate()
        self.stage(candidate, binary)
        self.origin.activate(candidate['releaseId'])
        (self.home / 'veil-server').write_bytes(ELF + b'by hand')
        self.assertIsNone(self.origin.status()['liveRelease'])


if __name__ == '__main__':
    unittest.main()
