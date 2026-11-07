"""Offline contract tests; fake Play calls and synthetic AAB bytes only."""

import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import unittest
from unittest import mock
import zipfile


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "publish.py"
SPEC = importlib.util.spec_from_file_location("mathrunner_publisher", SOURCE)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
COMMIT = "8602e68745213fbc93e63281e39a1f5111466977"


def fake_aab():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("base/lib/arm64-v8a/libunity.so", b"unity")
        bundle.writestr("base/lib/arm64-v8a/libil2cpp.so", b"il2cpp")
    return stream.getvalue()


def fixture_files():
    aab = fake_aab()
    sha = hashlib.sha256(aab).hexdigest()
    report = {
        "status": "passed", "source_commit": COMMIT,
        "package": publisher.PACKAGE, "version_name": "0.1.0",
        "version_code": 1, "aab_sha256": sha,
        "signer_sha256": publisher.SIGNER_SHA256,
        "abis": ["arm64-v8a"], "license_lifecycle": "persistent-personal",
        "image": "image@sha256:" + "1" * 64, "unity_cli_version": "1.0.0-beta.17",
    }
    manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.maksonlee.mathrunner" android:versionName="0.1.0" '
        'android:versionCode="1" />'
    )
    return {
        "SportsDayMath.aab": aab,
        "SportsDayMath.aab.sha256": f"{sha}  SportsDayMath.aab\n".encode(),
        "SportsDayMath.aab.signer-sha256": (publisher.SIGNER_SHA256 + "\n").encode(),
        "release-verification.json": json.dumps(report).encode(),
        "AndroidManifest.xml": manifest.encode(),
        "signature-verification.txt": b"jar verified, with signer errors.\n",
        "Android-AndroidBuildReport.json": json.dumps({
            "result": "Succeeded", "errors": 0, "target": "Android",
        }).encode(),
    }, sha


def play_report(code=1):
    return {
        "source_build_number": 1, "version_code": code,
        "version_name": "0.1.0", "aab_sha256": "a" * 64,
    }


def attempt():
    return {
        "stage": "start", "api_events": [], "edit_created": False,
        "upload_attempted": False, "upload_response_confirmed": False,
        "commit_attempted": False, "commit_response_confirmed": False,
        "edit_delete_confirmed": None,
    }


class FakePlay:
    def __init__(self, bundles=None, releases=None, failure=None, delete_failure=None,
                 upload_code=1):
        self.bundles = bundles if bundles is not None else []
        self.releases = releases if releases is not None else []
        self.failure = failure
        self.delete_failure = delete_failure
        self.upload_code = upload_code
        self.calls = []

    def request(self, method, path, data=None, *, upload=False, operation):
        self.calls.append((operation, method, path, data, upload))
        if operation == "edit_delete" and self.delete_failure:
            raise self.delete_failure
        if operation == self.failure:
            raise publisher.PublishError(f"synthetic {operation} failure")
        if operation == "edit_insert":
            return {"id": "fake-edit"}
        if operation == "bundles_list":
            return {"bundles": self.bundles}
        if operation == "track_get":
            return {"track": "internal", "releases": self.releases}
        if operation == "bundle_upload":
            return {"versionCode": self.upload_code, "sha256": "a" * 64}
        if operation in ("track_update", "edit_commit", "edit_delete"):
            return {}
        raise AssertionError(operation)

    def operations(self):
        return [call[0] for call in self.calls]


