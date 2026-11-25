"""Package, stage, activate and roll back a hongyu-portal release.

Locally (`package`) it turns a prepared release tree into a tarball with a manifest and a release ID derived from
the commit and the manifest. On the origin it runs as root over SSH (`status`, `stage`, `activate`, `rollback`):
staging unpacks the release beside the others without touching the live site, activation renames one symlink, and
a rollback renames it back while the failed release still owns it.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

ROOT = Path('/var/www/html/hongyu')
LOCK = Path('/var/lock/hongyu-deploy.lock')
OWNER, GROUP = 'ubuntu', 'www-data'
MANIFEST = 'manifest.sha256'
RELEASE_ID = re.compile(r'[0-9a-f]{12}-[0-9a-f]{12}')
WRITABLE = ('storage', 'bootstrap/cache')
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
ARTISAN_CACHES = ('config:cache', 'route:cache', 'view:cache')


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def file_digest(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def tree_manifest(tree):
    """Return {relative path: sha256} for every regular file; symlinks are recorded by their target."""
    entries = {}
    for path in sorted(tree.rglob('*')):
        relative = path.relative_to(tree).as_posix()
        if relative == MANIFEST:
            continue
        if path.is_symlink():
            entries[relative] = 'link:' + os.readlink(path)
        elif path.is_file():
            entries[relative] = file_digest(path)
    return entries


def manifest_text(entries):
    return ''.join(f'{digest}  {path}\n' for path, digest in sorted(entries.items()))


def release_id(commit, entries):
    require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Expected a full commit SHA')
    return f'{commit[:12]}-{hashlib.sha256(manifest_text(entries).encode()).hexdigest()[:12]}'


def package(tree, commit, output):
    tree, output = Path(tree).resolve(), Path(output).resolve()
    require((tree / 'artisan').is_file() and (tree / 'vendor/autoload.php').is_file(), 'Not a prepared release tree')
    require(not (tree / '.env').exists(), 'A release tree must not carry an environment file')
    entries = tree_manifest(tree)
    identifier = release_id(commit, entries)
    (tree / MANIFEST).write_text(manifest_text(entries), encoding='utf-8')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'hongyu-release.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(tree, arcname='.', recursive=True)
    candidate = {
        'releaseId': identifier, 'commit': commit, 'files': len(entries),
        'composerLockSha256': file_digest(tree / 'composer.lock'), 'archiveSha256': file_digest(archive),
        'archiveBytes': archive.stat().st_size,
    }
    (output / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n', encoding='utf-8')
    return candidate


def run(command, **options):
    result = subprocess.run(command, capture_output=True, text=True, **options)
    require(result.returncode == 0, f'{command[0]} failed: {(result.stderr or result.stdout).strip()[-300:]}')
    return result.stdout


class Origin:
    def __init__(self, root=ROOT, lock=LOCK, runner=run, privileged=True):
        self.root, self.lock, self.run, self.privileged = Path(root), Path(lock), runner, privileged
        self.releases = self.root / 'releases'
        self.current = self.root / 'current'

    def locked(self):
        handle = open(self.lock, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ReleaseError('Another hongyu deployment holds the lock') from None
        return handle

    def live_release(self):
        require(self.current.is_symlink(), 'current is not a symbolic link')
        target = Path(os.readlink(self.current))
        target = target if target.is_absolute() else self.root / target
        require(target.parent == self.releases and target.is_dir(), 'current does not point at a release')
        return target

    def release(self, identifier):
        require(RELEASE_ID.fullmatch(identifier or '') is not None, 'Invalid release ID')
        return self.releases / identifier

    def environment_source(self):
        link = self.live_release() / '.env'
        source = Path(os.path.realpath(link))
        require(source.is_file() and source.parent.parent == self.releases, 'The live release has no environment file')
        return source

    def verify(self, directory, identifier, commit):
        listed = {}
        for line in (directory / MANIFEST).read_text(encoding='utf-8').splitlines():
            digest, _, path = line.partition('  ')
            listed[path] = digest
        actual = tree_manifest(directory)
        for generated in ('.env', 'release.json'):
            actual.pop(generated, None)
        actual = {path: digest for path, digest in actual.items()
                  if not any(path == area or path.startswith(area + '/') for area in WRITABLE) or path in listed}
        require(actual == listed, 'Release files differ from the manifest')
        require(release_id(commit, listed) == identifier, 'Release ID differs from the manifest')
        return listed

    def status(self):
        live = self.live_release()
        return {'current': live.name, 'releases': sorted(p.name for p in self.releases.iterdir() if p.is_dir()),
                'environmentSource': str(self.environment_source())}

    def stage(self, identifier, commit, archive, archive_sha256):
        destination = self.release(identifier)
        archive = Path(archive)
        with self.locked():
            previous = self.live_release()
            environment = self.environment_source()
            if (destination / 'release.json').is_file():
                self.verify(destination, identifier, commit)
                return {'state': 'already-staged', 'releaseId': identifier, 'current': previous.name}
            require(destination != previous, 'Refusing to rebuild the live release')
            require(archive.is_file() and archive.stat().st_size <= MAX_ARCHIVE_BYTES, 'Release archive is missing or too large')
            require(file_digest(archive) == archive_sha256, 'Release archive SHA-256 mismatch')
            staging = self.releases / f'.staging-{identifier}'
            # A directory without release.json is an interrupted attempt; neither it nor staging was ever live.
            for leftover in (staging, destination):
                if leftover.exists():
                    shutil.rmtree(leftover)
            staging.mkdir(mode=0o750)
            try:
                with tarfile.open(archive, 'r:gz') as tar:
                    for member in tar.getmembers():
                        name = PurePosixPath(member.name)
                        require(not name.is_absolute() and '..' not in name.parts, f'Unsafe path in archive: {member.name}')
                        require(member.isfile() or member.isdir() or member.issym(), f'Unsupported entry: {member.name}')
                        if member.issym():
                            resolved = os.path.normpath((name.parent / member.linkname).as_posix())
                            require(not os.path.isabs(member.linkname) and not resolved.startswith('..'),
                                    f'Unsafe link in archive: {member.name}')
                    tar.extractall(staging, filter='tar')
                require(not (staging / '.env').exists(), 'The archive must not carry an environment file')
                self.verify(staging, identifier, commit)
                os.symlink(environment, staging / '.env')
                for area in WRITABLE:
                    (staging / area).mkdir(parents=True, exist_ok=True)
                # Laravel's caches hold absolute paths, so they are built only at the release's final path.
                os.rename(staging, destination)
                if self.privileged:
                    self.run(['chown', '-R', '-h', f'{OWNER}:{GROUP}', str(destination)])
                    self.run(['chmod', '-R', 'u=rwX,g=rX,o=', str(destination)])
                    self.run(['chmod', '-R', 'ug=rwX,o=', *(str(destination / area) for area in WRITABLE)])
                for command in ARTISAN_CACHES:
                    self.artisan(destination, command)
                self.run(['nginx', '-t'])
                self.verify(destination, identifier, commit)
                require(self.live_release() == previous, 'The live release changed during staging')
                record = {'releaseId': identifier, 'commit': commit, 'previousRelease': previous.name,
                          'environmentSource': str(environment), 'stagedAtUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                (destination / 'release.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                if self.privileged:
                    self.run(['chown', f'{OWNER}:{GROUP}', str(destination / 'release.json')])
            except BaseException:
                for leftover in (staging, destination):
                    shutil.rmtree(leftover, ignore_errors=True)
                raise
            return {'state': 'staged', 'releaseId': identifier, 'current': previous.name}

    def artisan(self, directory, command):
        prefix = ['runuser', '-u', OWNER, '-g', GROUP, '--'] if self.privileged else []
        self.run([*prefix, 'sh', '-c', 'umask 007 && exec php artisan "$1" --no-interaction', 'artisan', command],
                 cwd=directory)

    def switch(self, target):
        temporary = self.root / f'.current-{os.getpid()}'
        if temporary.is_symlink():
            temporary.unlink()
        os.symlink(target, temporary)
        os.replace(temporary, self.current)

    def activate(self, identifier, commit):
        destination = self.release(identifier)
        with self.locked():
            require(destination.is_dir(), 'The release is not staged')
            self.verify(destination, identifier, commit)
            previous = self.live_release()
            if previous == destination:
                return {'state': 'unchanged', 'releaseId': identifier, 'previousRelease': identifier}
            self.switch(destination)
            return {'state': 'activated', 'releaseId': identifier, 'previousRelease': previous.name}

    def rollback(self, identifier, previous):
        failed, restored = self.release(identifier), self.releases / previous
        require(re.fullmatch(r'[A-Za-z0-9._-]{1,80}', previous or '') is not None and restored.is_dir(),
                'The previous release is not available')
        with self.locked():
            live = self.live_release()
            if live == restored:
                return {'state': 'not-needed', 'current': restored.name}
            require(live == failed, 'current no longer belongs to this deployment; not changing it')
            self.switch(restored)
            return {'state': 'rolled-back', 'current': restored.name, 'failedRelease': identifier}


def main(argv):
    try:
        if len(argv) == 5 and argv[1] == 'package':
            result = package(argv[2], argv[3], argv[4])
        else:
            origin = Origin()
            if argv[1:] == ['status']:
                result = origin.status()
            elif len(argv) == 6 and argv[1] == 'stage':
                result = origin.stage(argv[2], argv[3], argv[4], argv[5])
            elif len(argv) == 4 and argv[1] == 'activate':
                result = origin.activate(argv[2], argv[3])
            elif len(argv) == 4 and argv[1] == 'rollback':
                result = origin.rollback(argv[2], argv[3])
            else:
                raise ReleaseError('Usage: release_ops.py package TREE COMMIT OUTPUT | status | stage ID COMMIT ARCHIVE '
                                   'SHA256 | activate ID COMMIT | rollback ID PREVIOUS')
    except (ReleaseError, OSError, tarfile.TarError, UnicodeDecodeError) as error:
        print(f'hongyu release refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
