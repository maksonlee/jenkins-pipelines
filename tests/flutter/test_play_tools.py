"""Offline tests for the Flutter Play tools: fake Play API, fake signing, synthetic bundles; no network."""

import hashlib
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "resources/flutter"
sys.path.insert(0, str(TOOLS))
play_api = importlib.import_module("play_api")
preflight = importlib.import_module("play_version_preflight")
upload = importlib.import_module("play_upload")
APP = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/com.example.app"
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/com.example.app"


class FakePlay:
    """Records every request and answers like the Play API for the calls the tools make."""

    def __init__(self, bundles=(), uploaded_code=7):
        self.calls = []
        self.bundles = list(bundles)
        self.uploaded_code = uploaded_code

    def __call__(self, method, url, token=None, data=None, content_type="application/json", timeout=300):
        self.calls.append((method, url.replace(APP, "APP").replace(UPLOAD, "UPLOAD"), data))
        if url == play_api.TOKEN_URL:
            return {"access_token": "token"}
        if method == "POST" and url.endswith("/edits"):
            return {"id": "edit-1"}
        if method == "GET" and url.endswith("/bundles"):
            return {"bundles": [{"versionCode": code} for code in self.bundles]}
        if "/bundles?uploadType=media" in url:
            return {"versionCode": self.uploaded_code}
        return {}


class PlayToolsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.service = self.dir / "service.json"
        self.service.write_text(json.dumps({"client_email": "ci@example.invalid", "private_key": "synthetic"}))
        self.fake = FakePlay()
        for patcher in (
            mock.patch.object(play_api, "request_json", side_effect=lambda *a, **k: self.fake(*a, **k)),
            mock.patch.object(play_api, "openssl_sign", return_value=b"signature"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def methods(self):
        return [(method, url) for method, url, _ in self.fake.calls]

    def test_jwt_assertion_has_service_account_claims(self):
        sign = mock.Mock(return_value=b"sig")
        assertion = play_api.jwt_assertion({"client_email": "ci@example.invalid", "private_key": "k"}, now=100, sign=sign)
        header, payload, signature = assertion.split(".")
        decode = lambda part: json.loads(__import__("base64").urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        self.assertEqual(decode(header), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(decode(payload)["iss"], "ci@example.invalid")
        self.assertEqual(decode(payload)["exp"], 3700)
        self.assertEqual(signature, "c2ln")
        sign.assert_called_once()

    def test_preflight_rejects_used_version_code_and_always_deletes_edit(self):
        self.fake.bundles = [3, 5]
        with self.assertRaisesRegex(SystemExit, "greater than 5"), mock.patch("sys.stdout", new_callable=io.StringIO):
            preflight.main([str(self.service), "com.example.app", "1.0.0", "5"])
        self.assertIn(("DELETE", "APP/edits/edit-1"), self.methods())

        self.fake.calls.clear()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            preflight.main([str(self.service), "com.example.app", "1.0.1", "6"])
        self.assertIn("highest versionCode: 5", out.getvalue())
        self.assertEqual(self.methods()[-1], ("DELETE", "APP/edits/edit-1"))

    def release_inputs(self, notes=None, aab=b"release bundle"):
        bundle = self.dir / "app-release.aab"
        bundle.write_bytes(aab)
        notes_path = self.dir / "notes.json"
        notes_path.write_text(json.dumps(notes if notes is not None else {"zh-TW": " 說明 ", "en-US": "Notes"}), encoding="utf-8")
        return bundle, notes_path

    def run_upload(self, status="completed", fraction="", environ=None, notes=None, metadata_root=""):
        bundle, notes_path = self.release_inputs(notes)
        env = {"EXPECTED_AAB_SHA256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "PLAY_METADATA_ROOT": metadata_root}
        env.update(environ or {})
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            upload.main([str(self.service), str(bundle), "com.example.app", "alpha", status, fraction, str(notes_path)], env)

    def test_upload_uses_one_edit_and_commits_the_track_release(self):
        self.run_upload()
        self.assertEqual(self.methods(), [
            ("POST", play_api.TOKEN_URL),
            ("POST", "APP/edits"),
            ("POST", "UPLOAD/edits/edit-1/bundles?uploadType=media"),
            ("PUT", "APP/edits/edit-1/tracks/alpha"),
            ("POST", "APP/edits/edit-1:commit"),
        ])
        track = self.fake.calls[3][2]
        self.assertEqual(track, {"track": "alpha", "releases": [{
            "versionCodes": ["7"], "status": "completed",
            "releaseNotes": [{"language": "en-US", "text": "Notes"}, {"language": "zh-TW", "text": "說明"}],
        }]})

    def test_upload_refuses_a_bundle_other_than_the_approved_one_before_any_call(self):
        with self.assertRaisesRegex(SystemExit, "approved for upload"):
            self.run_upload(environ={"EXPECTED_AAB_SHA256": "0" * 64})
        self.assertEqual(self.fake.calls, [])

    def test_upload_checks_the_release_artifact_report(self):
        bundle, _ = self.release_inputs()
        report = self.dir / "report.json"
        good = {"status": "passed", "source_commit": "c" * 40, "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "package": "com.example.app", "version_name": "1.2.3", "version_code": 42}
        env = {"RELEASE_ARTIFACT_REPORT": str(report), "PLAY_VERSION_NAME": "1.2.3", "PLAY_VERSION_CODE": "42"}
        report.write_text(json.dumps(good))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            upload.verify_bundle(bundle.read_bytes(), "com.example.app", env, source_commit="c" * 40)
        for key, value in (("status", "failed"), ("sha256", "0" * 64), ("version_code", 41), ("source_commit", "d" * 40)):
            with self.subTest(key=key):
                report.write_text(json.dumps({**good, key: value}))
                with self.assertRaisesRegex(SystemExit, "no longer matches"):
                    upload.verify_bundle(bundle.read_bytes(), "com.example.app", env, source_commit="c" * 40)

    def test_release_notes_and_rollout_validation(self):
        for notes, message in (({}, "required"), ({"en-US": "  "}, "Invalid"), ({"en-US": "x" * 501}, "exceed 500")):
            with self.subTest(notes=notes), self.assertRaisesRegex(SystemExit, message):
                self.run_upload(notes=notes)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(upload.track_release("7", "inProgress", "0.05", [])["userFraction"], 0.05)
        for fraction in ("", "1", "0", "abc"):
            with self.subTest(fraction=fraction), self.assertRaises(SystemExit):
                upload.track_release("7", "inProgress", fraction, [])

    def test_listing_sync_replaces_images_and_updates_text_in_the_same_edit(self):
        root = self.dir / "play"
        listing = root / "listings/en-US"
        (listing / "graphics/phone-screenshots").mkdir(parents=True)
        (listing / "graphics/icon").mkdir(parents=True)
        for name, text in (("title.txt", "Title"), ("short-description.txt", "Short"), ("full-description.txt", "Full")):
            (listing / name).write_text(text, encoding="utf-8")
        (listing / "graphics/phone-screenshots/2.jpg").write_bytes(b"jpg")
        (listing / "graphics/phone-screenshots/1.png").write_bytes(b"png")
        (listing / "graphics/icon/notes.txt").write_text("ignored")
        self.run_upload(metadata_root=str(root))
        self.assertEqual(self.methods()[3:7], [
            ("PUT", "APP/edits/edit-1/listings/en-US"),
            ("DELETE", "APP/edits/edit-1/listings/en-US/phoneScreenshots"),
            ("POST", "UPLOAD/edits/edit-1/listings/en-US/phoneScreenshots?uploadType=media"),
            ("POST", "UPLOAD/edits/edit-1/listings/en-US/phoneScreenshots?uploadType=media"),
        ])
        self.assertEqual([call[2] for call in self.fake.calls[5:7]], [b"png", b"jpg"])

        (listing / "title.txt").write_text("T" * 51, encoding="utf-8")
        self.fake.calls.clear()
        with self.assertRaisesRegex(SystemExit, "exceeds 50"):
            self.run_upload(metadata_root=str(root))
        self.assertNotIn(("POST", "APP/edits/edit-1:commit"), self.methods())


class FlutterStepContractTest(unittest.TestCase):
    STEPS = {name: (ROOT / f"vars/{name}.groovy").read_text(encoding="utf-8")
             for name in ("flutterAndroid", "flutterCi", "flutterRelease")}

    def test_scripts_exist_and_parse(self):
        loaded = set()
        for text in self.STEPS.values():
            loaded |= set(re.findall(r"libraryResource\(['\"]flutter/([^'\"$]+)['\"]\)", text))
        loaded |= {"play_api.py", "play_version_preflight.py", "play_upload.py"}
        self.assertEqual(loaded, {p.name for p in TOOLS.iterdir() if p.suffix in (".sh", ".py")})
        for script in TOOLS.glob("*.sh"):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_ci_never_touches_signing_or_play(self):
        ci = self.STEPS["flutterCi"]
        for forbidden in ("prepareSigning", "playPublish", "PUBLISH_TO_PLAY", "appbundle-release", "input message"):
            self.assertNotIn(forbidden, ci)
        self.assertIn("githubPush()", ci)

    def test_release_reads_play_credentials_only_after_approval(self):
        release = self.STEPS["flutterRelease"]
        self.assertGreater(release.index("flutterAndroid.playPublish()"), release.index("input message:"))
        helper = self.STEPS["flutterAndroid"]
        self.assertEqual(helper.count("PLAY_SERVICE_VAULT_PATH,"), 1)
        self.assertIn("def playPublish()", helper[helper.index("def withPlayService"):])
        self.assertNotIn("PLAY_REQUIRE_PRODUCTION_APPROVAL", release)
        self.assertNotIn("githubPush", release)


if __name__ == "__main__":
    unittest.main()
