import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

import play_submission_preflight as submission


APP_URL = (
    "https://androidpublisher.googleapis.com/androidpublisher/v3/"
    "applications/com.maksonlee.cloudcost.aws"
)
EDIT_ID = "SENSITIVE_EDIT_123"
EDIT_URL = f"{APP_URL}/edits/{EDIT_ID}"
TOKEN = "SENSITIVE_TOKEN"
DRAFT_STATE = "RELEASE_LIFECYCLE_STATE_DRAFT"
EXPECTED_PHONE_HASH = "1c9619931bf521d367e65cd43abeb82424ac38b35478b67a01c1efe8d14894ae"


def response(value=None, status=200):
    body = b"" if status == 204 else json.dumps(value).encode()
    result = io.BytesIO(body)
    result.status = status
    return result


def http_error(code, body):
    return urllib.error.HTTPError(
        "https://example.invalid/SENSITIVE_URL", code, "SENSITIVE_REASON", {},
        io.BytesIO(json.dumps(body).encode()),
    )


class SubmissionPreflightTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        opener_patch = patch.object(urllib.request, "build_opener", return_value=self.opener)
        oauth_patch = patch.object(submission, "get_access_token", return_value=(TOKEN, 200))
        opener_patch.start()
        oauth_patch.start()
        self.addCleanup(opener_patch.stop)
        self.addCleanup(oauth_patch.stop)

    def run_with_responses(self, *responses):
        self.opener.open.side_effect = responses
        return submission.run_preflight("SYNTHETIC_CREDENTIAL")

    def assert_requests(self, operations):
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual([(req.get_method(), req.full_url) for req in requests], operations)
        for request in requests:
            self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
            if request.get_method() == "POST":
                self.assertEqual(request.data, b"{}")
                self.assertEqual(request.get_header("Content-type"), "application/json")
            else:
                self.assertIsNone(request.data)

    def candidate_snapshot(self, bundles=None, listings=None, cleanup=None):
        return [
            response(status=204),
            response(status=204),
            response({"id": EDIT_ID}),
            bundles if bundles is not None else response({"bundles": []}),
            response({"track": "production", "releases": [{
                "versionCodes": ["41"], "status": "completed", "name": "SENSITIVE_RELEASE_NAME",
            }]}),
            response({"track": "wear:production"}),
            listings if listings is not None else response({"listings": [{
                "language": "en-US",
                "title": "SENSITIVE_TITLE",
                "shortDescription": "SENSITIVE_SHORT_DESCRIPTION",
                "fullDescription": "SENSITIVE_FULL_DESCRIPTION",
                "video": "https://example.invalid/SENSITIVE_VIDEO",
            }]}),
            cleanup if cleanup is not None else response(status=204),
        ]

    def test_pending_release_blocks_insert_after_reading_both_tracks(self):
        report = self.run_with_responses(
            response({"releases": [{"releaseLifecycleState": DRAFT_STATE}]}),
            response(status=204),
        )

        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["live_tracks"]["production"]["result"], "pending_release")
        self.assertEqual(report["live_tracks"]["wear:production"]["result"], "no_content")
        self.assertEqual(report["edit_insert"]["result"], "not_attempted")
        self.assert_requests([
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
        ])

    def test_live_track_error_blocks_insert_without_masking_other_track(self):
        report = self.run_with_responses(
            http_error(403, {"error": {"message": "SENSITIVE_MESSAGE"}}),
            response({"releases": []}),
        )

        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["live_tracks"]["production"]["http_status"], 403)
        self.assertEqual(report["live_tracks"]["wear:production"]["result"], "ok")
        self.assertEqual(report["edit_insert"]["result"], "not_attempted")
        self.assertEqual(self.opener.open.call_count, 2)

    def test_snapshots_fixed_tracks_and_deletes_only_the_received_edit(self):
        report = self.run_with_responses(*self.candidate_snapshot(
            bundles=response({"bundles": [{"versionCode": 100006, "sha256": EXPECTED_PHONE_HASH}]}),
        ))

        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["source_sha"], "f6cce0cc9e7aa060b3013a447c220d149b31f80b")
        self.assertEqual(report["bundles"]["candidates"]["production"]["result"], "hash_matches")
        self.assertEqual(report["bundles"]["candidates"]["wear:production"]["result"], "not_seen")
        self.assertEqual(report["edit_tracks"]["production"]["releases"], [
            {"status": "completed", "version_codes": [41]},
        ])
        self.assertEqual(report["listings"]["locales"], [{"locale": "en-US", "text_complete": True}])
        self.assertEqual(report["cleanup"], {"result": "ok", "http_status": 204})
        self.assertNotIn("SENSITIVE", json.dumps(report))
        self.assert_requests([
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks/production"),
            ("GET", f"{EDIT_URL}/tracks/wear:production"),
            ("GET", f"{EDIT_URL}/listings"),
            ("DELETE", EDIT_URL),
        ])

    def test_insert_not_found_filters_error_and_stops(self):
        report = self.run_with_responses(
            response(status=204), response(status=204),
            http_error(404, {"error": {
                "status": "NOT_FOUND",
                "message": "SENSITIVE_MESSAGE",
                "errors": [
                    {"reason": "notFound"}, {"reason": "packageNotFound"},
                    {"reason": "SENSITIVE_SERVER_REASON"},
                ],
                "details": [{"@type": "type.googleapis.com/google.rpc.ResourceInfo",
                             "resourceName": "SENSITIVE_RESOURCE", "reason": "forbidden"}],
            }}),
        )

        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["edit_insert"], {
            "result": "not_found", "http_status": 404,
            "google_status": "NOT_FOUND", "google_reasons": ["notFound", "packageNotFound"],
        })
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertNotIn("SENSITIVE", json.dumps(report))
        self.assert_requests([
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
            ("POST", f"{APP_URL}/edits"),
        ])

    def test_invalid_receipt_cannot_change_path_or_trigger_cleanup(self):
        report = self.run_with_responses(
            response(status=204), response(status=204),
            response({"id": "../../SENSITIVE_EDIT"}),
        )

        self.assertEqual(report["edit_insert"], {"result": "invalid_edit_receipt", "http_status": 200})
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertNotIn("SENSITIVE", json.dumps(report))

    def test_cleanup_runs_once_even_if_a_readback_fails(self):
        report = self.run_with_responses(*self.candidate_snapshot(
            bundles=http_error(403, {"error": {"message": "SENSITIVE_MESSAGE"}}),
            cleanup=response({}),
        ))

        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["bundles"]["result"], "forbidden")
        self.assertEqual(report["listings"]["result"], "ok")
        self.assertEqual(report["cleanup"], {"result": "ok", "http_status": 200})
        deletes = [
            call.args[0].full_url for call in self.opener.open.call_args_list
            if call.args[0].get_method() == "DELETE"
        ]
        self.assertEqual(deletes, [EDIT_URL])

    def test_unexpected_collection_payloads_fail_closed(self):
        report = self.run_with_responses(*self.candidate_snapshot(
            bundles=response({"kind": "androidpublisher#listingsListResponse"}),
            listings=response({"unrelated": "SENSITIVE_TEXT"}),
        ))

        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["bundles"]["result"], "invalid_response")
        self.assertEqual(report["listings"]["result"], "invalid_response")
        self.assertEqual(report["cleanup"]["result"], "ok")
        self.assertNotIn("SENSITIVE", json.dumps(report))

    def test_candidate_hash_conflict_blocks_and_cleans_up(self):
        report = self.run_with_responses(*self.candidate_snapshot(
            bundles=response({"bundles": [{"versionCode": 100006, "sha256": "a" * 64}]}),
        ))

        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["bundles"]["result"], "candidate_conflict")
        self.assertEqual(report["bundles"]["candidates"]["production"]["result"], "hash_conflict")
        self.assertEqual(report["cleanup"]["result"], "ok")

    def test_cleanup_failure_is_recorded_without_retrying(self):
        report = self.run_with_responses(*self.candidate_snapshot(
            cleanup=http_error(403, {"error": {"message": "SENSITIVE_MESSAGE"}}),
        ))

        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["failure"], "cleanup_failed")
        self.assertEqual(report["cleanup"], {"result": "forbidden", "http_status": 403})
        self.assertEqual(self.opener.open.call_count, 8)
        self.assertNotIn("SENSITIVE", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