class PublisherContractTests(unittest.TestCase):
    def test_archive_identity_and_signer_gate(self):
        files, sha = fixture_files()
        calls = []
        result = publisher.check_artifacts(
            files, sha, 1, pathlib.Path("copied.aab"), signer_check=calls.append
        )
        self.assertEqual(result["source_commit"], COMMIT)
        self.assertEqual(result["aab_sha256"], sha)
        self.assertEqual(result["fresh_jar_signature_and_signer_check"], "passed")
        self.assertEqual(calls, [pathlib.Path("copied.aab")])

    def test_archive_mismatch_fails_before_signer_gate(self):
        files, _ = fixture_files()
        calls = []
        with self.assertRaisesRegex(publisher.PublishError, "expected SHA-256"):
            publisher.check_artifacts(
                files, "0" * 64, 1, pathlib.Path("copied.aab"), signer_check=calls.append
            )
        self.assertEqual(calls, [])

    def test_strict_signature_accepts_only_pinned_self_signed_cert(self):
        colon_sha = ":".join(publisher.SIGNER_SHA256[i:i + 2] for i in range(0, 64, 2))
        jarsigner = subprocess.CompletedProcess(
            [], 4,
            "jar verified, with signer errors.\nError:\n"
            "This jar contains entries whose signer certificate is self-signed.\nWarning:\n", ""
        )
        keytool = subprocess.CompletedProcess([], 0, f"SHA256: {colon_sha}\n", "")
        with mock.patch.object(publisher.subprocess, "run", side_effect=[jarsigner, keytool]) as run:
            publisher.verify_archive_signer(pathlib.Path("copied.aab"))
        self.assertEqual(run.call_count, 2)

    def test_strict_signature_rejects_unsigned_entries(self):
        bad = subprocess.CompletedProcess([], 20, "jar verified.\nunsigned entries\n", "")
        with mock.patch.object(publisher.subprocess, "run", return_value=bad) as run:
            with self.assertRaisesRegex(publisher.PublishError, "unsigned entries"):
                publisher.verify_archive_signer(pathlib.Path("copied.aab"))
        self.assertEqual(run.call_count, 1)

    def test_selection_rejects_untrusted_build_and_status(self):
        for build, sha, status in (("../1", "a" * 64, "draft"),
                                   ("0", "a" * 64, "draft"),
                                   ("1", "bad", "draft"),
                                   ("1", "a" * 64, "production")):
            with self.subTest(build=build, status=status):
                with self.assertRaises(publisher.PublishError):
                    publisher.validate_selection(build, sha, status)

    def test_new_draft_preserves_old_completed_and_checks_track_before_upload(self):
        client = FakePlay(releases=[{"status": "completed", "versionCodes": [1]}],
                          upload_code=2)
        state = attempt()
        result = publisher.publish_transaction(client, play_report(2), b"aab", "draft", state)
        self.assertEqual(result["action"], "uploaded exact AAB once")
        self.assertLess(client.operations().index("track_get"), client.operations().index("bundle_upload"))
        track_body = next(call[3] for call in client.calls if call[0] == "track_update")
        self.assertEqual([r["status"] for r in track_body["releases"]], ["completed", "draft"])
        self.assertEqual(client.operations().count("bundle_upload"), 1)
        commit = next(call for call in client.calls if call[0] == "edit_commit")
        self.assertIsNone(commit[3])
        self.assertIn("changesInReviewBehavior=ERROR_IF_IN_REVIEW", commit[2])
        self.assertNotIn("edit_delete", client.operations())

    def test_exact_existing_draft_reused_for_completed_without_upload(self):
        client = FakePlay(
            bundles=[{"versionCode": 1, "sha256": "a" * 64}],
            releases=[{"status": "draft", "versionCodes": [1]}],
        )
        result = publisher.publish_transaction(client, play_report(), b"aab", "completed", attempt())
        self.assertEqual(result["action"], "reused exact Play bundle")
        self.assertNotIn("bundle_upload", client.operations())
        self.assertEqual(client.operations().count("edit_commit"), 1)

    def test_new_completed_replaces_only_older_completed_release(self):
        client = FakePlay(releases=[{"status": "completed", "versionCodes": [1]}],
                          upload_code=2)
        publisher.publish_transaction(client, play_report(2), b"aab", "completed", attempt())
        releases = next(call[3]["releases"] for call in client.calls if call[0] == "track_update")
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["versionCodes"], ["2"])

    def test_mismatched_existing_bundle_fails_and_deletes_own_edit(self):
        client = FakePlay(bundles=[{"versionCode": 1, "sha256": "b" * 64}])
        state = attempt()
        with self.assertRaisesRegex(publisher.PublishError, "matching bundle SHA-256"):
            publisher.publish_transaction(client, play_report(), b"aab", "completed", state)
        self.assertEqual(client.operations(), ["edit_insert", "bundles_list", "edit_delete"])
        self.assertTrue(state["edit_delete_confirmed"])

    def test_unrelated_draft_fails_before_bundle_upload(self):
        client = FakePlay(releases=[{"status": "draft", "versionCodes": [2]}])
        with self.assertRaisesRegex(publisher.PublishError, "Unrelated draft"):
            publisher.publish_transaction(client, play_report(), b"aab", "completed", attempt())
        self.assertNotIn("bundle_upload", client.operations())
        self.assertEqual(client.operations()[-1], "edit_delete")

    def test_commit_failure_and_cleanup_failure_preserve_primary(self):
        client = FakePlay(
            bundles=[{"versionCode": 1, "sha256": "a" * 64}],
            releases=[{"status": "draft", "versionCodes": [1]}],
            failure="edit_commit", delete_failure=OSError("synthetic delete failure"),
        )
        state = attempt()
        with self.assertRaisesRegex(publisher.PublishError, "synthetic edit_commit failure"):
            publisher.publish_transaction(client, play_report(), b"aab", "completed", state)
        self.assertEqual(client.operations().count("edit_commit"), 1)
        self.assertEqual(client.operations().count("edit_delete"), 1)
        self.assertTrue(state["commit_attempted"])
        self.assertFalse(state["commit_response_confirmed"])
        self.assertFalse(state["edit_delete_confirmed"])
        self.assertEqual(state["failure_stage"], "edit_commit")

    def test_upload_failure_never_retries(self):
        client = FakePlay(failure="bundle_upload")
        state = attempt()
        with self.assertRaisesRegex(publisher.PublishError, "synthetic bundle_upload failure"):
            publisher.publish_transaction(client, play_report(), b"aab", "draft", state)
        self.assertEqual(client.operations().count("bundle_upload"), 1)
        self.assertEqual(client.operations().count("edit_delete"), 1)
        self.assertFalse(state["upload_response_confirmed"])


if __name__ == "__main__":
    unittest.main()
