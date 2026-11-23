import io
import json
import unittest
from unittest.mock import Mock, patch

import play_alpha_audit as audit


APP_URL = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/com.maksonlee.cloudcost.aws"
EDIT_URL = f"{APP_URL}/edits/PRIVATE_EDIT"
DIGEST = "ab" * 32


def response(content=None, status=200):
    stream = io.BytesIO(json.dumps(content).encode() if content is not None else b"")
    stream.status = status
    return stream


class AlphaAuditTest(unittest.TestCase):
    def test_one_disposable_readback_checks_notes_and_signed_digest(self):
        for notes, digest, observed, note_results in (
            (audit.RELEASE_NOTES, DIGEST, True, {"en-US": "matches", "zh-TW": "matches"}),
            (None, DIGEST, True, {"en-US": "absent", "zh-TW": "absent"}),
            ([{"language": "en-US", "text": "PRIVATE_MISMATCH"}], DIGEST, False,
             {"en-US": "text_mismatch", "zh-TW": "absent"}),
            (None, "cd" * 32, False, {"en-US": "absent", "zh-TW": "absent"}),
        ):
            with self.subTest(observed=observed, notes=note_results):
                release = {"status": "completed", "versionCodes": ["100008"]}
                if notes is not None:
                    release["releaseNotes"] = notes
                opener = Mock()
                opener.open.side_effect = [
                    response({"id": "PRIVATE_EDIT"}),
                    response({"track": "alpha", "releases": [release]}),
                    response({"bundles": [{"versionCode": 100008, "sha256": digest}]}),
                    response(status=204),
                ]
                with patch.object(audit.urllib.request, "build_opener", return_value=opener), \
                        patch.object(audit, "get_access_token", return_value=("PRIVATE_TOKEN", 200)):
                    report = audit.run_audit("PRIVATE_VAULT", DIGEST)

                requests = [call.args[0] for call in opener.open.call_args_list]
                self.assertEqual([(request.get_method(), request.full_url) for request in requests], [
                    ("POST", f"{APP_URL}/edits"),
                    ("GET", f"{EDIT_URL}/tracks/alpha"),
                    ("GET", f"{EDIT_URL}/bundles"),
                    ("DELETE", EDIT_URL),
                ])
                self.assertEqual(report["result"], "observed_in_edit" if observed else "unconfirmed")
                self.assertEqual(report["alpha_track"]["release_notes"], note_results)
                self.assertEqual(report["public_lifecycle"]["result"], "not_verified")
                self.assertEqual(report["cleanup"]["result"], "deleted")
                self.assertNotIn("PRIVATE", json.dumps(report))

    def test_malformed_digest_stops_before_oauth(self):
        with patch.object(audit.urllib.request, "build_opener") as build_opener:
            report = audit.run_audit("PRIVATE_VAULT", DIGEST.upper())
        self.assertEqual(report["result"], "invalid_signed_sha256")
        build_opener.assert_not_called()
