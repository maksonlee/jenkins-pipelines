"""Package, stage, activate and roll back a release of a static site that is served from one directory.

Sites: share-notes (the Flutter web build) and readerplay (the repository's public/ directory). The first argument
names the site. Locally (`package`) this turns the built tree into a
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

# Each site: the live directory, where staged releases are kept, the owner its files have, the files a release must
# hold, and the files compared with the public site after activation ('' is the front page).
SITES = {
    'share-notes': {
        'live': '/var/www/html/share-notes', 'store': '/var/www/html/share-notes-releases', 'owner': 'ubuntu:ubuntu',
        'required': ('index.html', 'main.dart.js', 'flutter_bootstrap.js', 'version.json', 'help.html',
                     'privacy.html', 'terms.html'),
    },
    'readerplay': {
        'live': '/var/www/html/readerplay', 'store': '/var/www/html/readerplay-releases', 'owner': 'root:root',
        'required': ('index.html', 'home.js', 'home.css', 'sw.js'),
    },
}
RELEASE_ID = re.compile(r'[0-9a-f]{12}-[0-9a-f]{12}')
# Staged releases kept besides the one that is live and the one just staged or activated.
KEEP_RELEASES = 5
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
    """The Flutter build's version, or for a site without one the content ID of its files."""
    try:
        data = json.loads((Path(tree) / 'version.json').read_text(encoding='utf-8'))
        return f"{data['version']}+{data['build_number']}"
    except (OSError, ValueError, KeyError, TypeError):
        return 'content-' + content_id(tree_manifest(tree))


def site_settings(site):
    require(site in SITES, 'Unknown site')
    return SITES[site]


def package(site, tree, commit, output):
    tree, output = Path(tree).resolve(), Path(output).resolve()
    for name in site_settings(site)['required']:
        require((tree / name).is_file(), f'The release has no {name}')
    entries = tree_manifest(tree)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'site-release.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(tree, arcname='.', recursive=True)
    (output / 'manifest.sha256').write_text(manifest_text(entries), encoding='utf-8')
    candidate = {
        'releaseId': release_id(commit, entries), 'commit': commit, 'version': version_of(tree),
        'files': len(entries), 'indexSha256': entries['index.html'],
        'archiveSha256': file_digest(archive), 'archiveBytes': archive.stat().st_size,
    }
    (output / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n', encoding='utf-8')
    return candidate


def run(command, **options):
    result = subprocess.run(command, capture_output=True, text=True, **options)
    require(result.returncode == 0, f'{command[0]} failed: {(result.stderr or result.stdout).strip()[-300:]}')
    return result.stdout


class Origin:
    def __init__(self, site, live=None, store=None, lock=None, runner=run, privileged=True):
        settings = site_settings(site)
        self.site, self.owner, self.required = site, settings['owner'], settings['required']
        self.live, self.store = Path(live or settings['live']), Path(store or settings['store'])
        self.lock = Path(lock or f'/var/lock/{site}-deploy.lock')
        self.run, self.privileged = runner, privileged

    def locked(self):
        handle = open(self.lock, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ReleaseError(f'Another {self.site} deployment holds the lock') from None
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

    def prune(self, *protected):
        """Remove old staged releases: all but the newest KEEP_RELEASES, never the live one or a given one."""
        live = content_id(tree_manifest(self.live))
        keep = {Path(p) for p in protected}
        others = sorted((p for p in self.store.iterdir() if p.is_dir() and RELEASE_ID.fullmatch(p.name)
                         and p not in keep and not p.name.endswith('-' + live)),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        removed = []
        for release in others[KEEP_RELEASES:]:
            shutil.rmtree(release)
            removed.append(release.name)
        return sorted(removed)

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
                for name in self.required:
                    require((staging / 'site' / name).is_file(), f'The release has no {name}')
                self.verify(staging, identifier, commit)
                if self.privileged:
                    self.run(['chown', '-R', self.owner, str(staging)])
                self.run(['chmod', '-R', 'u=rwX,go=rX', str(staging)])
                record = {'releaseId': identifier, 'commit': commit, 'version': version_of(staging / 'site'),
                          'stagedAtUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                (staging / 'release.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                os.rename(staging, destination)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return {'state': 'staged', 'releaseId': identifier, **self.live_state(), 'pruned': self.prune(destination)}

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
                    'previousContent': before['liveContent'], 'pruned': self.prune(destination)}

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
        if len(argv) == 6 and argv[2] == 'package':
            result = package(argv[1], argv[3], argv[4], argv[5])
        elif len(argv) >= 3:
            origin = Origin(argv[1])
            if argv[2:] == ['status']:
                result = origin.status()
            elif len(argv) == 7 and argv[2] == 'stage':
                result = origin.stage(argv[3], argv[4], argv[5], argv[6])
            elif len(argv) == 5 and argv[2] in ('activate', 'rollback'):
                result = getattr(origin, argv[2])(argv[3], argv[4])
            else:
                raise ReleaseError('Unknown command')
        else:
            raise ReleaseError('Usage: static_site_release.py SITE package TREE COMMIT OUTPUT | SITE status | '
                               'SITE stage ID COMMIT ARCHIVE SHA256 | SITE activate ID COMMIT | SITE rollback ID COMMIT')
    except (ReleaseError, OSError, tarfile.TarError, UnicodeDecodeError) as error:
        print(f'site release refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
