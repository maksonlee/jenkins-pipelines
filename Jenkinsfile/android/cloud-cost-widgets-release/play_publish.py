"""Advance the existing phone closed Alpha from 100006 to the signed 100007."""

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from play_preflight import (
    APPLICATION_URL, HTTP_TIMEOUT_SECONDS, PACKAGE_NAME, TRACKS as PRODUCTION_TRACKS, NoRedirect,
    PreflightError, get_access_token, request_json,
)
from play_submission_preflight import (
    BUNDLE_SHA256, edit_request, error_outcome,
    filter_google_error, outcome, read_edit_section, read_google_error, read_live_track,
    repeated_field, require_edit_id, summarize_bundles, summarize_edit_track,
)
from verify_submission_artifacts import ArtifactVerificationError, load_phone_bundle


RELEASE_NOTES = [
    {"language": "en-US", "text": (
        "Phone home-screen widgets show one budget, start at 2×1, and open budget details when tapped. "
        "The app icon has more room inside the launcher mask."
    )},
    {"language": "zh-TW", "text": (
        "手機桌面小工具每張顯示一筆預算，預設為 2×1。點一下即可開啟預算詳細資料。"
        "App 圖示的雲朵在遮罩內保留更多留白。"
    )},
]
PHONE_ALPHA = "alpha"
PHONE_PREDECESSOR = 100006
PHONE_CANDIDATE = 100007
VERSION_NAME = "1.0.6"
TRACKS = {PHONE_ALPHA: PHONE_CANDIDATE}
GUARDED_TRACKS = (*TRACKS, *PRODUCTION_TRACKS, "internal", "wear:internal")
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
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
    PUBLISHED,
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
    if response.code not in {400, 403, 404, 409, 412}:
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


