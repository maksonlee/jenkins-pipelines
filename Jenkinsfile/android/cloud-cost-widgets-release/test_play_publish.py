import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import play_publish as publish


APP_URL = (
    "https://androidpublisher.googleapis.com/androidpublisher/v3/"
    "applications/com.maksonlee.cloudcost.aws"
)
EDIT_ID = "PRIVATE_EDIT"
EDIT_URL = f"{APP_URL}/edits/{EDIT_ID}"
UPLOAD_URL = (
    "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/"
    f"applications/com.maksonlee.cloudcost.aws/edits/{EDIT_ID}/bundles?uploadType=media"
)
COMMIT_URL = f"{EDIT_URL}:commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW"
PHONE = 100006
WEAR = 200006
ARTIFACTS = {PHONE: b"verified test phone bytes", WEAR: b"verified test wear bytes"}
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"


def response(value=None, status=200):
    stream = io.BytesIO(b"" if status == 204 else json.dumps(value).encode())
    stream.status = status
    return stream


def http_error(status, message="", reason=None):
    document = {"error": {
        "status": "PERMISSION_DENIED" if status == 403 else "FAILED_PRECONDITION",
        "message": message,
        "details": [{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": reason,
            "metadata": {"editId": EDIT_ID},
        }],
    }}
    return urllib.error.HTTPError(
        "https://example.invalid/PRIVATE_URL", status, "PRIVATE_ERROR", {},
        io.BytesIO(json.dumps(document).encode()),
    )


def bundle(code):
    return {"versionCode": code, "sha256": publish.BUNDLE_SHA256[code]}


def track(track_name, code, **details):
    return {"track": track_name, "releases": [{
        "status": "completed", "versionCodes": [str(code)], **details,
    }]}


def live(track_name, code, state=IN_REVIEW):
    return response({"releases": [{
        "track": track_name, "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}],
    }]})


def edit_snapshot(catalog=None, tracks=None, live_checks=None):
    if live_checks is None:
        live_checks = [response(status=204) for _ in range(3)]
    if tracks is None:
        tracks = [track("alpha", 100005), track("wear:qa", 200005)]
    return [
        *live_checks,
        response({"id": EDIT_ID}),
        response({"bundles": [] if catalog is None else catalog}),
        response({"tracks": tracks}),
    ]


def completed_precommit():
    return [*edit_snapshot(), response(bundle(PHONE)), response(track("alpha", PHONE))]


class PlayPublishTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        opener_patch = patch.object(urllib.request, "build_opener", return_value=self.opener)
        loader_patch = patch.object(publish, "load_bundles", return_value=ARTIFACTS)
        oauth_patch = patch.object(publish, "get_access_token", return_value=("PRIVATE_TOKEN", 200))
        opener_patch.start()
        self.loader = loader_patch.start()
        self.oauth = oauth_patch.start()
        self.addCleanup(opener_patch.stop)
        self.addCleanup(loader_patch.stop)
        self.addCleanup(oauth_patch.stop)

    def run_with(self, *responses):
        self.opener.open.side_effect = responses
        report = publish.run_publish(Path("synthetic-archive"), "PRIVATE_VAULT_VALUE")
        self.assertNotIn("PRIVATE", json.dumps(report))
        return report

    def requests(self):
        return [call.args[0] for call in self.opener.open.call_args_list]

    def operations(self):
        return [(request.get_method(), request.full_url) for request in self.requests()]

    def mutations(self):
        return [operation for operation in self.operations() if operation[0] != "GET"]

    def test_exact_phone_alpha_advance_with_404_verified_in_edit(self):
        checks = [http_error(404), response(status=204), response(status=204)]
        report = self.run_with(
            *edit_snapshot(live_checks=checks), response(bundle(PHONE)),
            response(track("alpha", PHONE)), response({"id": EDIT_ID}), response(status=204),
        )

        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["review_observation"]["result"], "unconfirmed")
        self.assertEqual(report["live_tracks"]["alpha"], {
            "result": "not_found", "http_status": 404, "inventory_verified": True,
        })
        self.assertEqual(report["edit_tracks"]["alpha"]["releases"], [
            {"status": "completed", "version_codes": [100005]},
        ])
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("GET", f"{APP_URL}/tracks/alpha/releases"),
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks"),
            ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/alpha"),
            ("POST", COMMIT_URL),
            ("GET", f"{APP_URL}/tracks/alpha/releases"),
        ])
        requests = self.requests()
        self.assertEqual(requests[6].data, ARTIFACTS[PHONE])
        self.assertEqual(requests[6].get_header("Content-type"), "application/octet-stream")
        self.assertEqual(json.loads(requests[7].data), {
            "track": "alpha",
            "releases": [{
                "versionCodes": ["100006"], "status": "completed",
                "releaseNotes": [{"language": "en-US", "text": (
                    "Phone budget details show AWS forecast spending alongside the budget "
                    "target when a forecast is available. Technical details start collapsed "
                    "and can be opened below Budget settings."
                )}],
            }],
        })
        self.assertIsNone(requests[8].data)

    def test_invalid_archive_never_reaches_oauth(self):
        self.loader.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "failed")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_read_forbidden_or_unrelated_review_never_opens_edit(self):
        for alpha, production in (
            (http_error(403), response(status=204)),
            (response(status=204), live("production", 100004)),
        ):
            with self.subTest(case=alpha):
                self.opener.open.reset_mock()
                report = self.run_with(alpha, production, response(status=204))
                self.assertEqual(report["edit_insert"]["result"], "not_attempted")
                self.assertEqual(self.mutations(), [])

    def test_missing_empty_or_conflicting_alpha_never_creates_or_updates_track(self):
        for tracks in (
            [],
            [{"track": "alpha"}],
            [track("alpha", 100004)],
            [track("alpha", 100007)],
            [track("alpha", 200005)],
            [track("alpha", 100005, status="inProgress")],
            [track("alpha", 100005, status="halted")],
            [track("alpha", 100005, countryTargeting={"countries": ["US"]})],
        ):
            with self.subTest(tracks=tracks):
                self.opener.open.reset_mock()
                checks = [http_error(404), response(status=204), response(status=204)]
                report = self.run_with(*edit_snapshot(tracks=tracks, live_checks=checks), response(status=204))

                self.assertEqual(report["failure"], "target_track_changed")
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_alpha_inventory_conflicting_with_live_version_blocks(self):
        checks = [live("alpha", PHONE, PUBLISHED), response(status=204), response(status=204)]
        report = self.run_with(*edit_snapshot(live_checks=checks), response(status=204))
        self.assertEqual(report["failure"], "target_track_changed")
        self.assertEqual(report["uploads"]["alpha"]["result"], "not_attempted")

    def test_exact_current_candidate_is_not_reuploaded_or_committed(self):
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PHONE)], tracks=[track("alpha", PHONE)]),
            response(status=204),
        )
        self.assertEqual(report["result"], "already_current")
        self.assertTrue(report["live_tracks"]["alpha"]["inventory_verified"])
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_existing_candidate_without_archived_hash_never_advances(self):
        for catalog in (
            [],
            [{"versionCode": PHONE, "sha256": "a" * 64}],
        ):
            with self.subTest(catalog=catalog):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *edit_snapshot(catalog=catalog, tracks=[track("alpha", PHONE)]),
                    response(status=204),
                )
                self.assertEqual(report["result"], "blocked")
                self.assertNotIn(("POST", COMMIT_URL), self.operations())

    def test_unrelated_draft_in_edit_inventory_blocks_alpha(self):
        tracks = [track("alpha", 100005), track("PRIVATE_UNRELATED", 200005, status="draft")]
        report = self.run_with(*edit_snapshot(tracks=tracks), response(status=204))
        self.assertEqual(report["failure"], "unrelated_pending_release")
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_existing_matching_phone_upload_is_reused(self):
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PHONE), bundle(WEAR)]),
            response(track("alpha", PHONE)), response({"id": EDIT_ID}), live("alpha", PHONE),
        )
        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["uploads"]["alpha"]["result"], "reused")
        self.assertEqual(report["review_observation"]["result"], "observed_submitted")
        self.assertNotIn(("POST", UPLOAD_URL), self.operations())

    def test_wrong_phone_upload_receipt_stops_before_track_update(self):
        report = self.run_with(
            *edit_snapshot(), response({"versionCode": PHONE, "sha256": "a" * 64}), response(status=204),
        )
        self.assertEqual(report["uploads"]["alpha"]["result"], "bundle_receipt_mismatch")
        self.assertEqual(self.operations()[-2:], [("POST", UPLOAD_URL), ("DELETE", EDIT_URL)])
        self.assertEqual(report["commit"]["result"], "not_attempted")

    def test_newer_phone_bundle_blocks_candidate_without_release_mutations(self):
        report = self.run_with(
            *edit_snapshot([{"versionCode": PHONE + 1, "sha256": "a" * 64}]), response(status=204),
        )
        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["bundle_catalog"]["newer_version_codes"]["alpha"], [PHONE + 1])
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_hard_prerequisite_and_existing_review_have_one_cleanup(self):
        cases = [
            (400, "Only releases with status draft may be created on draft app. PRIVATE_TEXT", None),
            (400, "PRIVATE_TEXT", "CHANGES_ALREADY_IN_REVIEW"),
            (403, "PRIVATE_TEXT", None),
        ]
        for status, message, reason in cases:
            with self.subTest(status=status, reason=reason):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *completed_precommit(), http_error(status, message, reason), response({}),
                )
                self.assertEqual(report["result"], "rejected")
                self.assertEqual(report["commit"]["http_status"], status)
                if reason:
                    self.assertEqual(report["commit"]["google_reasons"], [reason])
                elif status == 400:
                    self.assertEqual(report["commit"]["prerequisite"], "draft_app")
                self.assertEqual(self.operations()[-2:], [("POST", COMMIT_URL), ("DELETE", EDIT_URL)])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)

    def test_ambiguous_commit_only_reconciles_exact_phone_code(self):
        cases = [
            (TimeoutError("PRIVATE_TIMEOUT"), PHONE, "submission_observed"),
            (response({"id": "PRIVATE_WRONG_EDIT"}), 100005, "commit_indeterminate"),
            (http_error(429), 100005, "commit_indeterminate"),
            (http_error(503), 100005, "commit_indeterminate"),
        ]
        for problem, code, result in cases:
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                report = self.run_with(*completed_precommit(), problem, live("alpha", code))
                self.assertEqual(report["result"], result)
                self.assertEqual(report["cleanup"]["result"], "not_attempted")
                self.assertEqual(self.operations()[-2:], [
                    ("POST", COMMIT_URL), ("GET", f"{APP_URL}/tracks/alpha/releases"),
                ])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)


if __name__ == "__main__":
    unittest.main()
