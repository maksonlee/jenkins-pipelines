import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import release_ops as ops

BUILD = 31
FIRST = "a" * 32
SECOND = "b" * 32
INDEX = (
    f'<link rel="canonical" href="{ops.ORIGIN}">'
    '<script type="module" src="/assets/app.js"></script>'
    '<link rel="stylesheet" href="/assets/app.css">'
).encode()


def tree_fixture(text):
    shell = {
        "index.html": INDEX,
        "manifest.webmanifest": b'{"scope":"/"}',
        "assets/app.css": b"body {}",
        "assets/app.js": f'console.log("{text}");'.encode(),
    }
    asset = "scores/svg/repertoire/frere-jacques-bass.svg"
    reference = b"<svg/>"
    worker = b"// Offline worker body fixture.\n"
    version_material = b"".join(
        b"/" + name.encode() + shell[name]
        for name in ("index.html", "manifest.webmanifest", "assets/app.css", "assets/app.js")
    )
    release = ops.digest(version_material + asset.encode() + ops.digest(reference).encode() + worker)[:20]
    filename = ops.PACK + asset
    worker_header = (
        f'const VERSION = "{release}"\n'
        f"const CORE = {json.dumps([f'/{name}' for name in shell])}\n"
        f'const OPTIONAL = new Set(["/{filename}"])\n\n'
    ).encode()
    return release, {**shell, "service-worker.js": worker_header + worker, filename: reference}


def make_package(destination, name, *, entry_override=None, special_entry=None, meta_override=None, release_override=None):
    destination.mkdir()
    release, files = tree_fixture(name)
    if release_override:
        release = release_override
        first, rest = files["service-worker.js"].split(b"\n", 1)
        assert first.startswith(b"const VERSION = ")
        files["service-worker.js"] = f'const VERSION = "{release}"\n'.encode() + rest
    inventory = [{"path": path, "size": len(data), "sha256": ops.digest(data)} for path, data in files.items()]
    raw = ops.json_bytes({"schemaVersion": 1, "releaseId": release, "files": inventory})
    archive = destination / ops.ARCHIVE
    with tarfile.open(archive, "w:gz") as package:
        for path, data in (entry_override or files).items():
            info = tarfile.TarInfo("./" + path)
            info.size = len(data)
            package.addfile(info, io.BytesIO(data))
        if special_entry is not None:
            package.addfile(special_entry)
    metadata = {
        "schemaVersion": 1,
        "repository": ops.REPOSITORY,
        "branch": "main",
        "commit": "9" * 40,
        "ciJob": ops.CI_JOBS[0],
        "ciBuild": BUILD,
        "releaseId": release,
        "publicOrigin": ops.ORIGIN,
        "archiveSha256": ops.file_digest(archive),
        "checksumsSha256": ops.digest(raw),
        "fileCount": len(files),
        "totalBytes": sum(map(len, files.values())),
        **(meta_override or {}),
    }
    (destination / "release.json").write_bytes(ops.json_bytes(metadata))
    (destination / "checksums.json").write_bytes(raw)
    return release


