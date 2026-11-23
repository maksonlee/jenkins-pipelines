import hashlib
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
PHONE = 100007
PREDECESSOR = 100006
PREDECESSOR_DIGEST = "1c9619931bf521d367e65cd43abeb82424ac38b35478b67a01c1efe8d14894ae"
PHONE_BUNDLE = b"verified test phone bytes for 1.0.6"
SIGNED_DIGEST = hashlib.sha256(PHONE_BUNDLE).hexdigest()
RELEASE_INPUTS = {
    "PRODUCT_SHA": "a" * 40,
    "PIPELINE_SHA": "b" * 40,
    "SIGNED_BUILD": "17",
    "SIGNED_SHA256": SIGNED_DIGEST,
}
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
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


def bundle(code=PHONE):
    return {"versionCode": code, "sha256": SIGNED_DIGEST if code == PHONE else "d" * 64}


def track(track_name, code, **details):
    return {"track": track_name, "releases": [{
        "status": "completed", "versionCodes": [str(code)], **details,
    }]}


def live(track_name, code, state=IN_REVIEW):
    return live_releases(track_name, [(code, state)])


def live_releases(track_name, releases):
    return response({"releases": [
        {"track": track_name, "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}]}
        for code, state in releases
    ]})


def guard_responses(alpha=None, production=None, internal=None, wear_internal=None):
    return [
        alpha if alpha is not None else live("alpha", PREDECESSOR, PUBLISHED),
        production if production is not None else response(status=204),
        response(status=204),
        internal if internal is not None else live("internal", 100005, PUBLISHED),
        wear_internal if wear_internal is not None else live("wear:internal", 200005, PUBLISHED),
    ]


def edit_snapshot(catalog=None, tracks=None, live_checks=None):
    if live_checks is None:
        live_checks = guard_responses()
    if tracks is None:
        tracks = [track("alpha", PREDECESSOR), track("wear:qa", 200006)]
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
        loader_patch = patch.object(publish, "load_phone_bundle", return_value=PHONE_BUNDLE)
        oauth_patch = patch.object(publish, "get_access_token", return_value=("PRIVATE_TOKEN", 200))
        opener_patch.start()
        self.loader = loader_patch.start()
        self.oauth = oauth_patch.start()
        self.addCleanup(opener_patch.stop)
        self.addCleanup(loader_patch.stop)
        self.addCleanup(oauth_patch.stop)

    def run_with(self, *responses, release_inputs=RELEASE_INPUTS):
        self.opener.open.side_effect = responses
        report = publish.run_publish(Path("synthetic-archive"), "PRIVATE_VAULT_VALUE", release_inputs)
        self.assertNotIn("PRIVATE", json.dumps(report))
        return report

    def requests(self):
        return [call.args[0] for call in self.opener.open.call_args_list]

    def operations(self):
        return [(request.get_method(), request.full_url) for request in self.requests()]

    def mutations(self):
        return [operation for operation in self.operations() if operation[0] != "GET"]

    def test_exact_phone_alpha_advance_over_published_predecessor(self):
        catalog = [{"versionCode": PREDECESSOR, "sha256": PREDECESSOR_DIGEST}]
        report = self.run_with(
            *edit_snapshot(catalog=catalog), response(bundle(PHONE)),
            response(track("alpha", PHONE)), response({"id": EDIT_ID}), response(status=204),
        )

        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["version_name"], "1.0.6")
        self.assertEqual(report["build_number"], 17)
        self.assertEqual(report["source_sha"], RELEASE_INPUTS["PRODUCT_SHA"])
        self.assertEqual(report["source_pipeline_sha"], RELEASE_INPUTS["PIPELINE_SHA"])
        self.loader.assert_called_once_with(
            Path("synthetic-archive"), RELEASE_INPUTS["PRODUCT_SHA"], RELEASE_INPUTS["PIPELINE_SHA"],
            SIGNED_DIGEST,
        )
        self.assertEqual(report["review_observation"]["result"], "unconfirmed")
        self.assertEqual(report["live_tracks"]["alpha"], {
            "result": "ok", "http_status": 200,
            "releases": [{"lifecycle_state": PUBLISHED, "version_codes": [PREDECESSOR]}],
        })
        self.assertEqual(report["bundle_catalog"]["predecessor"]["result"], "hash_matches")
        self.assertEqual(report["edit_tracks"]["alpha"]["releases"], [
            {"status": "completed", "version_codes": [PREDECESSOR]},
        ])
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("GET", f"{APP_URL}/tracks/alpha/releases"),
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
            ("GET", f"{APP_URL}/tracks/internal/releases"),
            ("GET", f"{APP_URL}/tracks/wear:internal/releases"),
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks"),
            ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/alpha"),
            ("POST", COMMIT_URL),
            ("GET", f"{APP_URL}/tracks/alpha/releases"),
        ])
        requests = self.requests()
        self.assertEqual(requests[8].data, PHONE_BUNDLE)
        self.assertEqual(requests[8].get_header("Content-type"), "application/octet-stream")
        self.assertEqual(json.loads(requests[9].data), {
            "track": "alpha",
            "releases": [{
                "versionCodes": ["100007"], "status": "completed",
                "releaseNotes": [
                    {"language": "en-US", "text": (
                        "Phone home-screen widgets show one budget, start at 2×1, "
                        "and open budget details when tapped. "
                        "The app icon has more room inside the launcher mask."
                    )},
                    {"language": "zh-TW", "text": (
                        "手機桌面小工具每張顯示一筆預算，預設為 2×1。點一下即可開啟預算詳細資料。"
                        "App 圖示的雲朵在遮罩內保留更多留白。"
                    )},
                ],
            }],
        })
        self.assertIsNone(requests[10].data)

    def test_invalid_archive_never_reaches_oauth(self):
        self.loader.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "failed")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_invalid_signing_inputs_or_bundle_bytes_never_reach_oauth(self):
        report = self.run_with(release_inputs={**RELEASE_INPUTS, "SIGNED_SHA256": "PRIVATE_DIGEST"})
        self.assertEqual(report["artifact_verification"]["result"], "invalid_release_parameters")
        self.loader.assert_not_called()

        self.loader.return_value = b"wrong archive bytes"
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "hash_mismatch")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_read_forbidden_or_unrelated_review_never_opens_edit(self):
        for alpha, production in (
            (http_error(403), response(status=204)),
            (live("alpha", PREDECESSOR, PUBLISHED), live("production", 100004)),
        ):
            with self.subTest(case=alpha):
                self.opener.open.reset_mock()
                report = self.run_with(*guard_responses(alpha=alpha, production=production))
                self.assertEqual(report["edit_insert"]["result"], "not_attempted")
                self.assertEqual(self.mutations(), [])

    def test_pending_internal_release_blocks_before_opening_an_edit(self):
        for track, kwargs in (
            ("internal", {"internal": live("internal", 100005)}),
            ("wear:internal", {"wear_internal": live("wear:internal", 200005, NOT_SENT)}),
        ):
            with self.subTest(track=track):
                self.opener.open.reset_mock()
                report = self.run_with(*guard_responses(**kwargs))
                self.assertEqual(report["failure"], "unrelated_pending_release")
                self.assertEqual(report["live_tracks"][track]["result"], "pending_release")
                self.assertEqual(self.mutations(), [])

    def test_pending_predecessor_does_not_open_an_edit(self):
        for releases in (
            [(PREDECESSOR, NOT_SENT)],
            [(PREDECESSOR, IN_REVIEW)],
            [(PREDECESSOR - 1, PUBLISHED), (PREDECESSOR, NOT_SENT)],
        ):
            with self.subTest(releases=releases):
                self.opener.open.reset_mock()
                report = self.run_with(*guard_responses(alpha=live_releases("alpha", releases)))
                self.assertEqual(report["result"], "blocked")
                self.assertEqual(report["failure"], "target_track_changed")
                self.assertEqual(self.mutations(), [])

    def test_unobserved_alpha_predecessor_never_opens_an_edit(self):
        for result in (http_error(404), response(status=204)):
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                report = self.run_with(*guard_responses(alpha=result))
                self.assertEqual(report["failure"], "target_lifecycle_unverified")
                self.assertEqual(self.mutations(), [])

    def test_published_predecessor_with_conflicting_digest_blocks_updates(self):
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PREDECESSOR)]), response(status=204),
        )
        self.assertEqual(report["failure"], "predecessor_hash_conflict")
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_missing_empty_or_conflicting_alpha_never_creates_or_updates_track(self):
        for tracks in (
            [],
            [{"track": "alpha"}],
            [track("alpha", PREDECESSOR - 1)],
            [track("alpha", PHONE + 1)],
            [track("alpha", 200006)],
            [track("alpha", PREDECESSOR, status="inProgress")],
            [track("alpha", PREDECESSOR, status="halted")],
            [track("alpha", PREDECESSOR, countryTargeting={"countries": ["US"]})],
        ):
            with self.subTest(tracks=tracks):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(tracks=tracks), response(status=204))

                self.assertEqual(report["failure"], "target_track_changed")
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_alpha_inventory_conflicting_with_live_version_blocks(self):
        checks = guard_responses(alpha=live("alpha", PHONE, PUBLISHED))
        report = self.run_with(*edit_snapshot(live_checks=checks), response(status=204))
        self.assertEqual(report["failure"], "target_track_changed")
        self.assertEqual(report["uploads"]["alpha"]["result"], "not_attempted")

    def test_exact_current_candidate_is_not_reuploaded_or_committed(self):
        checks = guard_responses(alpha=live("alpha", PHONE, PUBLISHED))
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PHONE)], tracks=[track("alpha", PHONE)], live_checks=checks),
            response(status=204),
        )
        self.assertEqual(report["result"], "already_current")
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_existing_candidate_without_archived_hash_never_advances(self):
        for catalog in (
            [],
            [{"versionCode": PHONE, "sha256": "a" * 64}],
        ):
            with self.subTest(catalog=catalog):
                self.opener.open.reset_mock()
                checks = guard_responses(alpha=live("alpha", PHONE, PUBLISHED))
                report = self.run_with(
                    *edit_snapshot(catalog=catalog, tracks=[track("alpha", PHONE)], live_checks=checks),
                    response(status=204),
                )
                self.assertEqual(report["result"], "blocked")
                self.assertNotIn(("POST", COMMIT_URL), self.operations())

    def test_unrelated_draft_in_edit_inventory_blocks_alpha(self):
        tracks = [track("alpha", PREDECESSOR), track("PRIVATE_UNRELATED", 200006, status="draft")]
        report = self.run_with(*edit_snapshot(tracks=tracks), response(status=204))
        self.assertEqual(report["failure"], "unrelated_pending_release")
        self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_existing_matching_phone_upload_is_reused(self):
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PHONE), bundle(200006)]),
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
            (response({"id": "PRIVATE_WRONG_EDIT"}), PREDECESSOR, "commit_indeterminate"),
            (http_error(429), PREDECESSOR, "commit_indeterminate"),
            (http_error(503), PREDECESSOR, "commit_indeterminate"),
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
