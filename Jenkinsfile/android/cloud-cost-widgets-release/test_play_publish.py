import hashlib
import io
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import play_publish as publish


PACKAGE = "com.maksonlee.cloudcost.aws"
APP_URL = f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{PACKAGE}"
EDIT_ID = "PRIVATE_EDIT"
EDIT_URL = f"{APP_URL}/edits/{EDIT_ID}"
UPLOAD_URL = (
    "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/"
    f"applications/{PACKAGE}/edits/{EDIT_ID}/bundles?uploadType=media"
)
COMMIT_URL = f"{EDIT_URL}:commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW"
PHONE_TRACK = "internal"
WEAR_TRACK = "wear:internal"
PHONE = 100011
WEAR = 200008
PHONE_PREDECESSOR = 100005
WEAR_PREDECESSOR = 200005
ALPHA = 100009
PHONE_BUNDLE = b"verified test phone bytes for 1.0.10"
WEAR_BUNDLE = b"verified test wear bytes for 1.0.7"
PHONE_DIGEST = hashlib.sha256(PHONE_BUNDLE).hexdigest()
WEAR_DIGEST = hashlib.sha256(WEAR_BUNDLE).hexdigest()
RELEASE_INPUTS = {
    "PRODUCT_SHA": "a" * 40,
    "PIPELINE_SHA": "b" * 40,
    "SIGNED_BUILD": "19",
    "SIGNED_SHA256": PHONE_DIGEST,
    "SIGNED_WEAR_SHA256": WEAR_DIGEST,
}
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
QUOTA_REASON = "listing_releases_quota_exceeded"
NO_CONTENT = {"result": "no_content", "http_status": 204}
TRACK_NAMES = ("alpha", "internal", "production", "wear:internal", "wear:production")


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


def bundle(code, digest="c" * 64):
    return {"versionCode": code, "sha256": digest}


def catalog():
    return [bundle(ALPHA), bundle(PHONE_PREDECESSOR), bundle(WEAR_PREDECESSOR)]


def track(name, code, **details):
    return {"track": name, "releases": [{
        "status": "completed", "versionCodes": [str(code)], **details,
    }]}


def observed(code, state=PUBLISHED):
    return {"lifecycle_state": state, "version_codes": [code]}


def verified_track(code):
    return {"result": "ok", "http_status": 200, "releases": [observed(code)]}


def live(name, code, state=PUBLISHED):
    return response({"releases": [{
        "track": name, "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}],
    }]})


def preflight(override=None, quota_tracks=(), age_minutes=5):
    tracks = {
        "alpha": verified_track(ALPHA),
        "internal": verified_track(PHONE_PREDECESSOR),
        "production": NO_CONTENT,
        "wear:internal": verified_track(WEAR_PREDECESSOR),
        "wear:production": NO_CONTENT,
    }
    for name in quota_tracks:
        tracks[name] = {"result": "forbidden", "http_status": 403, "quota": QUOTA_REASON}
    if override:
        tracks.update(override)
    return {
        "package_name": PACKAGE,
        "result": "quota_limited" if quota_tracks else "checked",
        "observed_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "oauth": {"result": "ok", "http_status": 200},
        "tracks": tracks,
    }


def expected_inventory():
    return [track("alpha", ALPHA), track(PHONE_TRACK, PHONE_PREDECESSOR), track(WEAR_TRACK, WEAR_PREDECESSOR)]


def edit_snapshot(bundles=None, tracks=None):
    return [
        response({"id": EDIT_ID}),
        response({"bundles": catalog() if bundles is None else bundles}),
        response({"tracks": expected_inventory() if tracks is None else tracks}),
    ]


def staged(name, code):
    return response(track(name, code, releaseNotes=list(reversed(publish.RELEASE_NOTES[name]))))


def completed_precommit():
    return [
        *edit_snapshot(),
        response(bundle(PHONE, PHONE_DIGEST)), response(bundle(WEAR, WEAR_DIGEST)),
        staged(PHONE_TRACK, PHONE), staged(WEAR_TRACK, WEAR),
    ]


class PlayPublishTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        opener_patch = patch.object(urllib.request, "build_opener", return_value=self.opener)
        loader_patch = patch.object(
            publish, "load_release_bundles", return_value={"phone": PHONE_BUNDLE, "wear": WEAR_BUNDLE},
        )
        oauth_patch = patch.object(publish, "get_access_token", return_value=("PRIVATE_TOKEN", 200))
        archive_patch = patch.object(publish, "read_archive", return_value=json.dumps(preflight()).encode())
        clock_patch = patch.object(publish, "datetime", wraps=datetime)
        opener_patch.start()
        self.loader = loader_patch.start()
        self.oauth = oauth_patch.start()
        self.archive = archive_patch.start()
        clock_patch.start().now.return_value = NOW
        for patcher in (opener_patch, loader_patch, oauth_patch, archive_patch, clock_patch):
            self.addCleanup(patcher.stop)

    def run_with(self, *responses, archive=None, release_inputs=RELEASE_INPUTS):
        if archive is not None:
            self.archive.return_value = json.dumps(archive).encode()
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

    def test_both_internal_tracks_advance_in_one_edit_with_the_approved_bytes(self):
        report = self.run_with(
            *completed_precommit(), response({"id": EDIT_ID}), live(PHONE_TRACK, PHONE), live(WEAR_TRACK, WEAR),
        )

        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["version_names"], {"phone": "1.0.10", "wear": "1.0.7"})
        self.assertEqual(report["build_number"], 19)
        self.assertEqual(report["candidates"], {
            PHONE_TRACK: {"version_code": PHONE, "sha256": PHONE_DIGEST},
            WEAR_TRACK: {"version_code": WEAR, "sha256": WEAR_DIGEST},
        })
        self.loader.assert_called_once_with(
            Path("synthetic-archive"), RELEASE_INPUTS["PRODUCT_SHA"], RELEASE_INPUTS["PIPELINE_SHA"],
            {"phone": PHONE_DIGEST, "wear": WEAR_DIGEST},
        )
        self.archive.assert_called_once_with(Path("synthetic-archive").resolve(), "alpha-status.json")
        self.assertEqual(report["lifecycle_validation"], {
            "result": "signed_preflight", "archive_build": 19,
            "archive_sha256": hashlib.sha256(self.archive.return_value).hexdigest(),
            "observed_at": preflight()["observed_at"], "fresh_inventory": "verified",
        })
        self.assertEqual(report["bundle_catalog"]["predecessors"], {
            PHONE_TRACK: {"version_code": PHONE_PREDECESSOR, "result": "present"},
            WEAR_TRACK: {"version_code": WEAR_PREDECESSOR, "result": "present"},
        })
        for name in (PHONE_TRACK, WEAR_TRACK):
            self.assertEqual(report["uploads"][name]["result"], "uploaded")
            self.assertEqual(report["track_updates"][name]["matched_note_locales"], ["en-US", "zh-TW"])
        self.assertEqual(report["review_observation"]["result"], "observed_submitted")
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks"),
            ("POST", UPLOAD_URL),
            ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/internal"),
            ("PUT", f"{EDIT_URL}/tracks/wear:internal"),
            ("POST", COMMIT_URL),
            ("GET", f"{APP_URL}/tracks/internal/releases"),
            ("GET", f"{APP_URL}/tracks/wear:internal/releases"),
        ])
        requests = self.requests()
        self.assertEqual([requests[3].data, requests[4].data], [PHONE_BUNDLE, WEAR_BUNDLE])
        self.assertEqual(requests[3].get_header("Content-type"), "application/octet-stream")
        self.assertEqual(json.loads(requests[5].data), {
            "track": "internal",
            "releases": [{"versionCodes": ["100011"], "status": "completed", "releaseNotes": publish.PHONE_NOTES}],
        })
        self.assertEqual(json.loads(requests[6].data), {
            "track": "wear:internal",
            "releases": [{"versionCodes": ["200008"], "status": "completed", "releaseNotes": publish.WEAR_NOTES}],
        })
        self.assertIsNone(requests[7].data)
        self.assertNotIn(("PUT", f"{EDIT_URL}/tracks/alpha"), self.operations())

    def test_release_notes_fit_play_limits(self):
        for notes in (publish.PHONE_NOTES, publish.WEAR_NOTES):
            self.assertEqual([note["language"] for note in notes], ["en-US", "zh-TW"])
            for note in notes:
                self.assertLessEqual(len(note["text"]), 500)

    def test_any_quota_limited_track_stops_before_oauth(self):
        for quota_track in TRACK_NAMES:
            with self.subTest(track=quota_track):
                report = self.run_with(archive=preflight(quota_tracks=(quota_track,)))
                self.assertEqual(report["result"], "blocked")
                self.assertEqual(report["failure"], "preflight_quota_unverified")
                self.assertEqual(report["blocking_track"], quota_track)
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_unexpected_lifecycle_observations_stop_before_oauth(self):
        cases = [
            ("alpha", verified_track(100008)),
            ("alpha", {"result": "pending_release", "http_status": 200, "releases": [observed(ALPHA, IN_REVIEW)]}),
            ("alpha", NO_CONTENT),
            ("internal", verified_track(PHONE)),
            ("internal", {"result": "pending_release", "http_status": 200, "releases": [observed(100006, NOT_SENT)]}),
            ("wear:internal", verified_track(200006)),
            ("wear:internal", {"result": "forbidden", "http_status": 403}),
            ("production", verified_track(100003)),
            ("wear:production", verified_track(200003)),
        ]
        for name, observation in cases:
            with self.subTest(track=name, observation=observation):
                report = self.run_with(archive=preflight({name: observation}))
                self.assertEqual(report["failure"], "preflight_track_unverified")
                self.assertEqual(report["blocking_track"], name)
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_stale_future_inconsistent_or_wrong_scope_preflight_stops_before_oauth(self):
        cases = [
            (preflight(age_minutes=30.01), "preflight_outside_age_limit"),
            (preflight(age_minutes=-1), "preflight_outside_age_limit"),
            ({**preflight(), "observed_at": "2026-11-24T09:59:00"}, "preflight_timestamp_invalid"),
            ({**preflight(), "package_name": "PRIVATE_PACKAGE"}, "preflight_archive_unverified"),
            ({**preflight(), "result": "quota_limited"}, "preflight_archive_unverified"),
        ]
        for archive, failure in cases:
            with self.subTest(failure=failure):
                report = self.run_with(archive=archive)
                self.assertEqual(report["failure"], failure)
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_no_write_without_exact_full_edit_inventory(self):
        good = expected_inventory()
        cases = [
            (good[1:], "fresh_inventory_mismatch"),
            ([track("alpha", 100010), *good[1:]], "fresh_inventory_mismatch"),
            ([good[0], track(PHONE_TRACK, PHONE), good[2]], "fresh_inventory_mismatch"),
            ([*good[:2], track(WEAR_TRACK, WEAR_PREDECESSOR, userFraction=0.5)], "fresh_inventory_mismatch"),
            ([*good, track("production", 100009)], "fresh_inventory_mismatch"),
            ([*good, track("PRIVATE_UNRELATED", 100004, status="draft")], "unrelated_pending_release"),
        ]
        for tracks, failure in cases:
            with self.subTest(failure=failure):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(tracks=tracks), response(status=204))
                self.assertEqual(report["failure"], failure)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_inventory_mismatch_reports_standard_tracks_and_counts_custom_ones(self):
        tracks = [
            track("alpha", ALPHA, countryTargeting={"countries": ["US"]}), *expected_inventory()[1:],
            track("wear:alpha", 200006), track("PRIVATE_CUSTOM", 100004),
        ]
        report = self.run_with(*edit_snapshot(tracks=tracks), response(status=204))
        self.assertEqual(report["failure"], "fresh_inventory_mismatch")
        self.assertEqual(report["track_inventory"]["observed_active"], {
            "alpha": {"releases": [{"status": "completed", "version_codes": [ALPHA]}], "custom_targeting": True},
            "internal": {"releases": [{"status": "completed", "version_codes": [PHONE_PREDECESSOR]}], "custom_targeting": False},
            "wear:alpha": {"releases": [{"status": "completed", "version_codes": [200006]}], "custom_targeting": False},
            "wear:internal": {"releases": [{"status": "completed", "version_codes": [WEAR_PREDECESSOR]}], "custom_targeting": False},
        })
        self.assertEqual(report["track_inventory"]["other_active_track_count"], 1)

    def test_missing_predecessor_or_newer_code_never_uploads(self):
        cases = [
            ([bundle(ALPHA), bundle(WEAR_PREDECESSOR)], "predecessor_missing", PHONE_TRACK),
            ([bundle(ALPHA), bundle(PHONE_PREDECESSOR)], "predecessor_missing", WEAR_TRACK),
            ([*catalog(), bundle(PHONE + 1)], "candidate_conflict", None),
            ([*catalog(), bundle(WEAR + 1)], "candidate_conflict", None),
            ([*catalog(), bundle(300001)], "candidate_conflict", None),
            ([*catalog(), bundle(PHONE, "a" * 64)], "candidate_conflict", None),
        ]
        for bundles, failure, blocking in cases:
            with self.subTest(failure=failure, bundles=bundles):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(bundles=bundles), response(status=204))
                self.assertEqual(report["failure"], failure)
                if blocking:
                    self.assertEqual(report["blocking_track"], blocking)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_archive_verification_happens_before_oauth(self):
        for field in ("SIGNED_SHA256", "SIGNED_WEAR_SHA256", "SIGNED_BUILD"):
            with self.subTest(field=field):
                report = self.run_with(release_inputs={**RELEASE_INPUTS, field: "PRIVATE_VALUE"})
                self.assertEqual(report["artifact_verification"]["result"], "invalid_release_parameters")
        self.loader.assert_not_called()

        self.loader.return_value = {"phone": PHONE_BUNDLE, "wear": b"wrong signed bytes"}
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "hash_mismatch")
        self.archive.assert_not_called()

        self.loader.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "failed")
        self.archive.assert_not_called()

        self.loader.side_effect = None
        self.loader.return_value = {"phone": PHONE_BUNDLE, "wear": WEAR_BUNDLE}
        self.archive.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["failure"], "preflight_archive_unverified")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_existing_matching_candidate_upload_is_reused(self):
        report = self.run_with(
            *edit_snapshot(bundles=[*catalog(), bundle(PHONE, PHONE_DIGEST)]),
            response(bundle(WEAR, WEAR_DIGEST)), staged(PHONE_TRACK, PHONE), staged(WEAR_TRACK, WEAR),
            response({"id": EDIT_ID}), live(PHONE_TRACK, PHONE), live(WEAR_TRACK, WEAR),
        )
        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["uploads"][PHONE_TRACK]["result"], "reused")
        self.assertEqual(report["uploads"][WEAR_TRACK]["result"], "uploaded")
        self.assertEqual(self.operations().count(("POST", UPLOAD_URL)), 1)
        self.assertEqual(self.requests()[3].data, WEAR_BUNDLE)

    def test_wrong_upload_receipt_stops_before_any_track_update(self):
        cases = [
            [response(bundle(PHONE, "a" * 64))],
            [response(bundle(PHONE, PHONE_DIGEST)), response(bundle(WEAR, PHONE_DIGEST))],
            [response(bundle(PHONE, PHONE_DIGEST)), response(bundle(WEAR_PREDECESSOR, WEAR_DIGEST))],
        ]
        for receipts in cases:
            with self.subTest(receipts=len(receipts)):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(), *receipts, response(status=204))
                failed = PHONE_TRACK if len(receipts) == 1 else WEAR_TRACK
                self.assertEqual(report["uploads"][failed]["result"], "bundle_receipt_mismatch")
                self.assertNotIn("PUT", [method for method, _ in self.operations()])
                self.assertEqual(self.mutations()[-2:], [("POST", UPLOAD_URL), ("DELETE", EDIT_URL)])

    def test_staged_wrong_notes_do_not_commit(self):
        report = self.run_with(
            *edit_snapshot(), response(bundle(PHONE, PHONE_DIGEST)), response(bundle(WEAR, WEAR_DIGEST)),
            staged(PHONE_TRACK, PHONE),
            response(track(WEAR_TRACK, WEAR, releaseNotes=publish.PHONE_NOTES)),
            response(status=204),
        )
        self.assertEqual(report["track_updates"][WEAR_TRACK]["result"], "track_receipt_notes_mismatch")
        self.assertEqual(self.mutations()[-2:], [("PUT", f"{EDIT_URL}/tracks/wear:internal"), ("DELETE", EDIT_URL)])
        self.assertNotIn(("POST", COMMIT_URL), self.operations())

    def test_hard_prerequisite_and_existing_review_have_one_cleanup(self):
        cases = [
            (400, "Only releases with status draft may be created on draft app. PRIVATE_TEXT", None),
            (400, "PRIVATE_TEXT", "CHANGES_ALREADY_IN_REVIEW"),
            (403, "PRIVATE_TEXT", None),
        ]
        for status, message, reason in cases:
            with self.subTest(status=status, reason=reason):
                self.opener.open.reset_mock()
                report = self.run_with(*completed_precommit(), http_error(status, message, reason), response({}))
                self.assertEqual(report["result"], "rejected")
                self.assertEqual(report["commit"]["http_status"], status)
                if reason:
                    self.assertEqual(report["commit"]["google_reasons"], [reason])
                elif status == 400:
                    self.assertEqual(report["commit"]["prerequisite"], "draft_app")
                self.assertEqual(self.operations()[-2:], [("POST", COMMIT_URL), ("DELETE", EDIT_URL)])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)

    def test_ambiguous_commit_only_reconciles_both_exact_codes(self):
        cases = [
            (TimeoutError("PRIVATE_TIMEOUT"), [live(PHONE_TRACK, PHONE), live(WEAR_TRACK, WEAR)], "submission_observed"),
            (TimeoutError("PRIVATE_TIMEOUT"), [live(PHONE_TRACK, PHONE), live(WEAR_TRACK, WEAR_PREDECESSOR)],
             "commit_indeterminate"),
            (response({"id": "PRIVATE_WRONG_EDIT"}), [live(PHONE_TRACK, PHONE, NOT_SENT), live(WEAR_TRACK, WEAR)],
             "commit_indeterminate"),
            (http_error(503), [http_error(403, "Listing releases quota exceeded"), live(WEAR_TRACK, WEAR)],
             "commit_indeterminate"),
        ]
        for problem, observations, result in cases:
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                report = self.run_with(*completed_precommit(), problem, *observations)
                self.assertEqual(report["result"], result)
                self.assertEqual(report["cleanup"]["result"], "not_attempted")
                self.assertEqual(self.operations()[-3:], [
                    ("POST", COMMIT_URL),
                    ("GET", f"{APP_URL}/tracks/internal/releases"),
                    ("GET", f"{APP_URL}/tracks/wear:internal/releases"),
                ])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)

    def test_an_unsent_target_is_reported_after_an_acknowledged_commit(self):
        report = self.run_with(
            *completed_precommit(), response({"id": EDIT_ID}),
            live(PHONE_TRACK, PHONE, NOT_SENT), live(WEAR_TRACK, WEAR),
        )
        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["review_observation"]["result"], "observed_not_sent_for_review")


if __name__ == "__main__":
    unittest.main()
