import base64
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.parse
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

import play_preflight


SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "example@example.invalid",
    "private_key": "SYNTHETIC_KEY",
    "token_uri": "https://example.invalid/untrusted-token-target",
}
ENCODED_ACCOUNT = base64.b64encode(json.dumps(SERVICE_ACCOUNT).encode()).decode()
ACCESS_TOKEN = "synthetic.token_123"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TRACK_URL = (
    "https://androidpublisher.googleapis.com/androidpublisher/v3/"
    "applications/com.maksonlee.cloudcost.aws/tracks"
)
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
DRAFT = "RELEASE_LIFECYCLE_STATE_DRAFT"


def response(value):
    result = io.BytesIO(json.dumps(value).encode())
    result.status = 200
    return result


def http_error(status):
    return urllib.error.HTTPError(
        "https://example.invalid/sensitive-url", status, "SENSITIVE_MESSAGE", {},
        io.BytesIO(b"SENSITIVE_RESPONSE"),
    )


class SimulatedRedirect(urllib.request.HTTPSHandler):
    def __init__(self):
        super().__init__()
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        headers = Message()
        headers["Location"] = "https://example.invalid/redirect-target"
        result = urllib.response.addinfourl(io.BytesIO(), headers, request.full_url, 302)
        result.msg = "Redirect"
        return result


