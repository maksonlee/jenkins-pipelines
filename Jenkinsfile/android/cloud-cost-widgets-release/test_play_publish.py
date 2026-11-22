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
VALIDATE_URL = f"{EDIT_URL}:validate"
COMMIT_URL = f"{EDIT_URL}:commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW"
PHONE = 100006
WEAR = 200006
ARTIFACTS = {PHONE: b"verified test phone bytes", WEAR: b"verified test wear bytes"}
IN_REVIEW = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"


def response(value=None, status=200):
    stream = io.BytesIO(b"" if status == 204 else json.dumps(value).encode())
    stream.status = status
    return stream


def http_error(status, message="", reason=None):
    document = {"error": {
        "status": "FAILED_PRECONDITION",
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


def staged(track, code):
    return response({"track": track, "releases": [{
        "status": "completed", "versionCodes": [str(code)],
    }]})


def live(track, code, state=IN_REVIEW):
    return response({"releases": [{
        "track": track, "releaseLifecycleState": state, "activeArtifacts": [{"versionCode": code}],
    }]})


def edit_snapshot(catalog=None):
    return [
        response(status=204),
        response({"releases": []}),
        response({"id": EDIT_ID}),
        response({"bundles": [] if catalog is None else catalog}),
        response({"track": "production"}),
        response({"track": "wear:production"}),
    ]


def completed_precommit():
    return [
        *edit_snapshot(), response(bundle(PHONE)), response(bundle(WEAR)),
        staged("production", PHONE), staged("wear:production", WEAR), response({"id": EDIT_ID}),
    ]


class PlayPublishTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock(spec=urllib.request.OpenerDirector)
        patcher = patch.object(urllib.request, "build_opener", return_value=self.opener)
        loader_patch = patch.object(publish, "load_bundles", return_value=ARTIFACTS)
        oauth_patch = patch.object(publish, "get_access_token", return_value=("PRIVATE_TOKEN", 200))
        patcher.start()
        self.loader = loader_patch.start()
        self.oauth = oauth_patch.start()
        self.addCleanup(patcher.stop)
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

    def test_verified_candidate_has_only_exact_production_writes(self):
        report = self.run_with(
            *completed_precommit(), response({"id": EDIT_ID}),
            live("production", PHONE), response(status=204),
        )

        self.assertEqual(report["result"], "committed")
        self.assertEqual(report["review_observation"]["result"], "unconfirmed")
        self.assertEqual(
            report["review_observation"]["tracks"]["production"]["lifecycle_states"], [IN_REVIEW],
        )
        self.assertEqual(report["cleanup"]["result"], "not_attempted")
        self.assertEqual(self.operations(), [
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
            ("POST", f"{APP_URL}/edits"),
            ("GET", f"{EDIT_URL}/bundles"),
            ("GET", f"{EDIT_URL}/tracks/production"),
            ("GET", f"{EDIT_URL}/tracks/wear:production"),
            ("POST", UPLOAD_URL), ("POST", UPLOAD_URL),
            ("PUT", f"{EDIT_URL}/tracks/production"),
            ("PUT", f"{EDIT_URL}/tracks/wear:production"),
            ("POST", VALIDATE_URL), ("POST", COMMIT_URL),
            ("GET", f"{APP_URL}/tracks/production/releases"),
            ("GET", f"{APP_URL}/tracks/wear:production/releases"),
        ])
        req = self.requests()
        self.assertEqual(req[6].data, ARTIFACTS[PHONE])
        self.assertEqual(req[7].data, ARTIFACTS[WEAR])
        self.assertEqual(req[6].get_header("Content-type"), "application/octet-stream")
        for request, track, code in zip(req[8:10], ("production", "wear:production"), (PHONE, WEAR)):
            self.assertEqual(json.loads(request.data), {
                "track": track,
                "releases": [{
                    "versionCodes": [str(code)], "status": "completed",
                    "releaseNotes": [{"language": "en-US", "text": (
                        "Phone budget details show AWS forecast spending alongside the budget "
                        "target when a forecast is available. Technical details start collapsed "
                        "and can be opened below Budget settings."
                    )}],
                }],
            })
        self.assertIsNone(req[10].data)
        self.assertIsNone(req[11].data)

    def test_invalid_archive_never_reaches_oauth(self):
        self.loader.side_effect = publish.ArtifactVerificationError("PRIVATE_PATH")
        report = self.run_with()
        self.assertEqual(report["artifact_verification"]["result"], "failed")
        self.oauth.assert_not_called()
        self.opener.open.assert_not_called()

    def test_pending_release_never_opens_edit(self):
        report = self.run_with(live("production", PHONE), response(status=204))
        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["edit_insert"]["result"], "not_attempted")
        self.assertTrue(all(method == "GET" for method, _ in self.operations()))

    def test_reuses_matching_upload_and_stops_on_wrong_wear_receipt(self):
        snapshot = edit_snapshot([bundle(PHONE)])
        report = self.run_with(*snapshot, response(bundle(PHONE)), response(status=204))
        self.assertEqual(report["uploads"]["production"]["result"], "reused")
        self.assertEqual(report["uploads"]["wear:production"]["result"], "bundle_receipt_mismatch")
        self.assertEqual(self.operations()[-2:], [("POST", UPLOAD_URL), ("DELETE", EDIT_URL)])
        self.assertEqual(self.requests()[-2].data, ARTIFACTS[WEAR])
        self.assertEqual(report["commit"]["result"], "not_attempted")

    def test_newer_bundle_blocks_candidate_without_writes_inside_edit(self):
        report = self.run_with(
            *edit_snapshot([{"versionCode": PHONE + 1, "sha256": "a" * 64}]),
            response(status=204),
        )
        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["bundle_catalog"]["newer_version_codes"]["production"], [PHONE + 1])
        self.assertEqual(self.operations()[-1], ("DELETE", EDIT_URL))
        self.assertNotIn(("POST", UPLOAD_URL), self.operations())

    def test_hard_prerequisite_and_existing_review_have_one_cleanup(self):
        cases = [
            ("Only releases with status draft may be created on draft app. PRIVATE_TEXT", None),
            ("PRIVATE_TEXT", "CHANGES_ALREADY_IN_REVIEW"),
        ]
        for message, reason in cases:
            with self.subTest(reason=reason):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *completed_precommit(), http_error(400, message, reason), response({}),
                )
                self.assertEqual(report["result"], "rejected")
                self.assertEqual(report["commit"]["http_status"], 400)
                if reason:
                    self.assertEqual(report["commit"]["google_reasons"], [reason])
                else:
                    self.assertEqual(report["commit"]["prerequisite"], "draft_app")
                self.assertEqual(self.operations()[-2:], [("POST", COMMIT_URL), ("DELETE", EDIT_URL)])

    def test_ambiguous_commit_only_reconciles_exact_target_versions(self):
        cases = [
            (TimeoutError("PRIVATE_TIMEOUT"), WEAR, "submission_observed"),
            (response({"id": "PRIVATE_WRONG_EDIT"}), PHONE, "commit_indeterminate"),
            (http_error(429), PHONE, "commit_indeterminate"),
            (http_error(503), PHONE, "commit_indeterminate"),
        ]
        for problem, wear_code, result in cases:
            with self.subTest(result=result):
                self.opener.open.reset_mock()
                report = self.run_with(
                    *completed_precommit(), problem, live("production", PHONE),
                    live("wear:production", wear_code),
                )
                self.assertEqual(report["result"], result)
                self.assertEqual(report["cleanup"]["result"], "not_attempted")
                self.assertEqual(self.operations()[-3:], [
                    ("POST", COMMIT_URL),
                    ("GET", f"{APP_URL}/tracks/production/releases"),
                    ("GET", f"{APP_URL}/tracks/wear:production/releases"),
                ])
                self.assertEqual(self.operations().count(("POST", COMMIT_URL)), 1)


if __name__ == "__main__":
    unittest.main()
