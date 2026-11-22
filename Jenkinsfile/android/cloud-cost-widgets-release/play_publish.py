"""Submit only the reviewed 1.0.5 Android App Bundles to Google Play."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from play_preflight import (
    APPLICATION_URL, HTTP_TIMEOUT_SECONDS, PACKAGE_NAME, TRACKS, NoRedirect,
    PreflightError, get_access_token, request_json,
)
from play_submission_preflight import (
    BUNDLE_SHA256, SOURCE_SHA, edit_request, error_outcome,
    filter_google_error, outcome, read_edit_section, read_google_error, read_live_track,
    require_edit_id, summarize_bundles, summarize_edit_track,
)
from verify_submission_artifacts import ArtifactVerificationError, load_bundles


RELEASE_NOTES = (
    "Phone budget details show AWS forecast spending alongside the budget target when a "
    "forecast is available. Technical details start collapsed and can be opened below Budget settings."
)
UPLOAD_URL = (
    "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/"
    f"{PACKAGE_NAME}/edits"
)
COMMIT_QUERY = "changesInReviewBehavior=ERROR_IF_IN_REVIEW"
EDIT_ROOT = f"{APPLICATION_URL}/edits"
UPLOAD_TIMEOUT_SECONDS = 120
SUBMITTED_STATES = {
    "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
    "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED",
    "RELEASE_LIFECYCLE_STATE_PUBLISHED",
}
PREREQUISITE_PATTERNS = {
    "draft_app": r"only releases with status draft|\bon (?:a )?draft app\b",
    "first_release_requires_console": (
        r"(?:first (?:apk|app bundle|release)|at least one (?:apk|app bundle)).{0,120}play console"
        r"|play console.{0,120}(?:first (?:apk|app bundle|release)|at least one (?:apk|app bundle))"
    ),
    "legal_declaration_required": (
        r"(?:complete|accept|provide|submit|missing|required).{0,100}"
        r"(?:legal consent|terms of service|privacy policy|data safety|data privacy|content rating|app content)"
        r"|(?:legal consent|terms of service|privacy policy|data safety|data privacy|content rating|app content)"
        r".{0,100}(?:complete|accept|provide|submit|missing|required)"
    ),
}


def publish_error_details(response):
    error = read_google_error(response)
    filtered = filter_google_error(error)
    if response.code not in {400, 403, 409, 412}:
        return filtered
    message = error.get("message")
    if isinstance(message, str):
        for category, pattern in PREREQUISITE_PATTERNS.items():
            if re.search(pattern, message, re.IGNORECASE | re.DOTALL):
                filtered["prerequisite"] = category
                break
    return filtered


def publisher_request(
    opener, token, method, url, data=None, content_type=None, timeout=HTTP_TIMEOUT_SECONDS,
):
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    return request_json(opener, request, collect_error=publish_error_details, timeout=timeout)


def inspect_bundle_catalog(content, status):
    snapshot = summarize_bundles(content, status)
    codes = {bundle["versionCode"] for bundle in content.get("bundles", [])}
    # Phone and Wear reserve separate 100,000-code ranges in the app's release scheme.
    newer = {
        track: sorted(code for code in codes if expected < code < (expected // 100_000 + 1) * 100_000)
        for track, expected in TRACKS.items()
    }
    unexpected = sorted(code for code in codes if code >= 300_000)
    if any(newer.values()) or unexpected:
        snapshot["result"] = "candidate_conflict"
        snapshot["newer_version_codes"] = newer
        snapshot["unexpected_version_codes"] = unexpected
    return snapshot


def upload_missing_bundles(opener, token, edit_id, bundles, report):
    url = f"{UPLOAD_URL}/{edit_id}/bundles?uploadType=media"
    for track, code in TRACKS.items():
        if report["bundle_catalog"]["candidates"][track]["result"] == "hash_matches":
            report["uploads"][track] = outcome("reused")
            continue
        try:
            receipt, status = publisher_request(
                opener, token, "POST", url, bundles[code], "application/octet-stream",
                UPLOAD_TIMEOUT_SECONDS,
            )
            digest = receipt.get("sha256")
            actual_code = receipt.get("versionCode")
            if (
                type(actual_code) is not int or actual_code != code
                or not isinstance(digest, str) or digest.lower() != BUNDLE_SHA256[code]
            ):
                raise PreflightError("bundle_receipt_mismatch", status)
        except PreflightError as error:
            report["uploads"][track] = error_outcome(error)
            return False
        report["uploads"][track] = outcome("uploaded", status)
    return True


def stage_target_tracks(opener, token, edit_url, report):
    for track, code in TRACKS.items():
        expected_release = {"status": "completed", "version_codes": [code]}
        body = {
            "track": track,
            "releases": [{
                "status": "completed",
                "versionCodes": [str(code)],
                "releaseNotes": [{"language": "en-US", "text": RELEASE_NOTES}],
            }],
        }
        try:
            receipt, status = publisher_request(
                opener, token, "PUT", f"{edit_url}/tracks/{track}",
                json.dumps(body).encode("utf-8"), "application/json",
            )
            snapshot = summarize_edit_track(receipt, status, track)
            if snapshot["releases"] != [expected_release]:
                raise PreflightError("track_receipt_mismatch", status)
        except PreflightError as error:
            report["track_updates"][track] = error_outcome(error)
            return False
        report["track_updates"][track] = outcome("staged", status, release=expected_release)
    return True


def action_request(opener, token, edit_url, edit_id, action):
    receipt, status = publisher_request(opener, token, "POST", f"{edit_url}:{action}")
    if require_edit_id(receipt, status) != edit_id:
        raise PreflightError("edit_receipt_mismatch", status)
    return status


def observe_submission(opener, token):
    tracks = {}
    for track, code in TRACKS.items():
        snapshot = read_live_track(opener, token, track)
        states = {
            release["lifecycle_state"] for release in snapshot.get("releases", [])
            if release["version_codes"] == [code]
        }
        tracks[track] = outcome(
            snapshot["result"], snapshot["http_status"],
            expected_version_code=code, lifecycle_states=sorted(states),
        )
    observed = all(
        entry["lifecycle_states"] and set(entry["lifecycle_states"]) <= SUBMITTED_STATES
        for entry in tracks.values()
    )
    return {"result": "observed_submitted" if observed else "unconfirmed", "tracks": tracks}


def cleanup_edit(opener, token, edit_url):
    try:
        body, status = edit_request(opener, token, "DELETE", edit_url)
        if body:
            raise PreflightError("invalid_response", status)
    except PreflightError as error:
        return error_outcome(error)
    return outcome("deleted", status)


def publish_edit(opener, token, edit_id, bundles, report):
    edit_url = f"{EDIT_ROOT}/{edit_id}"
    cleanup_allowed = True
    try:
        report["bundle_catalog"] = read_edit_section(
            opener, token, f"{edit_url}/bundles", inspect_bundle_catalog,
        )
        for track in TRACKS:
            report["edit_tracks"][track] = read_edit_section(
                opener, token, f"{edit_url}/tracks/{track}", summarize_edit_track, track,
            )
        snapshots = report["edit_tracks"].values()
        if any(snapshot.get("releases") for snapshot in snapshots):
            report["result"] = "blocked"
            report["failure"] = "target_track_changed"
            return
        if report["bundle_catalog"]["result"] == "candidate_conflict":
            report["result"] = "blocked"
            report["failure"] = "candidate_conflict"
            return
        if report["bundle_catalog"]["result"] != "ok" or any(
            snapshot["result"] != "ok" for snapshot in snapshots
        ):
            return
        if not upload_missing_bundles(opener, token, edit_id, bundles, report):
            return
        if not stage_target_tracks(opener, token, edit_url, report):
            return
        try:
            status = action_request(opener, token, edit_url, edit_id, "validate")
        except PreflightError as error:
            report["validation"] = error_outcome(error)
            return
        report["validation"] = outcome("validated", status)

        cleanup_allowed = False
        try:
            status = action_request(opener, token, edit_url, edit_id, f"commit?{COMMIT_QUERY}")
        except PreflightError as error:
            report["commit"] = error_outcome(error)
            if error.http_status in {400, 401, 403, 404, 409, 412, 422}:
                cleanup_allowed = True
                report["result"] = "rejected"
                return
            report["result"] = "commit_indeterminate"
        else:
            report["commit"] = outcome("acknowledged", status)
            report["result"] = "committed"

        report["review_observation"] = observe_submission(opener, token)
        if (
            report["result"] == "commit_indeterminate"
            and report["review_observation"]["result"] == "observed_submitted"
        ):
            report["result"] = "submission_observed"
    finally:
        if cleanup_allowed:
            report["cleanup"] = cleanup_edit(opener, token, edit_url)
            if report["cleanup"]["result"] != "deleted":
                report["failure"] = "cleanup_failed"


def run_publish(input_directory, encoded):
    report = {
        "result": "failed",
        "package_name": PACKAGE_NAME,
        "source_sha": SOURCE_SHA,
        "version_name": "1.0.5",
        "build_number": 2,
        "candidates": {
            track: {"version_code": code, "sha256": BUNDLE_SHA256[code]}
            for track, code in TRACKS.items()
        },
        "artifact_verification": outcome(),
        "oauth": outcome(),
        "live_tracks": {track: outcome() for track in TRACKS},
        "edit_insert": outcome(),
        "bundle_catalog": outcome(),
        "edit_tracks": {track: outcome() for track in TRACKS},
        "uploads": {track: outcome() for track in TRACKS},
        "track_updates": {track: outcome() for track in TRACKS},
        "validation": outcome(),
        "commit": outcome(),
        "review_observation": {"result": "not_attempted"},
        "cleanup": outcome(),
    }
    try:
        bundles = load_bundles(input_directory)
    except ArtifactVerificationError:
        report["artifact_verification"] = outcome("failed")
        return report
    report["artifact_verification"] = outcome("verified")

    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report["oauth"] = error_outcome(error)
        return report
    report["oauth"] = outcome("ok", status)
    for track in TRACKS:
        report["live_tracks"][track] = read_live_track(opener, token, track)
    snapshots = report["live_tracks"].values()
    if any(snapshot["result"] not in {"ok", "no_content", "pending_release"} for snapshot in snapshots):
        return report
    if any(snapshot.get("releases") for snapshot in snapshots):
        report["result"] = "blocked"
        report["failure"] = "target_track_changed"
        return report

    try:
        receipt, status = edit_request(opener, token, "POST", EDIT_ROOT)
        edit_id = require_edit_id(receipt, status)
    except PreflightError as error:
        report["edit_insert"] = error_outcome(error)
        return report
    report["edit_insert"] = outcome("ok", status)
    publish_edit(opener, token, edit_id, bundles, report)
    return report


def main(argv):
    if len(argv) != 3:
        print("Usage: play_publish.py INPUT_DIRECTORY REPORT_PATH", file=sys.stderr)
        return 2
    report = run_publish(Path(argv[1]), os.environ.pop("PLAY_SERVICE_JSON_B64", ""))
    try:
        Path(argv[2]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play production submission evidence.", file=sys.stderr)
        return 1
    return 0 if report["result"] in {"committed", "submission_observed"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