def inspect_bundle_catalog(content, status, signed_digest):
    snapshot = summarize_bundles(content, status, TRACKS, {PHONE_CANDIDATE: signed_digest})
    hashes = {bundle["versionCode"]: bundle.get("sha256") for bundle in content.get("bundles", [])}
    # Phone and Wear reserve separate 100,000-code ranges in the app's release scheme.
    newer = {
        track: sorted(code for code in hashes if expected < code < (expected // 100_000 + 1) * 100_000)
        for track, expected in TRACKS.items()
    }
    unexpected = sorted(code for code in hashes if code >= 300_000)
    if any(newer.values()) or unexpected:
        snapshot["result"] = "candidate_conflict"
        snapshot["newer_version_codes"] = newer
        snapshot["unexpected_version_codes"] = unexpected
    predecessor_hash = hashes.get(PHONE_PREDECESSOR)
    expected_predecessor_hash = BUNDLE_SHA256[PHONE_PREDECESSOR]
    if predecessor_hash is None:
        comparison = "hash_unavailable"
    elif isinstance(predecessor_hash, str) and predecessor_hash.lower() == expected_predecessor_hash:
        comparison = "hash_matches"
    else:
        comparison = "hash_conflict"
    snapshot["predecessor"] = {
        "version_code": PHONE_PREDECESSOR,
        "expected_sha256": expected_predecessor_hash,
        "result": comparison,
    }
    return snapshot


def summarize_track_inventory(content, status):
    empty_inventory = {"kind": "androidpublisher#tracksListResponse"}
    if not content:
        raise PreflightError("invalid_response", status)
    inventory = repeated_field(content, "tracks", status, empty_inventory)
    targets = {track: outcome("missing", status) for track in TRACKS}
    seen = set()
    unrelated_pending = False
    for entry in inventory:
        track = entry.get("track")
        if not isinstance(track, str) or not track.strip() or track in seen:
            raise PreflightError("invalid_response", status)
        seen.add(track)
        snapshot = summarize_edit_track(entry, status, track)
        if track not in TRACKS:
            unrelated_pending |= snapshot["result"] == "pending_release"
            continue
        custom_targeting = any(
            raw.get("countryTargeting", {}) != {} or "userFraction" in raw
            for raw in entry.get("releases", [])
        )
        snapshot["result"] = "incompatible_release"
        if not custom_targeting:
            for code, result in ((PHONE_PREDECESSOR, "predecessor"), (PHONE_CANDIDATE, "candidate")):
                if snapshot["releases"] == [{"status": "completed", "version_codes": [code]}]:
                    snapshot["result"] = result
        targets[track] = snapshot
    return outcome("unrelated_pending" if unrelated_pending else "ok", status, targets=targets)


def check_live_tracks(opener, token, report):
    for track in report["live_tracks"]:
        report["live_tracks"][track] = read_live_track(opener, token, track)
    for track, snapshot in report["live_tracks"].items():
        result = snapshot["result"]
        if result == "pending_release":
            failure = "target_track_changed" if track == PHONE_ALPHA else "unrelated_pending_release"
            report.update(result="blocked", failure=failure)
            return False
        if track == PHONE_ALPHA:
            if result in {"no_content", "not_found"}:
                report.update(result="blocked", failure="target_lifecycle_unverified")
                return False
            if result != "ok":
                return False
            expected = [
                [{"lifecycle_state": PUBLISHED, "version_codes": [code]}]
                for code in (PHONE_PREDECESSOR, PHONE_CANDIDATE)
            ]
            if snapshot["releases"] not in expected:
                report.update(result="blocked", failure="target_track_changed")
                return False
        elif result not in {"ok", "no_content"}:
            return False
    return True


def check_edit_tracks(opener, token, edit_url, report):
    inventory = read_edit_section(
        opener, token, f"{edit_url}/tracks", summarize_track_inventory,
    )
    report["track_inventory"] = inventory
    if "targets" in inventory:
        report["edit_tracks"] = inventory.pop("targets")
    if inventory["result"] == "unrelated_pending":
        report.update(result="blocked", failure="unrelated_pending_release")
        return False
    if inventory["result"] != "ok":
        return False

    snapshot = report["edit_tracks"][PHONE_ALPHA]
    live_track = report["live_tracks"][PHONE_ALPHA]
    if snapshot["result"] not in {"predecessor", "candidate"}:
        report.update(result="blocked", failure="target_track_changed")
        return False
    if live_track["releases"][0]["version_codes"] != snapshot["releases"][0]["version_codes"]:
        report.update(result="blocked", failure="target_track_changed")
        return False
    return True


def upload_phone_bundle(opener, token, edit_id, phone_bundle, signed_digest, report):
    if report["bundle_catalog"]["candidates"][PHONE_ALPHA]["result"] == "hash_matches":
        report["uploads"][PHONE_ALPHA] = outcome("reused")
        return True
    url = f"{UPLOAD_URL}/{edit_id}/bundles?uploadType=media"
    try:
        receipt, status = publisher_request(
            opener, token, "POST", url, phone_bundle, "application/octet-stream",
            UPLOAD_TIMEOUT_SECONDS,
        )
        digest = receipt.get("sha256")
        actual_code = receipt.get("versionCode")
        if (
            type(actual_code) is not int or actual_code != PHONE_CANDIDATE
            or not isinstance(digest, str) or digest.lower() != signed_digest
        ):
            raise PreflightError("bundle_receipt_mismatch", status)
    except PreflightError as error:
        report["uploads"][PHONE_ALPHA] = error_outcome(error)
        return False
    report["uploads"][PHONE_ALPHA] = outcome("uploaded", status)
    return True


def stage_target_tracks(opener, token, edit_url, report):
    for track, code in TRACKS.items():
        expected_release = {"status": "completed", "version_codes": [code]}
        body = {
            "track": track,
            "releases": [{
                "status": "completed",
                "versionCodes": [str(code)],
                "releaseNotes": RELEASE_NOTES,
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


def commit_edit(opener, token, edit_url, edit_id):
    receipt, status = publisher_request(opener, token, "POST", f"{edit_url}:commit?{COMMIT_QUERY}")
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


def publish_edit(opener, token, edit_id, phone_bundle, signed_digest, report):
    edit_url = f"{EDIT_ROOT}/{edit_id}"
    cleanup_allowed = True
    try:
        report["bundle_catalog"] = read_edit_section(
            opener, token, f"{edit_url}/bundles", inspect_bundle_catalog, signed_digest,
        )
        if not check_edit_tracks(opener, token, edit_url, report):
            return
        if report["bundle_catalog"]["result"] == "candidate_conflict":
            report["result"] = "blocked"
            report["failure"] = "candidate_conflict"
            return
        if report["bundle_catalog"]["result"] != "ok":
            return
        if report["edit_tracks"][PHONE_ALPHA]["result"] == "candidate":
            candidate = report["bundle_catalog"]["candidates"][PHONE_ALPHA]
            if candidate["result"] == "hash_matches":
                report["result"] = "already_current"
            else:
                report.update(result="blocked", failure="current_candidate_hash_unverified")
            return
        if report["bundle_catalog"]["predecessor"]["result"] == "hash_conflict":
            report.update(result="blocked", failure="predecessor_hash_conflict")
            return
        if not upload_phone_bundle(opener, token, edit_id, phone_bundle, signed_digest, report):
            return
        if not stage_target_tracks(opener, token, edit_url, report):
            return

        cleanup_allowed = False
        try:
            status = commit_edit(opener, token, edit_url, edit_id)
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
                if report["result"] == "already_current":
                    report["result"] = "failed"


def run_publish(input_directory, encoded, release_inputs):
    report = {
        "result": "failed",
        "package_name": PACKAGE_NAME,
        "source_sha": None,
        "source_pipeline_sha": None,
        "version_name": VERSION_NAME,
        "build_number": None,
        "candidates": {PHONE_ALPHA: {"version_code": PHONE_CANDIDATE, "sha256": None}},
        "predecessor_version_code": PHONE_PREDECESSOR,
        "artifact_verification": outcome(),
        "oauth": outcome(),
        "live_tracks": {track: outcome() for track in GUARDED_TRACKS},
        "edit_insert": outcome(),
        "bundle_catalog": outcome(),
        "track_inventory": outcome(),
        "edit_tracks": {track: outcome() for track in TRACKS},
        "uploads": {track: outcome() for track in TRACKS},
        "track_updates": {track: outcome() for track in TRACKS},
        "commit": outcome(),
        "review_observation": {"result": "not_attempted"},
        "cleanup": outcome(),
    }
    source_commit = release_inputs.get("PRODUCT_SHA", "")
    pipeline_commit = release_inputs.get("PIPELINE_SHA", "")
    signed_build = release_inputs.get("SIGNED_BUILD", "")
    signed_digest = release_inputs.get("SIGNED_SHA256", "")
    if not (
        re.fullmatch(r"[0-9a-f]{40}", source_commit)
        and re.fullmatch(r"[0-9a-f]{40}", pipeline_commit)
        and re.fullmatch(r"[1-9][0-9]{0,9}", signed_build)
        and re.fullmatch(r"[0-9a-f]{64}", signed_digest)
    ):
        report["artifact_verification"] = outcome("invalid_release_parameters")
        return report
    report.update(source_sha=source_commit, source_pipeline_sha=pipeline_commit, build_number=int(signed_build))
    report["candidates"][PHONE_ALPHA]["sha256"] = signed_digest
    try:
        phone_bundle = load_phone_bundle(input_directory, source_commit, pipeline_commit, signed_digest)
    except ArtifactVerificationError:
        report["artifact_verification"] = outcome("failed")
        return report
    if hashlib.sha256(phone_bundle).hexdigest() != signed_digest:
        report["artifact_verification"] = outcome("hash_mismatch")
        return report
    report["artifact_verification"] = outcome("verified")

    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report["oauth"] = error_outcome(error)
        return report
    report["oauth"] = outcome("ok", status)
    if not check_live_tracks(opener, token, report):
        return report

    try:
        receipt, status = edit_request(opener, token, "POST", EDIT_ROOT)
        edit_id = require_edit_id(receipt, status)
    except PreflightError as error:
        report["edit_insert"] = error_outcome(error)
        return report
    report["edit_insert"] = outcome("ok", status)
    publish_edit(opener, token, edit_id, phone_bundle, signed_digest, report)
    return report


def main(argv):
    if len(argv) != 3:
        print("Usage: play_publish.py INPUT_DIRECTORY REPORT_PATH", file=sys.stderr)
        return 2
    report = run_publish(Path(argv[1]), os.environ.pop("PLAY_SERVICE_JSON_B64", ""), os.environ)
    try:
        Path(argv[2]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play Alpha submission evidence.", file=sys.stderr)
        return 1
    return 0 if report["result"] in {"already_current", "committed", "submission_observed"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
