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
ALPHA_URL = f"{APP_URL}/tracks/alpha/releases"
PHONE = 100009
PREDECESSOR = 100008
PREDECESSOR_DIGEST = "32ed81f2f4d6e5ca32fed0818526758746dcbbdfb3fc49b8c72c983d61c40d59"
PHONE_BUNDLE = b"verified test phone bytes for 1.0.8"
SIGNED_DIGEST = hashlib.sha256(PHONE_BUNDLE).hexdigest()
RELEASE_INPUTS = {
    "PRODUCT_SHA": "a" * 40,
    "PIPELINE_SHA": "b" * 40,
    "SIGNED_BUILD": "17",
    "SIGNED_SHA256": SIGNED_DIGEST,
}
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
QUOTA_REASON = "listing_releases_quota_exceeded"
NO_CONTENT = {"result": "no_content", "http_status": 204}
TRACK_NAMES = ("alpha", "production", "wear:production", "internal", "wear:internal")
EXPECTED_NOTES = [
    {"language": "en-US", "text": (
        "Tap an unconfigured home-screen widget to choose a budget, or open the app’s Settings "
        "to configure it. Select one budget before saving."
    )},
    {"language": "zh-TW", "text": (
        "點一下尚未設定的桌面小工具即可選擇預算，也可以到 App 的「設定」重新設定原本的小工具。"
        "選定一筆預算後才能儲存。"
    )},
]


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
    return {"versionCode": code, "sha256": SIGNED_DIGEST if code == PHONE else PREDECESSOR_DIGEST}


def track(name, code, **details):
    return {"track": name, "releases": [{
        "status": "completed", "versionCodes": [str(code)], **details,
    }]}


def observed(code, state=PUBLISHED):
    return {"lifecycle_state": state, "version_codes": [code]}


def verified_track(code):
    return {"result": "ok", "http_status": 200, "releases": [observed(code)]}


def live_alpha(code, state=IN_REVIEW):
    return response({"releases": [{
        "track": "alpha", "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}],
    }]})


