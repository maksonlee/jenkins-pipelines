"""Offline tests for the android-snapshots cleanup: fake AQL results and a fake Artifactory; no network."""

import importlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Jenkinsfile/artifactory/artifactory-cleanup"))
cleanup = importlib.import_module("cleanup_snapshots")


def item(path, day, size=10, name="a.aab"):
    return {"path": path, "name": name, "size": size, "created": f"2026-11-{day:02d}T10:00:00.000Z"}


def versions(app, count):
    return [item(f"com/maksonlee/{app}/1.0.{n}-SNAPSHOT", n + 1) for n in range(count)]


class PlanTest(unittest.TestCase):
    def test_keeps_the_newest_versions_of_each_app(self):
        delete, kept, skipped = cleanup.plan(versions("beepbeep", 4) + versions("share-notes", 2), 2)
        self.assertEqual([f["path"] for f in delete],
                         ["com/maksonlee/beepbeep/1.0.1-SNAPSHOT", "com/maksonlee/beepbeep/1.0.0-SNAPSHOT"])
        self.assertEqual(sorted(f["version"] for f in kept if f["app"] == "share-notes"), ["1.0.0-SNAPSHOT", "1.0.1-SNAPSHOT"])
        self.assertEqual(skipped, [])

    def test_a_folder_is_as_new_as_its_newest_file_and_nested_files_belong_to_it(self):
        items = [
            item("com/maksonlee/app/1.0.0-SNAPSHOT", 1),
            item("com/maksonlee/app/1.0.0-SNAPSHOT/mapping/release", 20, size=5, name="mapping.txt"),
            item("com/maksonlee/app/1.0.1-SNAPSHOT", 10),
        ]
        delete, kept, _ = cleanup.plan(items, 1)
        self.assertEqual([(f["version"], f["size"], f["files"]) for f in kept], [("1.0.0-SNAPSHOT", 15, 2)])
        self.assertEqual([f["version"] for f in delete], ["1.0.1-SNAPSHOT"])

    def test_math_runner_evidence_is_never_planned_for_deletion(self):
        items = [item(f"com/maksonlee/math-runner/jenkins-build-{n}-sha256-{'ab' * 32}-SNAPSHOT", n) for n in range(1, 6)]
        delete, kept, _ = cleanup.plan(items, 1)
        self.assertEqual(delete, [])
        self.assertEqual(len(kept), 5)

    def test_unexpected_paths_are_reported_and_left_alone(self):
        items = [item("com/maksonlee/app", 1), item("org/other/app/1.0-SNAPSHOT", 1), item("com/maksonlee/app/1.0", 1)]
        delete, kept, skipped = cleanup.plan(items, 1)
        self.assertEqual((delete, kept), ([], []))
        self.assertEqual(skipped, ["com/maksonlee/app", "com/maksonlee/app/1.0", "org/other/app/1.0-SNAPSHOT"])

    def test_keep_must_be_in_range(self):
        for keep in (0, 100, "5"):
            with self.subTest(keep=keep), self.assertRaises(cleanup.CleanupError):
                cleanup.plan([], keep)


class FakeArtifactory:
    def __init__(self, items, failing=()):
        self.items, self.failing, self.calls = items, set(failing), []

    def urlopen(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url))
        if req.get_method() == "POST":
            body = json.dumps({"results": self.items}).encode()
        elif any(req.full_url.endswith(path) for path in self.failing):
            raise urllib.error.HTTPError(req.full_url, 500, "error", {}, None)
        else:
            body = b""
        response = mock.MagicMock(status=200 if req.get_method() == "POST" else 204)
        response.read.return_value = body
        response.__enter__.return_value = response
        return response


class MainTest(unittest.TestCase):
    def run_main(self, fake, *args):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        report = Path(directory.name) / "report.json"
        with mock.patch.dict("os.environ", {"ARTIFACTORY_PASSWORD": "fixture"}), \
                mock.patch.object(cleanup.urllib.request, "urlopen", fake.urlopen), redirect_stdout(io.StringIO()) as out:
            try:
                cleanup.main(["--keep", "1", "--report", str(report), *args])
                error = None
            except cleanup.CleanupError as caught:
                error = caught
        return json.loads(report.read_text()), out.getvalue(), error

    def test_report_mode_never_deletes(self):
        fake = FakeArtifactory(versions("beepbeep", 3))
        report, out, error = self.run_main(fake)
        self.assertIsNone(error)
        self.assertEqual([method for method, _ in fake.calls], ["POST"])
        self.assertFalse(report["deleted"])
        self.assertEqual(len(report["delete"]), 2)
        self.assertIn("Would delete (report only) 2 version folders", out)
        self.assertNotIn("fixture", out)

    def test_delete_mode_removes_only_the_planned_folders(self):
        fake = FakeArtifactory(versions("beepbeep", 3) + versions("math-runner", 3))
        report, _, error = self.run_main(fake, "--delete")
        self.assertIsNone(error)
        deleted = sorted(url for method, url in fake.calls if method == "DELETE")
        self.assertEqual(deleted, [f"{cleanup.ARTIFACTORY_URL}/android-snapshots/com/maksonlee/beepbeep/1.0.{n}-SNAPSHOT"
                                   for n in (0, 1)])
        self.assertEqual(report["failed"], [])

    def test_a_failed_deletion_fails_the_run_and_is_recorded(self):
        fake = FakeArtifactory(versions("beepbeep", 3), failing=["beepbeep/1.0.0-SNAPSHOT"])
        report, _, error = self.run_main(fake, "--delete")
        self.assertIsInstance(error, cleanup.CleanupError)
        self.assertEqual(report["failed"], ["com/maksonlee/beepbeep/1.0.0-SNAPSHOT"])

    def test_delete_folder_refuses_protected_or_malformed_paths(self):
        for path in ("com/maksonlee/math-runner/x-SNAPSHOT", "com/maksonlee/app", "com/maksonlee/app/../x-SNAPSHOT",
                     "com/maksonlee/../x-SNAPSHOT", "com/maksonlee/app/..-SNAPSHOT"):
            with self.subTest(path=path), self.assertRaises(cleanup.CleanupError):
                cleanup.delete_folder(path, "auth")


if __name__ == "__main__":
    unittest.main()
