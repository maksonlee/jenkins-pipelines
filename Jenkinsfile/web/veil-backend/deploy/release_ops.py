"""Package, stage, activate and roll back a veil-server binary.

Locally (`package`) this records a cross-built binary's SHA-256, the newest glibc symbol version it needs and the
migrations of its commit. On the origin it runs as root over SSH (`status`, `stage`, `activate`, `rollback`). A
staged binary is only a file: it is never started, because the server applies its migrations when it starts.
Activation takes a database backup, keeps the live binary, installs the staged one, restarts the service and
checks its health on loopback; if the service does not become healthy the previous binary is put back.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path('/opt/veil-server')
LOCK = Path('/var/lock/veil-server-deploy.lock')
SERVICE = 'veil-server'
BACKUP_UNIT = 'veil-db-backup.service'
HEALTH_URL = 'http://127.0.0.1:18300/v2/health'
RELEASE_ID = re.compile(r'[0-9a-f]{12}-[0-9a-f]{12}')
MAX_GLIBC = (2, 39)
MAX_BINARY_BYTES = 100 * 1024 * 1024
HEALTH_ATTEMPTS = 30


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def file_digest(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def release_id(commit, digest):
    require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Expected a full commit SHA')
    require(re.fullmatch(r'[0-9a-f]{64}', digest or '') is not None, 'Expected a SHA-256')
    return f'{commit[:12]}-{digest[:12]}'


def newest_glibc(symbols):
    versions = {tuple(int(part) for part in found.split('.')) for found in re.findall(r'GLIBC_([0-9]+(?:\.[0-9]+)+)', symbols)}
    require(versions, 'The binary lists no glibc symbol versions')
    return max(versions)


def package(binary, commit, migrations, symbols_file, output):
    binary, output = Path(binary), Path(output)
    require(binary.is_file() and 0 < binary.stat().st_size <= MAX_BINARY_BYTES, 'The binary is missing or too large')
    with open(binary, 'rb') as file:
        header = file.read(20)
    require(header[:4] == b'\x7fELF' and header[18:20] == b'\xb7\x00', 'The binary is not an aarch64 ELF file')
    glibc = newest_glibc(Path(symbols_file).read_text(encoding='utf-8', errors='replace'))
    require(glibc <= MAX_GLIBC, f'The binary needs glibc {".".join(map(str, glibc))}; the origin has {".".join(map(str, MAX_GLIBC))}')
    names = sorted(path.name for path in Path(migrations).glob('*.sql'))
    require(names, 'The commit has no migrations')
    digest = file_digest(binary)
    candidate = {'releaseId': release_id(commit, digest), 'commit': commit, 'binarySha256': digest,
                 'binaryBytes': binary.stat().st_size, 'glibc': '.'.join(map(str, glibc)), 'migrations': names}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n', encoding='utf-8')
    return candidate


def run(command, **options):
    result = subprocess.run(command, capture_output=True, text=True, **options)
    require(result.returncode == 0, f'{command[0]} failed: {(result.stderr or result.stdout).strip()[-300:]}')
    return result.stdout


def healthy():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


class Origin:
    def __init__(self, home=HOME, lock=LOCK, runner=run, health=healthy, pause=time.sleep):
        self.home, self.lock, self.run, self.health, self.pause = Path(home), Path(lock), runner, health, pause
        self.binary = self.home / 'veil-server'
        self.releases = self.home / 'releases'
        self.record = self.home / 'current.json'

    def locked(self):
        handle = open(self.lock, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ReleaseError('Another veil-server deployment holds the lock') from None
        return handle

    def release(self, identifier):
        require(RELEASE_ID.fullmatch(identifier or '') is not None, 'Invalid release ID')
        return self.releases / identifier

    def staged(self, identifier):
        directory = self.release(identifier)
        require((directory / 'release.json').is_file(), 'The release is not staged')
        record = json.loads((directory / 'release.json').read_text(encoding='utf-8'))
        require(record.get('releaseId') == identifier and
                release_id(record.get('commit'), file_digest(directory / 'veil-server')) == identifier,
                'The staged binary differs from its release ID')
        return directory, record

    def live(self):
        require(self.binary.is_file(), 'The live binary is missing')
        digest = file_digest(self.binary)
        record = {}
        if self.record.is_file():
            record = json.loads(self.record.read_text(encoding='utf-8'))
            if record.get('binarySha256') != digest:
                record = {}  # installed by hand since the last deployment through this tool
        return {'liveSha256': digest, 'liveRelease': record.get('releaseId'), 'liveCommit': record.get('commit'),
                'liveMigrations': record.get('migrations')}

    def status(self):
        staged = sorted(p.name for p in self.releases.iterdir() if RELEASE_ID.fullmatch(p.name)) if self.releases.is_dir() else []
        return {**self.live(), 'healthy': self.health(), 'staged': staged}

    def install(self, source):
        temporary = self.home / '.veil-server.new'
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o755)
        os.replace(temporary, self.binary)

    def restart_and_wait(self):
        self.run(['systemctl', 'restart', SERVICE])
        for _ in range(HEALTH_ATTEMPTS):
            if self.health():
                return True
            self.pause(1)
        return False

    def stage(self, identifier, commit, upload, digest, migrations):
        destination, upload = self.release(identifier), Path(upload)
        require(release_id(commit, digest) == identifier, 'The release ID does not name this commit and binary')
        names = migrations.split(',')
        require(all(re.fullmatch(r'[0-9]{4}_[a-z0-9_]+\.sql', name) for name in names), 'Unexpected migration names')
        with self.locked():
            if (destination / 'release.json').is_file():
                self.staged(identifier)
                return {'state': 'already-staged', 'releaseId': identifier, **self.live()}
            require(upload.is_file() and upload.stat().st_size <= MAX_BINARY_BYTES, 'The uploaded binary is missing or too large')
            require(file_digest(upload) == digest, 'The uploaded binary SHA-256 does not match')
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True, mode=0o755)
            try:
                shutil.copyfile(upload, destination / 'veil-server')
                os.chmod(destination / 'veil-server', 0o644)  # not executable until it is installed
                record = {'releaseId': identifier, 'commit': commit, 'binarySha256': digest, 'migrations': names,
                          'stagedAtUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                (destination / 'release.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
            except BaseException:
                shutil.rmtree(destination, ignore_errors=True)
                raise
            return {'state': 'staged', 'releaseId': identifier, **self.live()}

    def activate(self, identifier):
        with self.locked():
            directory, record = self.staged(identifier)
            before = self.live()
            if before['liveSha256'] == record['binarySha256']:
                return {'state': 'unchanged', 'releaseId': identifier}
            require(self.health(), 'The live server is not healthy; not deploying over an unknown problem')
            self.run(['systemctl', 'start', BACKUP_UNIT])
            previous = directory / 'previous-veil-server'
            shutil.copyfile(self.binary, previous)
            require(file_digest(previous) == before['liveSha256'], 'The copy of the live binary is incomplete')
            previous_record = self.record.read_bytes() if self.record.is_file() else None
            self.install(directory / 'veil-server')
            self.record.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
            if self.restart_and_wait():
                return {'state': 'activated', 'releaseId': identifier, 'previousSha256': before['liveSha256'],
                        'previousRelease': before['liveRelease']}
            self.install(previous)
            if previous_record is None:
                self.record.unlink()
            else:
                self.record.write_bytes(previous_record)
            restored = self.restart_and_wait()
            raise ReleaseError('The new binary did not become healthy; the previous binary was put back and is ' +
                               ('healthy' if restored else 'NOT healthy'))

    def rollback(self, identifier):
        with self.locked():
            directory, record = self.staged(identifier)
            previous = directory / 'previous-veil-server'
            require(previous.is_file(), 'This release kept no previous binary')
            live = file_digest(self.binary)
            if live == file_digest(previous):
                return {'state': 'not-needed', **self.live()}
            require(live == record['binarySha256'], 'The live binary no longer belongs to this deployment; not changing it')
            self.install(previous)
            if self.record.is_file():
                self.record.unlink()
            restored = self.restart_and_wait()
            return {'state': 'rolled-back', 'failedRelease': identifier, 'healthy': restored, **self.live()}


def main(argv):
    try:
        if len(argv) == 7 and argv[1] == 'package':
            result = package(*argv[2:7])
        else:
            origin = Origin()
            if argv[1:] == ['status']:
                result = origin.status()
            elif len(argv) == 7 and argv[1] == 'stage':
                result = origin.stage(*argv[2:7])
            elif len(argv) == 3 and argv[1] == 'show':
                result = {**origin.staged(argv[2])[1], **origin.live(), 'healthy': origin.health()}
            elif len(argv) == 3 and argv[1] in ('activate', 'rollback'):
                result = getattr(origin, argv[1])(argv[2])
            else:
                raise ReleaseError('Usage: release_ops.py package BINARY COMMIT MIGRATIONS_DIR SYMBOLS OUTPUT | status | '
                                   'stage ID COMMIT UPLOAD SHA256 MIGRATIONS | show ID | activate ID | rollback ID')
    except (ReleaseError, OSError, ValueError) as error:
        print(f'veil-server release refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