def preflight(override=None, quota_tracks=(), age_minutes=5):
    tracks = {
        "alpha": verified_track(PREDECESSOR),
        "production": NO_CONTENT,
        "wear:production": NO_CONTENT,
        "internal": verified_track(100005),
        "wear:internal": verified_track(200005),
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
    return [track("alpha", PREDECESSOR), track("internal", 100005), track("wear:internal", 200005)]


def edit_snapshot(catalog=None, tracks=None):
    return [
        response({"id": EDIT_ID}),
        response({"bundles": [bundle(PREDECESSOR)] if catalog is None else catalog}),
        response({"tracks": expected_inventory() if tracks is None else tracks}),
    ]


def staged_candidate():
    return response(track("alpha", PHONE, releaseNotes=list(reversed(EXPECTED_NOTES))))


def completed_precommit():
    return [*edit_snapshot(), response(bundle()), staged_candidate()]


class PlayPublishTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        opener_patch = patch.object(urllib.request, "build_opener", return_value=self.opener)
        loader_patch = patch.object(publish, "load_phone_bundle", return_value=PHONE_BUNDLE)
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

    def test_exact_phone_advance_uses_approved_bytes_and_reads_alpha_only_after_commit(self):
        report = self.run_with(
            *completed_precommit(), response({"id": EDIT_ID}), live_alpha(PHONE),
        )

        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["version_name"], "1.0.8")
        self.assertEqual(report["build_number"], 17)
        self.assertEqual(report["source_sha"], RELEASE_INPUTS["PRODUCT_SHA"])
        self.assertEqual(report["source_pipeline_sha"], RELEASE_INPUTS["PIPELINE_SHA"])
        self.loader.assert_called_once_with(
            Path("synthetic-archive"), RELEASE_INPUTS["PRODUCT_SHA"], RELEASE_INPUTS["PIPELINE_SHA"],
            SIGNED_DIGEST,
        )
        self.archive.assert_called_once_with(Path("synthetic-archive").resolve(), "alpha-status.json")
        self.assertEqual(report["lifecycle_validation"], {
            "result": "signed_preflight", "quota_tracks": [], "archive_build": 17,
            "archive_sha256": hashlib.sha256(self.archive.return_value).hexdigest(),
            "observed_at": preflight()["observed_at"], "fresh_inventory": "verified",
        })
        self.assertEqual(report["preflight_tracks"]["alpha"], verified_track(PREDECESSOR))
        self.assertEqual(report["bundle_catalog"]["predecessor"]["result"], "hash_matches")
        self.assertEqual(report["track_updates"]["alpha"]["matched_note_locales"], ["en-US", "zh-TW"])
        self.assertEqual(report["review_observation"]["result"], "observed_submitted")
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks"),
            ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/alpha"),
            ("POST", COMMIT_URL),
            ("GET", ALPHA_URL),
        ])
        requests = self.requests()
        self.assertEqual(requests[3].data, PHONE_BUNDLE)
        self.assertEqual(requests[3].get_header("Content-type"), "application/octet-stream")
        self.assertEqual(json.loads(requests[4].data), {
            "track": "alpha",
            "releases": [{"versionCodes": ["100009"], "status": "completed", "releaseNotes": EXPECTED_NOTES}],
        })
        self.assertIsNone(requests[5].data)

    def test_non_alpha_quota_archive_requires_current_inventory_and_attests_audited_history(self):
        quota_tracks = TRACK_NAMES[1:]
        report = self.run_with(
            *completed_precommit(), response({"id": EDIT_ID}),
            http_error(403, "Listing releases quota exceeded. PRIVATE_TEXT"),
            archive=preflight(quota_tracks=quota_tracks),
        )

        self.assertEqual(report["result"], "committed")
        validation = report["lifecycle_validation"]
        self.assertEqual(validation["result"], "quota_fallback")
        self.assertEqual(validation["archive_build"], 17)
        self.assertEqual(validation["fresh_inventory"], "verified")
        self.assertEqual(validation["quota_tracks"], list(quota_tracks))
        self.assertEqual(validation["historical_evidence_builds"], {track: 15 for track in quota_tracks})
        self.assertEqual(report["review_observation"]["result"], "unconfirmed")
        self.assertEqual(report["review_observation"]["tracks"]["alpha"]["quota"], QUOTA_REASON)
        self.assertEqual(self.mutations(), [
            ("POST", f"{APP_URL}/edits"), ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/alpha"), ("POST", COMMIT_URL),
        ])

    def test_alpha_quota_stops_before_oauth_even_with_other_tracks_attested(self):
        for quota_tracks in (("alpha",), TRACK_NAMES):
            with self.subTest(quota_tracks=quota_tracks):
                report = self.run_with(archive=preflight(quota_tracks=quota_tracks))
                self.assertEqual(report["result"], "blocked")
                self.assertEqual(report["failure"], "alpha_preflight_quota_unverified")
                self.assertEqual(report["blocking_track"], "alpha")
                self.assertEqual(report["preflight_tracks"]["alpha"], {
                    "result": "forbidden", "http_status": 403, "quota": QUOTA_REASON,
                })
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_exact_same_track_not_sent_predecessor_is_allowed(self):
        unsent = observed(PREDECESSOR, NOT_SENT)
        previous = observed(PREDECESSOR - 1)
        for releases in ([unsent], [unsent, previous], [previous, unsent]):
            with self.subTest(releases=releases):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *completed_precommit(), response({"id": EDIT_ID}), live_alpha(PHONE, NOT_SENT),
                    archive=preflight({
                        "alpha": {"result": "pending_release", "http_status": 200, "releases": releases},
                    }, quota_tracks=("wear:production",)),
                )
                self.assertEqual(report["result"], "committed")
                self.assertEqual(report["review_observation"]["result"], "observed_not_sent_for_review")
                self.assertEqual(report["lifecycle_validation"]["historical_evidence_builds"], {"wear:production": 15})
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)

    def test_nonquota_errors_and_conflicting_observations_stop_before_oauth(self):
        cases = [
            ("alpha", {"result": "forbidden", "http_status": 403}),
            ("alpha", {"result": "http_error", "http_status": 429, "quota": QUOTA_REASON}),
            ("alpha", NO_CONTENT),
            ("alpha", verified_track(PHONE)),
            ("alpha", {"result": "pending_release", "http_status": 200, "releases": [observed(PREDECESSOR, IN_REVIEW)]}),
            ("alpha", {"result": "pending_release", "http_status": 200, "releases": [
                observed(PREDECESSOR, IN_REVIEW), observed(PREDECESSOR - 1),
            ]}),
            ("alpha", {"result": "pending_release", "http_status": 200, "releases": [observed(PHONE, NOT_SENT)]}),
            ("wear:internal", {"result": "pending_release", "http_status": 200, "releases": [observed(200005, NOT_SENT)]}),
            ("wear:internal", verified_track(200006)),
            ("production", verified_track(100003)),
        ]
        for name, observation in cases:
            with self.subTest(track=name, observation=observation):
                report = self.run_with(archive=preflight({name: observation}, quota_tracks=("internal",)))
                self.assertEqual(report["failure"], "preflight_track_unverified")
                self.assertEqual(report["blocking_track"], name)
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_stale_future_inconsistent_or_wrong_scope_preflight_stops_before_oauth(self):
        cases = [
            (preflight(age_minutes=30.01), "preflight_outside_age_limit"),
            (preflight(age_minutes=-1), "preflight_outside_age_limit"),
            ({**preflight(), "observed_at": "2026-11-23T09:59:00"}, "preflight_timestamp_invalid"),
            ({**preflight(), "package_name": "PRIVATE_PACKAGE"}, "preflight_archive_unverified"),
            ({**preflight(quota_tracks=("internal",)), "result": "checked"}, "preflight_archive_unverified"),
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
            ([track("alpha", PHONE), *good[1:]], "fresh_inventory_mismatch"),
            ([track("alpha", PREDECESSOR, countryTargeting={"countries": ["US"]}), *good[1:]], "fresh_inventory_mismatch"),
            ([*good, track("PRIVATE_UNRELATED", 100004)], "fresh_inventory_mismatch"),
            ([*good, track("PRIVATE_UNRELATED", 100004, status="draft")], "unrelated_pending_release"),
            ([*good[:-1], track("wear:internal", 200006)], "fresh_inventory_mismatch"),
        ]
        for tracks, failure in cases:
            with self.subTest(failure=failure):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *edit_snapshot(tracks=tracks), response(status=204),
                    archive=preflight(quota_tracks=("wear:internal",)),
                )
                self.assertEqual(report["failure"], failure)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_missing_or_wrong_signed_predecessor_hash_never_uploads(self):
        cases = [
            ([], "predecessor_hash_unavailable"),
            ([{"versionCode": PREDECESSOR, "sha256": "a" * 64}], "predecessor_hash_conflict"),
            ([bundle(PREDECESSOR), {"versionCode": PHONE + 1, "sha256": "a" * 64}], "candidate_conflict"),
        ]
        for catalog, failure in cases:
            with self.subTest(failure=failure):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(catalog=catalog), response(status=204))
                self.assertEqual(report["failure"], failure)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_archive_verification_happens_before_oauth(self):
        report = self.run_with(release_inputs={**RELEASE_INPUTS, "SIGNED_SHA256": "PRIVATE_DIGEST"})
        self.assertEqual(report["artifact_verification"]["result"], "invalid_release_parameters")
        self.loader.assert_not_called()

        self.loader.return_value = b"wrong signed bytes"
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "hash_mismatch")
        self.archive.assert_not_called()

        self.loader.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "failed")
        self.archive.assert_not_called()

        self.loader.side_effect = None
        self.loader.return_value = PHONE_BUNDLE
        self.archive.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["failure"], "preflight_archive_unverified")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_existing_matching_candidate_upload_is_reused(self):
        report = self.run_with(
            *edit_snapshot(catalog=[bundle(PHONE), bundle(PREDECESSOR)]),
            staged_candidate(), response({"id": EDIT_ID}), response(status=204),
        )
        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["uploads"]["alpha"]["result"], "reused")
        self.assertNotIn(("POST", UPLOAD_URL), self.operations())

    def test_wrong_upload_digest_or_version_stops_before_track_update(self):
        for receipt in ({"versionCode": PHONE, "sha256": "a" * 64}, bundle(PREDECESSOR)):
            with self.subTest(receipt=receipt):
                self.opener.open.reset_mock()
                report = self.run_with(*edit_snapshot(), response(receipt), response(status=204))
                self.assertEqual(report["uploads"]["alpha"]["result"], "bundle_receipt_mismatch")
                self.assertEqual(self.mutations()[-2:], [("POST", UPLOAD_URL), ("DELETE", EDIT_URL)])

    def test_staged_wrong_notes_do_not_commit(self):
        report = self.run_with(
            *edit_snapshot(), response(bundle()),
            response(track("alpha", PHONE, releaseNotes=[{"language": "en-US", "text": "PRIVATE_WRONG"}])),
            response(status=204),
        )
        self.assertEqual(report["track_updates"]["alpha"]["result"], "track_receipt_notes_mismatch")
        self.assertEqual(self.mutations()[-2:], [("PUT", f"{EDIT_URL}/tracks/alpha"), ("DELETE", EDIT_URL)])

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

    def test_ambiguous_commit_only_reconciles_exact_phone_code(self):
        cases = [
            (TimeoutError("PRIVATE_TIMEOUT"), live_alpha(PHONE), "submission_observed"),
            (response({"id": "PRIVATE_WRONG_EDIT"}), live_alpha(PHONE, NOT_SENT), "commit_indeterminate"),
            (http_error(429), live_alpha(PREDECESSOR), "commit_indeterminate"),
            (http_error(503), http_error(403, "Listing releases quota exceeded"), "commit_indeterminate"),
        ]
        for problem, observation, result in cases:
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                report = self.run_with(*completed_precommit(), problem, observation)
                self.assertEqual(report["result"], result)
                self.assertEqual(report["cleanup"]["result"], "not_attempted")
                self.assertEqual(self.operations()[-2:], [("POST", COMMIT_URL), ("GET", ALPHA_URL)])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)


if __name__ == "__main__":
    unittest.main()
