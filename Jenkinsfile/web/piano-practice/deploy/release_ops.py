#!/usr/bin/python3 -I
"""Verify a Piano Practice CI release and operate the site through fixed SSH verbs."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tempfile

ARCHIVE = "piano-practice-dist.tar.gz"
ARTIFACTS = ("release.json", "checksums.json", ARCHIVE)
# web/piano-practice-main-ci built every release before the jobs moved to web/piano-practice/; its artifacts
# must stay deployable, because the rollback targets on the origin were built by it.
CI_JOBS = ("web/piano-practice/ci", "web/piano-practice-main-ci")
REPOSITORY = "https://github.com/maksonlee/piano-practice.git"
ORIGIN = "https://piano.maksonlee.com/"
SITE = Path("/var/www/piano-practice")
PACK = "resources/piano-practice-v1/"
MAX_FILES = 10_000
MAX_BYTES = 256 * 1024 * 1024
METADATA_LIMITS = {"release.json": 4096, "checksums.json": 2 * 1024 * 1024, ARCHIVE: MAX_BYTES}


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data):
    return sha256(data).hexdigest()


def file_digest(path):
    with path.open("rb") as file:
        checked = sha256()
        for chunk in iter(lambda: file.read(128 * 1024), b""):
            checked.update(chunk)
        return checked.hexdigest()


def read_regular(path, limit):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= limit, f"Invalid file {path}")
    return path.read_bytes()


def parse_json(raw):
    def reject_duplicates(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "Duplicate JSON property")
        return result

    return json.loads(raw, object_pairs_hook=reject_duplicates)


def proper_directory(path):
    require(stat.S_ISDIR(path.lstat().st_mode), f"Expected real directory {path}")


def safe_name(name):
    return isinstance(name, str) and all(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", component)
        for component in name.split("/")
    )


def strict_size(value, maximum=MAX_BYTES):
    return type(value) is int and 0 <= value <= maximum


def parents_of(name):
    components = name.split("/")
    return {"/".join(components[:index]) for index in range(1, len(components))}


def read_manifest(raw, release, *, legacy=False):
    data = parse_json(raw)
    require(isinstance(data, dict), "Invalid checksum manifest")
    versioned = type(data.get("schemaVersion")) is int and data.get("schemaVersion") == 1
    require(
        (versioned and data.get("releaseId") == release)
        or (legacy and data.get("release") == release),
        "Checksum manifest release identity differs",
    )
    raw_entries = data.get("files")
    require(isinstance(raw_entries, list) and 0 < len(raw_entries) <= MAX_FILES, "Invalid inventory size")
    entries = {}
    for entry in raw_entries:
        require(isinstance(entry, dict) and set(entry) == {"path", "size", "sha256"}, "Invalid inventory entry")
        name = entry["path"]
        require(safe_name(name), "Unsafe inventory path")
        require(strict_size(entry["size"]) and matches(r"[0-9a-f]{64}", entry["sha256"]), f"Invalid file evidence for {name}")
        require(name not in entries, f"Duplicate inventory path {name}")
        entries[name] = entry
    directories = set().union(*(parents_of(name) for name in entries))
    folded = {name.casefold() for name in {*entries, *directories}}
    require(len(folded) == len(entries) + len(directories), "Case collision or file/directory conflict")
    total = sum(entry["size"] for entry in entries.values())
    require(total <= MAX_BYTES, "Release size exceeds the extraction limit")
    if not versioned:
        require(data.get("fileCount") == len(entries) and data.get("totalBytes") == total, "Legacy totals differ")
    return entries, directories, total


def load_package(directory, build):
    proper_directory(directory)
    raw = {
        name: read_regular(directory / name, limit)
        for name, limit in METADATA_LIMITS.items()
        if name != ARCHIVE
    }
    metadata = parse_json(raw["release.json"])
    expected_fields = {
        "schemaVersion", "repository", "branch", "commit", "ciJob", "ciBuild", "releaseId",
        "publicOrigin", "archiveSha256", "checksumsSha256", "fileCount", "totalBytes",
    }
    require(isinstance(metadata, dict) and set(metadata) == expected_fields, "Invalid release metadata")
    require(type(metadata["schemaVersion"]) is int and metadata["schemaVersion"] == 1, "Unsupported release schema")
    require(
        metadata["repository"] == REPOSITORY and metadata["branch"] == "main"
        and metadata["ciJob"] in CI_JOBS and type(metadata["ciBuild"]) is int and metadata["ciBuild"] == build
        and metadata["publicOrigin"] == ORIGIN,
        "Artifact provenance differs from the selected CI build",
    )
    release = metadata["releaseId"]
    require(matches(r"[0-9a-f]{20}", release) and matches(r"[0-9a-f]{40}", metadata["commit"]), "Invalid release or commit ID")
    require(matches(r"[0-9a-f]{64}", metadata["archiveSha256"]), "Invalid archive digest")
    require(metadata["checksumsSha256"] == digest(raw["checksums.json"]), "Manifest SHA-256 mismatch")
    entries, directories, total = read_manifest(raw["checksums.json"], release)
    require(
        strict_size(metadata["fileCount"], MAX_FILES) and metadata["fileCount"] == len(entries)
        and strict_size(metadata["totalBytes"]) and metadata["totalBytes"] == total,
        "Release metadata totals differ",
    )
    archive = directory / ARCHIVE
    archive_info = archive.lstat()
    require(stat.S_ISREG(archive_info.st_mode) and archive_info.st_size <= MAX_BYTES, "Invalid release archive")
    require(file_digest(archive) == metadata["archiveSha256"], "Archive SHA-256 mismatch")
    return metadata, raw, entries, directories, archive


def tar_name(member):
    name = member.name.removeprefix("./")
    if member.isdir():
        name = name.rstrip("/")
    return name


def copy_member(archive, member, destination, *, public):
    with archive.extractfile(member) as content, destination.open("xb") as file:
        remaining = member.size
        while remaining:
            chunk = content.read(min(remaining, 128 * 1024))
            require(chunk, f"Truncated archive entry {member.name}")
            file.write(chunk)
            remaining -= len(chunk)
    destination.chmod(0o644 if public else 0o600)


def extract(archive_file, destination, entries, directories):
    proper_directory(destination)
    require(not any(destination.iterdir()), "Extraction root must be empty")
    seen_files = set()
    seen_directories = set()
    with tarfile.open(archive_file, mode="r:gz") as archive:
        for member in archive:
            name = tar_name(member)
            if member.isdir() and name in {"", "."}:
                require("." not in seen_directories, "Duplicate archive root")
                seen_directories.add(".")
                continue
            require(safe_name(name), "Unsafe archive path")
            if member.isdir():
                require(name in directories and name not in seen_directories, f"Unexpected directory {name}")
                seen_directories.add(name)
                continue
            require(
                member.type in {tarfile.REGTYPE, tarfile.AREGTYPE} and not member.linkname and not member.sparse
                and name in entries and name not in seen_files and member.size == entries[name]["size"],
                f"Unexpected archive entry {name}",
            )
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True)
            copy_member(archive, member, output, public=True)
            require(file_digest(output) == entries[name]["sha256"], f"Payload digest differs for {name}")
            seen_files.add(name)
    require(seen_files == entries.keys(), "Archive did not contain the complete inventory")
    for name in directories:
        (destination / name).chmod(0o755)
    destination.chmod(0o755)


class IndexReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = []
        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("type") == "module":
            self.scripts.append(values.get("src"))
        elif tag == "link" and values.get("rel") == "canonical":
            self.canonical.append(values.get("href"))
        elif tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))


def verify_runtime(tree, entries, release):
    for file in ("index.html", "service-worker.js", "manifest.webmanifest"):
        require(file in entries, f"Missing required shell file {file}")
    header = (tree / "service-worker.js").read_bytes().split(b"\n", 4)
    require(len(header) == 5 and header[0] == f'const VERSION = "{release}"'.encode() and not header[3], "Service worker version or header differs")
    require(header[1].startswith(b"const CORE = ") and header[2].startswith(b"const OPTIONAL = new Set(")
            and header[2].endswith(b")"), "Invalid service worker inventory")
    cache_lists = [parse_json(header[1][13:]), parse_json(header[2][25:-1])]
    require(all(isinstance(items, list) for items in cache_lists), "Invalid service worker cache lists")
    urls = cache_lists[0] + cache_lists[1]
    require(all(isinstance(url, str) and url.startswith("/") and url[1:] in entries for url in urls), "Invalid service worker cache reference")
    require(len(set(urls)) == len(urls), "Duplicate service worker cache reference")
    assets = sorted(path for path in entries if path.startswith("assets/"))
    shell = ["index.html", "manifest.webmanifest", *assets]
    runtime = sorted(path for path in entries if path.startswith(PACK))
    require(runtime, "Missing bundled piano resources")
    require(set(urls) == {f"/{name}" for name in shell + runtime}, "Incomplete service worker inventory")
    require({f"/{name}" for name in shell} <= set(cache_lists[0]), "Shell files must be precached")
    version = sha256()
    for name in shell:
        version.update(f"/{name}".encode())
        version.update((tree / name).read_bytes())
    for name in runtime:
        version.update(name.removeprefix(PACK).encode())
        version.update(entries[name]["sha256"].encode())
    version.update(header[4])
    require(version.hexdigest()[:20] == release, "Recomputed offline release ID differs")
    index = IndexReferences()
    index.feed((tree / "index.html").read_text(encoding="utf-8"))
    require(index.canonical == [ORIGIN], "Canonical origin differs")
    require(len(index.scripts) == 1 and index.stylesheets and all(
        isinstance(url, str) and url.startswith("/assets/") and url[1:] in entries
        for url in index.scripts + index.stylesheets
    ), "Invalid app script or stylesheet")


def verify_tree(tree, entries, directories, release):
    proper_directory(tree)
    actual = set()
    actual_dirs = set()
    for path in tree.rglob("*"):
        info = path.lstat()
        name = path.relative_to(tree).as_posix()
        if stat.S_ISDIR(info.st_mode):
            actual_dirs.add(name)
            continue
        require(
            stat.S_ISREG(info.st_mode) and name in entries and info.st_size == entries[name]["size"]
            and file_digest(path) == entries[name]["sha256"],
            f"Unlisted or altered release file: {name}",
        )
        actual.add(name)
    require(actual == entries.keys() and actual_dirs == directories, "Release inventory is incomplete")
    verify_runtime(tree, entries, release)


def receive_bundle(stream, target):
    seen = set()
    with tarfile.open(fileobj=stream, mode="r|") as bundle:
        for member in bundle:
            name = member.name
            require(
                member.type in {tarfile.REGTYPE, tarfile.AREGTYPE} and not member.linkname
                and not member.sparse and name in METADATA_LIMITS and name not in seen
                and member.size <= METADATA_LIMITS[name],
                "Invalid SSH upload bundle",
            )
            copy_member(bundle, member, target / name, public=False)
            seen.add(name)
    require(seen == set(ARTIFACTS), "Incomplete SSH upload bundle")


def sync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_record(path, raw):
    fd, name = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(document):
    return (json.dumps(document, indent=2) + "\n").encode()


class Origin:
    def __init__(self, root):
        self.root = root
        self.current = root / "current"
        self.releases = root / "releases"
        self.recovery = root / "recovery"
        for directory in (root, self.releases, self.recovery):
            proper_directory(directory)

    def stamp(self, link):
        info = link.lstat()
        require(stat.S_ISLNK(info.st_mode), f"Expected an atomic release link at {link}")
        target = Path(os.readlink(link))
        require(target.parent == self.releases and matches(r"[0-9a-f]{20}", target.name), "Current points outside site releases")
        return {"releaseId": target.name, "device": info.st_dev, "inode": info.st_ino, "mtimeNs": info.st_mtime_ns}

    def manifest(self, release):
        return self.recovery / f"{release}.checksums.json"

    def existing(self, release):
        entries, directories, _ = read_manifest(
            read_regular(self.manifest(release), METADATA_LIMITS["checksums.json"]), release, legacy=True
        )
        verify_tree(self.releases / release, entries, directories, release)
        return entries

    def record_path(self, attempt):
        require(matches(r"[0-9a-f]{32}", attempt), "Invalid operation ID")
        return self.recovery / f"jenkins-{attempt}.json"

    def record(self, document):
        write_record(self.record_path(document["attempt"]), json_bytes(document))
        return document

    def status(self, attempt):
        document = parse_json(read_regular(self.record_path(attempt), 32 * 1024))
        require(document.get("attempt") == attempt, "Attempt receipt differs")
        return document

    def stage(self, attempt, build, stream, expected_release):
        require(not os.path.lexists(self.record_path(attempt)), "Attempt already exists")
        previous = self.stamp(self.current)
        self.existing(previous["releaseId"])
        with tempfile.TemporaryDirectory(prefix="piano-upload-") as upload:
            uploaded = Path(upload)
            receive_bundle(stream, uploaded)
            meta, raw, entries, directories, archive = load_package(uploaded, build)
            release = meta["releaseId"]
            require(release == expected_release, "Origin package differs from the confirmed release ID")
            candidate = self.releases / release
            manifest_path = self.manifest(release)
            with tempfile.TemporaryDirectory(dir=self.releases, prefix=".piano-stage-") as staging:
                tree = Path(staging) / "release"
                tree.mkdir()
                extract(archive, tree, entries, directories)
                verify_tree(tree, entries, directories, release)
                if os.path.lexists(candidate):
                    verify_tree(candidate, entries, directories, release)
                if os.path.lexists(manifest_path):
                    stored, _, _ = read_manifest(
                        read_regular(manifest_path, METADATA_LIMITS["checksums.json"]), release, legacy=True
                    )
                    require(stored == entries, "Existing recovery inventory differs")
                else:
                    write_record(manifest_path, raw["checksums.json"])
                if not os.path.lexists(candidate):
                    tree.rename(candidate)
                    sync_dir(self.releases)
        require(previous == self.stamp(self.current), "Active release changed while staging")
        return self.record({"attempt": attempt, "state": "staged", "source": meta, "previous": previous, "stagedAtUtc": timestamp()})

    def activate(self, attempt):
        document = self.status(attempt)
        require(document["state"] == "staged", "Activation requires a fresh staged attempt")
        prior = document["previous"]
        release = document["source"]["releaseId"]
        require(prior == self.stamp(self.current), "Active release changed since staging")
        self.existing(release)
        self.existing(prior["releaseId"])
        if prior["releaseId"] == release:
            return self.record({**document, "state": "unchanged", "activated": False, "candidate": prior})
        temporary = self.root / f".current-{attempt}"
        temporary.symlink_to(self.releases / release, target_is_directory=True)
        try:
            candidate = self.stamp(temporary)
            pending = {**document, "state": "activating", "candidate": candidate}
            self.record(pending)
            require(prior == self.stamp(self.current), "Active release changed before switch")
            os.replace(temporary, self.current)
            sync_dir(self.root)
            require(candidate == self.stamp(self.current), "Switch link verification failed")
            return self.record({**pending, "state": "activated", "activated": True, "activatedAtUtc": timestamp()})
        finally:
            temporary.unlink(missing_ok=True)

    def confirm(self, attempt):
        document = self.status(attempt)
        require(document["state"] in {"activated", "unchanged"}, "Confirmation requires an activation check")
        require(document["candidate"] == self.stamp(self.current), "Candidate no longer owns current")
        return self.record({**document, "state": "verified", "verifiedAtUtc": timestamp()})

    def rollback(self, attempt):
        document = self.status(attempt)
        if document["state"] not in {"activating", "activated", "rolling-back"}:
            return {"attempt": attempt, "state": document["state"], "restored": False}
        actual = self.stamp(self.current)
        prior = document["previous"]
        if document["state"] == "activating" and actual == prior:
            return self.record({**document, "state": "cancelled", "restored": False})
        if document["state"] == "rolling-back" and actual == document["rollbackLink"]:
            return self.record({**document, "state": "rolled-back", "restored": True, "rolledBackAtUtc": timestamp()})
        require(actual == document["candidate"], "Rollback refused because this attempt does not own current")
        self.existing(prior["releaseId"])
        with tempfile.TemporaryDirectory(dir=self.root, prefix=".rollback-") as staging:
            temporary = Path(staging) / "restored"
            temporary.symlink_to(self.releases / prior["releaseId"], target_is_directory=True)
            rollback_link = self.stamp(temporary)
            rolling = {**document, "state": "rolling-back", "rollbackLink": rollback_link}
            self.record(rolling)
            require(actual == self.stamp(self.current), "Active release changed before rollback")
            os.replace(temporary, self.current)
            sync_dir(self.root)
            require(rollback_link == self.stamp(self.current), "Rollback link verification failed")
            return self.record({**rolling, "state": "rolled-back", "restored": True, "rolledBackAtUtc": timestamp()})


@contextmanager
def site_lock(root):
    import fcntl

    proper_directory(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def remote(command):
    request = re.fullmatch(
        r"(stage|activate|confirm|rollback) ([0-9a-f]{32})(?: ([1-9][0-9]{0,8}) ([0-9a-f]{20}))?",
        command,
    )
    require(request, "Unknown origin request")
    verb, attempt, build, expected_release = request.groups()
    require((verb == "stage") == (build is not None), "Unexpected CI build argument")
    with site_lock(SITE):
        origin = Origin(SITE)
        if verb == "stage":
            return origin.stage(attempt, int(build), sys.stdin.buffer, expected_release)
        return getattr(origin, verb)(attempt)


def local():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-release")
    parser.add_argument("--ci-job", choices=CI_JOBS)
    args = parser.parse_args()
    require(0 < args.build <= 999_999_999, "Invalid CI build number")
    meta, _, entries, directories, archive = load_package(args.artifacts, args.build)
    require(
        args.expected_release is None or args.expected_release == meta["releaseId"],
        "Artifact differs from the confirmed release ID",
    )
    require(args.ci_job is None or args.ci_job == meta["ciJob"], "Artifact was built by a different CI job")
    with tempfile.TemporaryDirectory(dir=args.output.parent, prefix=".piano-local-") as staging:
        tree = Path(staging) / "release"
        tree.mkdir()
        extract(archive, tree, entries, directories)
        verify_tree(tree, entries, directories, meta["releaseId"])
        require(not os.path.lexists(args.output), "Local staging destination already exists")
        tree.rename(args.output)
    return meta


if __name__ == "__main__":
    try:
        if Path(sys.argv[0]).name == "piano-practice-origin":
            require(len(sys.argv) == 2, "The installed origin helper accepts one forced SSH command")
            result = remote(sys.argv[1])
        else:
            result = local()
        print(json.dumps(result), flush=True)
    except (ReleaseError, OSError, ValueError, tarfile.TarError) as error:
        print(f"Piano Practice release check failed: {error}", file=sys.stderr)
        sys.exit(1)
