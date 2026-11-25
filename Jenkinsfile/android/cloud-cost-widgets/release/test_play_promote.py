import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import play_alpha_status as alpha_status
import play_promote as promote


PACKAGE = "com.maksonlee.cloudcost.aws"
APP_URL = f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{PACKAGE}"
EDIT_ID = "PRIVATE_EDIT"
EDIT_URL = f"{APP_URL}/edits/{EDIT_ID}"
COMMIT_URL = f"{EDIT_URL}:commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW&changesNotSentForReview=true"
PHONE = 100013
WEAR = 200012
ALPHA = 100009
PHONE_BETA = 100005
WEAR_BETA = 200005
PHONE_DIGEST = "0b5789a3b1757043ca61825f4a4d0ed2e6c62c99fdc606ad7d13361d133a0aab"
WEAR_DIGEST = "e26d84c29443f01596acf2a63867311e6e259f245db15739a52e0f2cfde58cd3"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
QUOTA_REASON = "listing_releases_quota_exceeded"


def response(value=None, status=200):
    stream = io.BytesIO(b"" if status == 204 else json.dumps(value).encode())
    stream.status = status
    return stream


def http_error(status, message="", reason=None):
    document = {"error": {
        "status": "FAILED_PRECONDITION", "message": message,
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}],
    }}
    return urllib.error.HTTPError(
        "https://example.invalid/PRIVATE_URL", status, "PRIVATE_ERROR", {}, io.BytesIO(json.dumps(document).encode()),
    )


def bundle(code, digest="c" * 64):
    return {"versionCode": code, "sha256": digest}


def catalog():
    return [bundle(PHONE_BETA), bundle(ALPHA), bundle(PHONE, PHONE_DIGEST), bundle(WEAR_BETA), bundle(WEAR, WEAR_DIGEST)]


def track(name, code, **details):
    return {"track": name, "releases": [{"status": "completed", "versionCodes": [str(code)], **details}]}


def inventory():
    return [
        track("alpha", ALPHA), track("internal", PHONE), track("wear:internal", WEAR),
        track("beta", PHONE_BETA), track("wear:beta", WEAR_BETA), {"track": "production"},
    ]


def published(code):
    return {"result": "ok", "http_status": 200, "releases": [{"lifecycle_state": PUBLISHED, "version_codes": [code]}]}


def status_archive(override=None, quota_tracks=(), age_minutes=5):
    tracks = {"alpha": published(ALPHA), "internal": published(PHONE), "wear:internal": published(WEAR)}
    for name in quota_tracks:
        tracks[name] = {"result": "forbidden", "http_status": 403, "google_status": "PERMISSION_DENIED", "quota": QUOTA_REASON}
    tracks.update(override or {})
    return {
        "package_name": PACKAGE,
        "result": "quota_limited" if quota_tracks else "checked",
        "observed_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "oauth": {"result": "ok", "http_status": 200},
        "tracks": tracks,
    }