class PlayPreflightTest(unittest.TestCase):
    def setUp(self):
        self.build_real_opener = urllib.request.build_opener
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        opener_patch = patch.object(
            urllib.request, "build_opener", return_value=self.opener
        )
        signing_patch = patch.object(subprocess, "run")
        opener_patch.start()
        self.signing = signing_patch.start()
        self.signing.return_value.stdout = b"synthetic-signature"
        self.addCleanup(opener_patch.stop)
        self.addCleanup(signing_patch.stop)

    def test_success_uses_only_fixed_package_gets_and_summarizes_codes(self):
        self.opener.open.side_effect = [
            response({"access_token": ACCESS_TOKEN}),
            response({"releases": [{
                "track": "production",
                "releaseLifecycleState": PUBLISHED,
                "releaseName": "UNTRUSTED_NAME",
                "activeArtifacts": [{"versionCode": 100006}, {"versionCode": 3}],
            }]}),
            response({"releases": [{"releaseLifecycleState": DRAFT}]}),
        ]
        report = play_preflight.run_preflight("data:application/json;base64," + ENCODED_ACCOUNT)

        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["oauth"], {"result": "ok", "http_status": 200})
        phone = report["tracks"]["production"]
        self.assertTrue(phone["expected_version_code_visible"])
        self.assertEqual(phone["releases"], [{
            "lifecycle_state": PUBLISHED, "version_codes": [3, 100006],
        }])
        wear = report["tracks"]["wear:production"]
        self.assertFalse(wear["expected_version_code_visible"])
        self.assertEqual(wear["expected_version_code"], 200006)
        self.assertEqual(wear["releases"], [{"lifecycle_state": DRAFT, "version_codes": []}])

        calls = self.opener.open.call_args_list
        requests = [call.args[0] for call in calls]
        self.assertEqual([(req.get_method(), req.full_url) for req in requests], [
            ("POST", TOKEN_URL),
            ("GET", f"{TRACK_URL}/production/releases"),
            ("GET", f"{TRACK_URL}/wear:production/releases"),
        ])
        oauth_body = urllib.parse.parse_qs(requests[0].data.decode())
        self.assertEqual(oauth_body["grant_type"], ["urn:ietf:params:oauth:grant-type:jwt-bearer"])
        jwt_payload = oauth_body["assertion"][0].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(jwt_payload + "=" * (-len(jwt_payload) % 4)))
        self.assertEqual(claims["aud"], TOKEN_URL)
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/androidpublisher")
        self.assertIsNone(requests[0].get_header("Authorization"))
        for request in requests[1:]:
            self.assertIsNone(request.data)
            self.assertEqual(request.get_header("Authorization"), f"Bearer {ACCESS_TOKEN}")
        self.assertTrue(all(call.kwargs["timeout"] > 0 for call in calls))
        for value in ("UNTRUSTED_NAME", ACCESS_TOKEN, SERVICE_ACCOUNT["client_email"]):
            self.assertNotIn(value, json.dumps(report))

    def test_tracks_are_independent_and_preserve_http_failure_codes(self):
        for failure_status, result in ((403, "forbidden"), (404, "not_found")):
            with self.subTest(status=failure_status):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = [
                    response({"access_token": ACCESS_TOKEN}),
                    http_error(failure_status),
                    response({}),
                ]
                report = play_preflight.run_preflight(ENCODED_ACCOUNT)

                self.assertEqual(report["result"], "failed")
                self.assertEqual(report["tracks"]["production"]["result"], result)
                self.assertEqual(report["tracks"]["production"]["http_status"], failure_status)
                self.assertEqual(report["tracks"]["wear:production"]["result"], "ok")
                self.assertEqual(self.opener.open.call_count, 3)
                self.assertNotIn("SENSITIVE", json.dumps(report))
                self.assertNotIn("sensitive-url", json.dumps(report))

    def test_malformed_track_response_fails_without_copying_server_fields(self):
        invalid_responses = [
            {"error": {}},
            {"bundles": []},
            {"releases": None},
            {"releases": [{"track": "other", "releaseLifecycleState": PUBLISHED}]},
            {"releases": [{"releaseLifecycleState": "UNTRUSTED_STATE"}]},
            {"releases": [{
                "releaseLifecycleState": PUBLISHED,
                "activeArtifacts": [{"versionCode": True}],
            }]},
        ]
        for invalid in invalid_responses:
            with self.subTest(response=invalid):
                self.opener.open.side_effect = [
                    response({"access_token": ACCESS_TOKEN}),
                    response(invalid),
                    response({"releases": []}),
                ]
                report = play_preflight.run_preflight(ENCODED_ACCOUNT)
                phone = report["tracks"]["production"]
                self.assertEqual(phone["result"], "invalid_response")
                self.assertEqual(phone["http_status"], 200)
                self.assertEqual(report["tracks"]["wear:production"]["result"], "ok")
                self.assertNotIn("UNTRUSTED_STATE", json.dumps(report))

    def test_oauth_failure_records_outcome_without_track_requests(self):
        cases = [
            ("", "missing_credentials", None, None),
            ("invalid!", "invalid_credentials", None, None),
            (ENCODED_ACCOUNT, "http_error", 400, http_error(400)),
            (ENCODED_ACCOUNT, "invalid_response", 200, response({"access_token": "fake\nkey"})),
            (ENCODED_ACCOUNT, "request_failed", None, TimeoutError("SENSITIVE_TIMEOUT")),
        ]
        for encoded, result, status, outcome in cases:
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = [outcome] if outcome is not None else []
                report = play_preflight.run_preflight(encoded)
                self.assertEqual(report["result"], "failed")
                self.assertEqual(report["oauth"], {"result": result, "http_status": status})
                self.assertTrue(all(
                    track["result"] == "not_attempted" for track in report["tracks"].values()
                ))
                self.assertEqual(self.opener.open.call_count, int(outcome is not None))
                self.assertNotIn("SENSITIVE", json.dumps(report))

    def test_cli_writes_evidence_when_signing_fails(self):
        self.signing.side_effect = subprocess.CalledProcessError(1, ["SENSITIVE_COMMAND"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "play-preflight.json"
            with patch.dict(os.environ, PLAY_SERVICE_JSON_B64=ENCODED_ACCOUNT):
                result = play_preflight.main(["play_preflight.py", str(path)])

            self.assertEqual(result, 1)
            evidence = path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(evidence)["oauth"]["result"], "signing_failed")
            self.assertNotIn("SENSITIVE_COMMAND", evidence)
            self.opener.open.assert_not_called()

    def test_redirect_is_blocked_without_forwarding_token(self):
        client = SimulatedRedirect()
        opener = self.build_real_opener(
            urllib.request.ProxyHandler({}), client, play_preflight.NoRedirect()
        )
        with self.assertRaises(play_preflight.PreflightError) as error:
            play_preflight.read_track(opener, ACCESS_TOKEN, "production")

        self.assertEqual(error.exception.result, "redirect_blocked")
        self.assertEqual(error.exception.http_status, 302)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0].full_url, f"{TRACK_URL}/production/releases")

    def test_track_response_size_is_bounded(self):
        self.opener.open.return_value = response({"releases": []})
        with patch.object(play_preflight, "MAX_RESPONSE_BYTES", 8):
            with self.assertRaises(play_preflight.PreflightError) as error:
                play_preflight.read_track(self.opener, ACCESS_TOKEN, "production")

        self.assertEqual(error.exception.result, "response_too_large")
        self.assertEqual(error.exception.http_status, 200)


if __name__ == "__main__":
    unittest.main()