def upload(directory):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as outer:
        for name in ops.ARTIFACTS:
            outer.add(directory / name, arcname=name, recursive=False)
    stream.seek(0)
    return stream


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def extracted(self, package, build=BUILD):
        output = self.root / "candidate"
        output.mkdir()
        metadata, _, entries, directories, archive = ops.load_package(package, build)
        ops.extract(archive, output, entries, directories)
        ops.verify_tree(output, entries, directories, metadata["releaseId"])
        return output

    def test_selected_build_must_match_metadata_even_with_valid_bytes(self):
        package = self.root / "package"
        make_package(package, "test")
        with self.assertRaisesRegex(ops.ReleaseError, "provenance"):
            self.extracted(package, BUILD + 1)

    def test_both_ci_job_names_are_accepted_and_any_other_is_rejected(self):
        self.assertEqual(ops.CI_JOBS, ("web/piano-practice/ci", "web/piano-practice-main-ci"))
        for index, job in enumerate(ops.CI_JOBS):
            package = self.root / f"package-{index}"
            make_package(package, "test", meta_override={"ciJob": job})
            self.assertEqual(ops.load_package(package, BUILD)[0]["ciJob"], job)
        for job in ("web/piano-practice-deploy", "web/other/ci", ""):
            package = self.root / f"foreign-{len(job)}"
            make_package(package, "test", meta_override={"ciJob": job})
            with self.subTest(job=job), self.assertRaisesRegex(ops.ReleaseError, "provenance"):
                ops.load_package(package, BUILD)

    def test_local_check_rejects_an_artifact_from_the_other_ci_job(self):
        package = self.root / "package"
        make_package(package, "test", meta_override={"ciJob": "web/piano-practice-main-ci"})
        base = ["release_ops.py", "--build", str(BUILD), "--artifacts", str(package)]
        with patch.object(ops.sys, "argv", base + ["--output", str(self.root / "a"), "--ci-job", "web/piano-practice/ci"]):
            with self.assertRaisesRegex(ops.ReleaseError, "different CI job"):
                ops.local()
        with patch.object(ops.sys, "argv", base + ["--output", str(self.root / "b"), "--ci-job", "web/piano-practice-main-ci"]):
            self.assertEqual(ops.local()["ciJob"], "web/piano-practice-main-ci")

    def test_archive_hash_mismatch_fails_before_unpacking(self):
        package = self.root / "package"
        make_package(package, "test")
        path = package / ops.ARCHIVE
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ops.ReleaseError, "Archive SHA-256"):
            self.extracted(package)

    def test_payload_bytes_checked_against_manifest_even_if_archive_hash_matches(self):
        package = self.root / "package"
        _, files = tree_fixture("test")
        files["assets/app.js"] = files["assets/app.js"].replace(b"test", b"fake")
        make_package(package, "test", entry_override=files)
        with self.assertRaisesRegex(ops.ReleaseError, "Payload digest differs"):
            self.extracted(package)

    def test_missing_member_and_unsafe_path_rejected(self):
        package = self.root / "missing"
        _, files = tree_fixture("test")
        del files["assets/app.js"]
        make_package(package, "test", entry_override=files)
        with self.assertRaisesRegex(ops.ReleaseError, "complete inventory"):
            self.extracted(package)
        escaped = tarfile.TarInfo("../unpacked")
        escaped.size = 0
        package = self.root / "escape"
        make_package(package, "test", special_entry=escaped)
        _, _, entries, directories, archive = ops.load_package(package, BUILD)
        output = self.root / "safe"
        output.mkdir()
        with self.assertRaisesRegex(ops.ReleaseError, "Unsafe archive path"):
            ops.extract(archive, output, entries, directories)
        self.assertFalse((self.root / "unpacked").exists())

    def test_symlink_and_forged_worker_identity_rejected(self):
        symlink = tarfile.TarInfo("assets/redirect")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "app.js"
        invalid = self.root / "invalid"
        make_package(invalid, "test", special_entry=symlink)
        with self.assertRaisesRegex(ops.ReleaseError, "Unexpected archive entry"):
            self.extracted(invalid)
        forged = self.root / "forged"
        make_package(forged, "test", release_override="f" * 20)
        meta, _, entries, directories, archive = ops.load_package(forged, BUILD)
        target = self.root / "forged-dist"
        target.mkdir()
        ops.extract(archive, target, entries, directories)
        with self.assertRaisesRegex(ops.ReleaseError, "Recomputed offline release"):
            ops.verify_tree(target, entries, directories, meta["releaseId"])