def live(name, code, state):
    return response({"releases": [{"track": name, "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}]}]})


def staged(name, code):
    return response(track(name, code, releaseNotes=list(reversed(promote.RELEASE_NOTES[name]))))


def inspected(bundles=None, tracks=None):
    return [
        response({"id": EDIT_ID}),
        response({"bundles": catalog() if bundles is None else bundles}),
        response({"tracks": inventory() if tracks is None else tracks}),
    ]


def precommit():
    return [*inspected(), staged("production", PHONE), staged("wear:production", WEAR)]


class PlayPromoteTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        patches = [
            patch.object(urllib.request, "build_opener", return_value=self.opener),
            patch.object(promote, "get_access_token", return_value=("PRIVATE_TOKEN", 200)),
            patch.object(promote, "datetime", wraps=datetime),
        ]
        started = [patcher.start() for patcher in patches]
        self.oauth = started[1]
        started[2].now.return_value = NOW
        for patcher in patches:
            self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.status_path = Path(directory.name) / "alpha-status.json"

    def run_with(self, mode, *responses, archive=None):
        self.status_path.write_text(json.dumps(status_archive() if archive is None else archive), encoding="utf-8")
        self.opener.open.reset_mock()
        self.opener.open.side_effect = responses
        report = promote.run(mode, self.status_path, "PRIVATE_VAULT_VALUE")
        self.assertNotIn("PRIVATE", json.dumps(report))
        return report

    def operations(self):
        return [(call.args[0].get_method(), call.args[0].full_url) for call in self.opener.open.call_args_list]

    def mutations(self):
        return [operation for operation in self.operations() if operation[0] != "GET"]

    def test_the_promotion_targets_both_production_tracks_with_the_internal_bundles(self):
        self.assertEqual(promote.TRACKS, {"production": PHONE, "wear:production": WEAR})
        self.assertEqual(promote.BUNDLE_SHA256, {PHONE: PHONE_DIGEST, WEAR: WEAR_DIGEST})
        self.assertEqual(promote.EXPECTED_ACTIVE, {
            "alpha": ALPHA, "internal": PHONE, "wear:internal": WEAR, "beta": PHONE_BETA, "wear:beta": WEAR_BETA,
        })
        for target, source in promote.SOURCE_TRACKS.items():
            self.assertEqual(promote.EXPECTED_ACTIVE[source], promote.TRACKS[target])
        self.assertTrue(set(alpha_status.TRACK_NAMES) <= set(promote.EXPECTED_ACTIVE))
        self.assertFalse(set(promote.TRACKS) & set(promote.EXPECTED_ACTIVE))

    def test_release_notes_fit_play_limits(self):
        for name, notes in promote.RELEASE_NOTES.items():
            with self.subTest(track=name):
                self.assertEqual([note["language"] for note in notes], ["en-US", "zh-TW"])
                for note in notes:
                    self.assertLessEqual(len(note["text"]), 500)
                    self.assertNotIn("*", note["text"])

    def test_approval_names_every_version_digest_build_and_track(self):
        text = promote.approval_text()
        for fact in (
            PACKAGE, "phone 1.0.12 (100013) to Production,", "Wear OS 1.0.11 (200012) to Production (Wear OS)",
            PHONE_DIGEST, WEAR_DIGEST, "#14", "#15", "#16", "#17", promote.SOURCE_SHA,
        ):
            self.assertIn(fact, text)

    def test_check_proves_the_edit_and_deletes_it(self):
        report = self.run_with("check", *inspected(), response(status=204))
        self.assertEqual(report["result"], "checked")
        self.assertEqual(report["lifecycle_validation"]["result"], "published_on_internal")
        self.assertEqual(report["bundle_catalog"]["result"], "ok")
        self.assertEqual(report["track_inventory"]["result"], "ok")
        self.assertEqual(report["cleanup"]["result"], "deleted")
        self.assertEqual(self.operations(), [
            ("POST", f"{APP_URL}/edits"), ("GET", f"{EDIT_URL}/bundles"), ("GET", f"{EDIT_URL}/tracks"),
            ("DELETE", EDIT_URL),
        ])

    def test_submit_stages_both_tracks_and_commits_them_unsent(self):
        report = self.run_with(
            "submit", *precommit(), response({"id": EDIT_ID}),
            live("production", PHONE, NOT_SENT), live("wear:production", WEAR, NOT_SENT),
        )
        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["review_observation"]["result"], "observed_not_sent_for_review")
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("POST", f"{APP_URL}/edits"), ("GET", f"{EDIT_URL}/bundles"), ("GET", f"{EDIT_URL}/tracks"),
            ("PUT", f"{EDIT_URL}/tracks/production"), ("PUT", f"{EDIT_URL}/tracks/wear:production"),
            ("POST", COMMIT_URL),
            ("GET", f"{APP_URL}/tracks/production/releases"), ("GET", f"{APP_URL}/tracks/wear:production/releases"),
        ])
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(json.loads(requests[3].data), {"track": "production", "releases": [{
            "status": "completed", "versionCodes": ["100013"], "releaseNotes": promote.PHONE_NOTES,
        }]})
        self.assertEqual(json.loads(requests[4].data), {"track": "wear:production", "releases": [{
            "status": "completed", "versionCodes": ["200012"], "releaseNotes": promote.WEAR_NOTES,
        }]})
        # Nothing is uploaded and no testing track is written.
        self.assertFalse(any("/upload/" in url for _, url in self.operations()))
        for name in ("internal", "wear:internal", "alpha", "beta", "wear:beta"):
            self.assertNotIn(("PUT", f"{EDIT_URL}/tracks/{name}"), self.operations())

    def test_a_quota_limited_status_read_falls_back_to_the_exact_edit_inventory(self):
        report = self.run_with(
            "check", *inspected(), response(status=204), archive=status_archive(quota_tracks=("internal", "alpha")),
        )
        self.assertEqual(report["result"], "checked")
        self.assertEqual(report["lifecycle_validation"]["result"], "quota_fallback")
        self.assertEqual(report["lifecycle_validation"]["quota_tracks"], ["alpha", "internal"])

    def test_an_unexpected_status_read_stops_before_oauth(self):
        cases = [
            (status_archive({"internal": published(100012)}), "status_track_unverified"),
            (status_archive({"wear:internal": published(200011)}), "status_track_unverified"),
            (status_archive({"internal": {"result": "pending_release", "http_status": 200, "releases": [
                {"lifecycle_state": IN_REVIEW, "version_codes": [PHONE]}]}}), "status_track_unverified"),
            (status_archive({"alpha": {"result": "forbidden", "http_status": 403}}), "status_track_unverified"),
            (status_archive(age_minutes=31), "status_outside_age_limit"),
            (status_archive(age_minutes=-1), "status_outside_age_limit"),
            ({**status_archive(), "observed_at": "2026-11-26T02:55:00"}, "status_timestamp_invalid"),
            ({**status_archive(), "result": "quota_limited"}, "status_archive_unverified"),
            ({**status_archive(), "package_name": "PRIVATE_PACKAGE"}, "status_archive_unverified"),
            ({**status_archive(), "tracks": {**status_archive()["tracks"], "production": published(PHONE)}},
             "status_archive_unverified"),
        ]
        for index, (archive, failure) in enumerate(cases):
            with self.subTest(case=index, failure=failure):
                report = self.run_with("submit", archive=archive)
                self.assertEqual(report["failure"], failure)
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_a_missing_or_different_bundle_never_writes(self):
        good = catalog()
        cases = [
            ([entry for entry in good if entry["versionCode"] != WEAR], "candidate_missing"),
            ([entry for entry in good if entry["versionCode"] != PHONE], "candidate_missing"),
            ([*good[:2], bundle(PHONE, "a" * 64), *good[3:]], "candidate_conflict"),
            ([*good[:4], bundle(WEAR, None)], "candidate_conflict"),
            ([*good, bundle(PHONE + 1)], "candidate_conflict"),
            ([*good, bundle(WEAR + 1)], "candidate_conflict"),
            ([*good, bundle(300001)], "candidate_conflict"),
        ]
        for index, (bundles, failure) in enumerate(cases):
            with self.subTest(case=index, failure=failure):
                report = self.run_with("submit", *inspected(bundles=bundles), response(status=204))
                self.assertEqual(report["failure"], failure)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])

    def test_no_write_without_the_exact_track_inventory(self):
        good = inventory()
        cases = [
            ([*good[:-1], track("production", 100009)], "inventory_mismatch"),
            ([*good, track("wear:production", 200011)], "inventory_mismatch"),
            ([*good[:-1], track("production", PHONE, status="draft")], "pending_release"),
            ([track("alpha", ALPHA), *good[2:]], "inventory_mismatch"),
            ([track("alpha", ALPHA), track("internal", 100012), *good[2:]], "inventory_mismatch"),
            ([*good[:2], track("wear:internal", WEAR, userFraction=0.5), *good[3:]], "inventory_mismatch"),
            ([*good, track("PRIVATE_CUSTOM", 100004)], "inventory_mismatch"),
        ]
        for index, (tracks, failure) in enumerate(cases):
            with self.subTest(case=index, failure=failure):
                report = self.run_with("submit", *inspected(tracks=tracks), response(status=204))
                self.assertEqual(report["failure"], failure)
                self.assertEqual(self.mutations(), [("POST", f"{APP_URL}/edits"), ("DELETE", EDIT_URL)])
                self.assertNotIn("PRIVATE", json.dumps(report))

    def test_wrong_staged_notes_do_not_commit(self):
        other = [{"language": note["language"], "text": "Other notes."} for note in promote.PHONE_NOTES]
        report = self.run_with(
            "submit", *inspected(), response(track("production", PHONE, releaseNotes=other)), response(status=204),
        )
        self.assertEqual(report["track_updates"]["production"]["result"], "track_receipt_notes_mismatch")
        self.assertEqual(self.mutations()[-2:], [("PUT", f"{EDIT_URL}/tracks/production"), ("DELETE", EDIT_URL)])
        self.assertNotIn(("POST", COMMIT_URL), self.operations())

    def test_a_rejected_commit_is_cleaned_up_once(self):
        report = self.run_with("submit", *precommit(), http_error(400, "PRIVATE_TEXT", "CHANGES_ALREADY_IN_REVIEW"), response({}))
        self.assertEqual(report["result"], "rejected")
        self.assertEqual(report["commit"]["google_reasons"], ["CHANGES_ALREADY_IN_REVIEW"])
        self.assertEqual(self.operations()[-2:], [("POST", COMMIT_URL), ("DELETE", EDIT_URL)])

    def test_an_ambiguous_commit_is_reconciled_by_reading_the_production_tracks(self):
        cases = [
            ([live("production", PHONE, NOT_SENT), live("wear:production", WEAR, NOT_SENT)], "commit_observed"),
            ([live("production", PHONE, NOT_SENT), response(status=204)], "commit_indeterminate"),
            ([http_error(403, "Listing releases quota exceeded"), live("wear:production", WEAR, NOT_SENT)],
             "commit_indeterminate"),
        ]
        for index, (observations, result) in enumerate(cases):
            with self.subTest(case=index, result=result):
                report = self.run_with("submit", *precommit(), TimeoutError("PRIVATE_TIMEOUT"), *observations)
                self.assertEqual(report["result"], result)
                self.assertEqual(report["cleanup"]["result"], "not_attempted")
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)

    def test_main_writes_the_approval_only_after_a_passed_check(self):
        directory = Path(self.status_path).parent
        self.status_path.write_text(json.dumps(status_archive()), encoding="utf-8")
        self.opener.open.side_effect = [*inspected(), response(status=204)]
        with patch.dict("os.environ", {"PLAY_SERVICE_JSON_B64": "PRIVATE_VAULT_VALUE"}):
            code = promote.main(["play_promote.py", "check", str(self.status_path), str(directory / "report.json"),
                                 str(directory / "approval.txt")])
        self.assertEqual(code, 0)
        self.assertEqual((directory / "approval.txt").read_text(encoding="utf-8").strip(), promote.approval_text())

        self.status_path.write_text(json.dumps(status_archive(age_minutes=40)), encoding="utf-8")
        (directory / "approval.txt").unlink()
        with patch.dict("os.environ", {"PLAY_SERVICE_JSON_B64": "PRIVATE_VAULT_VALUE"}):
            code = promote.main(["play_promote.py", "check", str(self.status_path), str(directory / "report.json"),
                                 str(directory / "approval.txt")])
        self.assertEqual(code, 1)
        self.assertFalse((directory / "approval.txt").exists())
        self.assertEqual(promote.main(["play_promote.py", "submit", "a", "b", "c"]), 2)


if __name__ == "__main__":
    unittest.main()
