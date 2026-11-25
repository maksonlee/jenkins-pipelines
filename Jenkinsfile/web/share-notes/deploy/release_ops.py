"""Package, stage, activate and roll back a Share Notes web release.

The site is static files served straight from one directory. Locally (`package`) this turns `build/web` into a
tarball with a manifest and a release ID derived from the commit and the manifest. On the origin it runs as root
over SSH (`status`, `stage`, `activate`, `rollback`): staging unpacks the release beside the site, activation
copies the live files aside and then syncs the release into the live directory, and a rollback syncs the copy back
while the failed release is still what is live.
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

LIVE = Path('/var/www/html/share-notes')
STORE = Path('/var/www/html/share-notes-releases')
LOCK = Path('/var/lock/share-notes-deploy.lock')
OWNER = 'ubuntu:ubuntu'
RELEASE_ID = re.compile(r'[0-9a-f]{12}-[0-9a-f]{12}')
REQUIRED = ('index.html', 'main.dart.js', 'flutter_bootstrap.js', 'version.json', 'help.html', 'privacy.html',
            'terms.html')
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def file_digest(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def tree_manifest(tree):
    entries = {}
    for path in sorted(Path(tree).rglob('*')):
        require(not path.is_symlink(), f'Symbolic links are not part of a release: {path.name}')
        if path.is_file():
            entries[path.relative_to(tree).as_posix()] = file_digest(path)
    return entries


def manifest_text(entries):
    return ''.join(f'{digest}  {path}\n' for path, digest in sorted(entries.items()))


def content_id(entries):
    return hashlib.sha256(manifest_text(entries).encode()).hexdigest()[:12]


def release_id(commit, entries):
    require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Expected a full commit SHA')
    return f'{commit[:12]}-{content_id(entries)}'


def version_of(tree):
    try:
        data = json.loads((Path(tree) / 'version.json').read_text(encoding='utf-8'))
        return f"{data['version']}+{data['build_number']}"
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


def package(tree, commit, output):
    tree, output = Path(tree).resolve(), Path(output).resolve()
    for name in REQUIRED:
        require((tree / name).is_file(), f'The web build has no {name}')
    entries = tree_manifest(tree)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'share-notes-web.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(tree, arcname='.', recursive=True)
    (output / 'manifest.sha256').write_text(manifest_text(entries), encoding='utf-8')
    candidate = {
        'releaseId': release_id(commit, entries), 'commit': commit, 'version': version_of(tree),
        'files': len(entries), 'indexSha256': entries['index.html'], 'mainSha256': entries['main.dart.js'],
        'archiveSha256': file_digest(archive), 'archiveBytes': archive.stat().st_size,
    }
    (output / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n', encoding='utf-8')
    return candidate


def run(command, **options):
    result = subprocess.run(command, capture_output=True, text=True, **options)
    require(result.returncode == 0, f'{command[0]} failed: {(result.stderr or result.stdout).strip()[-300:]}')
    return result.stdout


class Origin:
    def __init__(self, live=LIVE, store=STORE, lock=LOCK, runner=run, privileged=True):
        self.live, self.store, self.lock = Path(live), Path(store), Path(lock)
        self.run, self.privileged = runner, privileged

    def locked(self):
        handle = open(self.lock, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ReleaseError('Another Share Notes deployment holds the lock') from None
        return handle

    def release(self, identifier):
        require(RELEASE_ID.fullmatch(identifier or '') is not None, 'Invalid release ID')
        return self.store / identifier

    def live_state(self):
        require(self.live.is_dir() and not self.live.is_symlink(), 'The live directory is missing')
        return {'liveVersion': version_of(self.live), 'liveContent': content_id(tree_manifest(self.live))}

    def verify(self, directory, identifier, commit):
        entries = tree_manifest(directory / 'site')
        require(release_id(commit, entries) == identifier, 'Release files differ from the release ID')
        return entries

    def status(self):
        staged = sorted(p.name for p in self.store.iterdir() if RELEASE_ID.fullmatch(p.name)) if self.store.is_dir() else []
        return {**self.live_state(), 'staged': staged}

    def stage(self, identifier, commit, archive, archive_sha256):
        destination, archive = self.release(identifier), Path(archive)
        with self.locked():
            if (destination / 'release.json').is_file():
                self.verify(destination, identifier, commit)
                return {'state': 'already-staged', 'releaseId': identifier, **self.live_state()}
            require(archive.is_file() and archive.stat().st_size <= MAX_ARCHIVE_BYTES, 'Release archive is missing or too large')
            require(file_digest(archive) == archive_sha256, 'Release archive SHA-256 mismatch')
            self.store.mkdir(mode=0o755, exist_ok=True)
            staging = self.store / f'.staging-{identifier}'
            for leftover in (staging, destination):
                if leftover.exists():
                    shutil.rmtree(leftover)
            (staging / 'site').mkdir(parents=True)
            try:
                with tarfile.open(archive, 'r:gz') as tar:
                    for member in tar.getmembers():
                        name = PurePosixPath(member.name)
                        require(not name.is_absolute() and '..' not in name.parts, f'Unsafe path in archive: {member.name}')
                        require(member.isfile() or member.isdir(), f'Unsupported entry: {member.name}')
                    tar.extractall(staging / 'site', filter='data')
                for name in REQUIRED:
                    require((staging / 'site' / name).is_file(), f'The release has no {name}')
                self.verify(staging, identifier, commit)
                if self.privileged:
                    self.run(['chown', '-R', OWNER, str(staging)])
                self.run(['chmod', '-R', 'u=rwX,go=rX', str(staging)])
                record = {'releaseId': identifier, 'commit': commit, 'version': version_of(staging / 'site'),
                          'stagedAtUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                (staging / 'release.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                os.rename(staging, destination)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return {'state': 'staged', 'releaseId': identifier, **self.live_state()}

    def sync(self, source, destination):
        # The way the site was deployed by hand: new files appear together at the end, then removed files go.
        # Files are compared by content: a size and time match must not leave an old file in place.
        self.run(['rsync', '-a', '--checksum', '--delete', '--delay-updates', f'{source}/', f'{destination}/'])

    def activate(self, identifier, commit):
        destination = self.release(identifier)
        with self.locked():
            require((destination / 'release.json').is_file(), 'The release is not staged')
            wanted = self.verify(destination, identifier, commit)
            before = self.live_state()
            if tree_manifest(self.live) == wanted:
                return {'state': 'unchanged', 'releaseId': identifier, 'previousVersion': before['liveVersion']}
            previous = destination / 'previous'
            if previous.exists():
                shutil.rmtree(previous)
            previous.mkdir()
            self.sync(self.live, previous)
            require(content_id(tree_manifest(previous)) == before['liveContent'], 'The copy of the live site is incomplete')
            try:
                self.sync(destination / 'site', self.live)
                require(tree_manifest(self.live) == wanted, 'The live site differs from the release after the sync')
            except BaseException:
                self.sync(previous, self.live)
                raise
            return {'state': 'activated', 'releaseId': identifier, 'previousVersion': before['liveVersion'],
                    'previousContent': before['liveContent']}

    def rollback(self, identifier, commit):
        destination = self.release(identifier)
        previous = destination / 'previous'
        with self.locked():
            require(previous.is_dir(), 'This release kept no copy of the previous site')
            live = tree_manifest(self.live)
            if live == tree_manifest(previous):
                return {'state': 'not-needed', **self.live_state()}
            require(release_id(commit, live) == identifier, 'The live site no longer belongs to this deployment; not changing it')
            self.sync(previous, self.live)
            return {'state': 'rolled-back', 'failedRelease': identifier, **self.live_state()}


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
            elif len(argv) == 4 and argv[1] in ('activate', 'rollback'):
                result = getattr(origin, argv[1])(argv[2], argv[3])
            else:
                raise ReleaseError('Usage: release_ops.py package TREE COMMIT OUTPUT | status | stage ID COMMIT ARCHIVE '
                                   'SHA256 | activate ID COMMIT | rollback ID COMMIT')
    except (ReleaseError, OSError, tarfile.TarError, UnicodeDecodeError) as error:
        print(f'share-notes release refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