@unittest.skipUnless(os.name == "posix", "Origin activation uses Linux directory symlink semantics")
class OriginChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "releases").mkdir()
        (self.site / "recovery").mkdir()
        self.origin = ops.Origin(self.site)
        self.old_archive = self.root / "old"
        self.old = make_package(self.old_archive, "old")
        _, _, entries, directories, archive = ops.load_package(self.old_archive, BUILD)
        self.old_tree = self.origin.releases / self.old
        self.old_tree.mkdir()
        ops.extract(archive, self.old_tree, entries, directories)
        # Historical manual releases have no schemaVersion or releaseId property.
        legacy = {"release": self.old, "fileCount": len(entries), "totalBytes": sum(item["size"] for item in entries.values()),
                  "files": list(entries.values())}
        self.origin.manifest(self.old).write_bytes(ops.json_bytes(legacy))
        self.origin.current.symlink_to(self.old_tree, target_is_directory=True)
        self.package = self.root / "package"
        self.new = make_package(self.package, "new")

    def test_stage_does_not_switch_and_failed_smoke_can_restore_prior_tree(self):
        previous = self.origin.stamp(self.origin.current)
        self.assertEqual(self.origin.stage(FIRST, BUILD, upload(self.package), self.new)["state"], "staged")
        self.assertEqual(self.origin.stamp(self.origin.current), previous)
        activated = self.origin.activate(FIRST)
        self.assertTrue(activated["activated"])
        self.assertEqual(self.origin.stamp(self.origin.current)["releaseId"], self.new)
        rollback = self.origin.rollback(FIRST)
        self.assertTrue(rollback["restored"])
        self.assertEqual(self.origin.stamp(self.origin.current)["releaseId"], self.old)
        self.assertTrue((self.origin.releases / self.new).is_dir())

    def test_expected_release_and_lock_protect_the_origin(self):
        initial = self.origin.stamp(self.origin.current)
        with ops.site_lock(self.site):
            with self.assertRaises(BlockingIOError):
                with ops.site_lock(self.site):
                    self.fail("Another deploy acquired the same site lock")
            with self.assertRaisesRegex(ops.ReleaseError, "confirmed release ID"):
                self.origin.stage(FIRST, BUILD, upload(self.package), self.old)
        self.assertFalse(self.origin.record_path(FIRST).exists())
        self.assertFalse((self.origin.releases / self.new).exists())
        self.assertEqual(self.origin.stamp(self.origin.current), initial)

    def test_same_artifact_unchanged_and_verification_does_not_replace_link(self):
        initial = self.origin.stamp(self.origin.current)
        self.origin.stage(FIRST, BUILD, upload(self.old_archive), self.old)
        self.assertFalse(self.origin.activate(FIRST)["activated"])
        self.assertFalse(self.origin.rollback(FIRST)["restored"])
        confirmed = self.origin.confirm(FIRST)
        self.assertEqual(confirmed["state"], "verified")
        self.assertFalse(confirmed["activated"])
        self.assertEqual(self.origin.stamp(self.origin.current), initial)

    def test_stale_stage_and_foreign_current_link_are_never_mutated(self):
        self.origin.stage(FIRST, BUILD, upload(self.package), self.new)
        self.origin.stage(SECOND, BUILD, upload(self.package), self.new)
        self.origin.activate(FIRST)
        with self.assertRaisesRegex(ops.ReleaseError, "changed since staging"):
            self.origin.activate(SECOND)
        current = self.origin.current
        other = self.site / "other-link"
        other.symlink_to(self.origin.releases / self.new, target_is_directory=True)
        other.replace(current)
        external = self.origin.stamp(current)
        with self.assertRaisesRegex(ops.ReleaseError, "does not own current"):
            self.origin.rollback(FIRST)
        self.assertEqual(self.origin.stamp(current), external)

    def test_receipt_interruption_after_switch_can_be_rolled_back_safely(self):
        self.origin.stage(FIRST, BUILD, upload(self.package), self.new)
        real_record = self.origin.record

        def interrupted(document):
            if document["state"] == "activated":
                raise OSError("receipt interrupted after switch")
            return real_record(document)

        with patch.object(self.origin, "record", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "receipt interrupted"):
                self.origin.activate(FIRST)
        self.assertEqual(self.origin.stamp(self.origin.current)["releaseId"], self.new)
        self.assertTrue(self.origin.rollback(FIRST)["restored"])


if __name__ == "__main__":
    unittest.main()
