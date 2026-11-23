"""Advance the existing phone closed Alpha from 100008 to the signed 100009."""

import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from play_alpha_status import lifecycle_error
from play_preflight import (
    APPLICATION_URL, HTTP_TIMEOUT_SECONDS, PACKAGE_NAME, TRACKS as PRODUCTION_TRACKS, NoRedirect,
    PreflightError, get_access_token, request_json,
)
from play_submission_preflight import (
    edit_request, error_outcome,
    filter_google_error, outcome, read_edit_section, read_google_error, read_live_track,
    repeated_field, require_edit_id, summarize_bundles, summarize_edit_track,
)
from verify_submission_artifacts import ArtifactVerificationError, load_phone_bundle, read_archive


RELEASE_NOTES = [
    {"language": "en-US", "text": (
        "Tap an unconfigured home-screen widget to choose a budget, or open the app’s Settings "
        "to configure it. Select one budget before saving."
    )},
    {"language": "zh-TW", "text": (
        "點一下尚未設定的桌面小工具即可選擇預算，也可以到 App 的「設定」重新設定原本的小工具。"
        "選定一筆預算後才能儲存。"
    )},
]
PHONE_ALPHA = "alpha"
PHONE_PRIOR_PUBLISHED = 100007
PHONE_PREDECESSOR = 100008
PHONE_PREDECESSOR_DIGEST = "32ed81f2f4d6e5ca32fed0818526758746dcbbdfb3fc49b8c72c983d61c40d59"
PHONE_CANDIDATE = 100009
VERSION_NAME = "1.0.8"
TRACKS = {PHONE_ALPHA: PHONE_CANDIDATE}
GUARDED_TRACKS = (*TRACKS, *PRODUCTION_TRACKS, "internal", "wear:internal")
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
QUOTA_REASON = "listing_releases_quota_exceeded"
MAX_PREFLIGHT_AGE_SECONDS = 30 * 60
# Build #14 committed the signed predecessor. Build #15 observed it in review and checked the other tracks.
EXPECTED_ACTIVE = {
    PHONE_ALPHA: PHONE_PREDECESSOR, "internal": 100005, "wear:internal": 200005,
}
OTHER_TRACKS_AUDIT_BUILD = 15
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
    if predecessor_hash is None:
        comparison = "hash_unavailable"
    elif isinstance(predecessor_hash, str) and predecessor_hash.lower() == PHONE_PREDECESSOR_DIGEST:
        comparison = "hash_matches"
    else:
        comparison = "hash_conflict"
    snapshot["predecessor"] = {
        "version_code": PHONE_PREDECESSOR,
        "expected_sha256": PHONE_PREDECESSOR_DIGEST,
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
    active = {}
    unrelated_pending = False
    for entry in inventory:
        track = entry.get("track")
        if not isinstance(track, str) or not track.strip() or track in seen:
            raise PreflightError("invalid_response", status)
        seen.add(track)
        snapshot = summarize_edit_track(entry, status, track)
        custom_targeting = any(
            raw.get("countryTargeting", {}) != {} or "userFraction" in raw
            for raw in entry.get("releases", [])
        )
        if snapshot["releases"]:
            active[track] = (snapshot["releases"], custom_targeting)
        if track not in TRACKS:
            unrelated_pending |= snapshot["result"] == "pending_release"
            continue
        compatible = snapshot["releases"] == [{"status": "completed", "version_codes": [PHONE_PREDECESSOR]}]
        snapshot["result"] = "predecessor" if compatible and not custom_targeting else "incompatible_release"
        targets[track] = snapshot
    if unrelated_pending:
        return outcome("unrelated_pending", status, targets=targets)
    expected = {
        track: ([{"status": "completed", "version_codes": [code]}], False)
        for track, code in EXPECTED_ACTIVE.items()
    }
    if active != expected:
        return outcome("inventory_mismatch", status, targets=targets)
    return outcome("ok", status, targets=targets)


def published_release(code):
    return {"lifecycle_state": PUBLISHED, "version_codes": [code]}


def check_preflight_archive(input_directory, report):
    try:
        archive_bytes = read_archive(input_directory.resolve(), "alpha-status.json")
        archive = json.loads(archive_bytes)
    except (ArtifactVerificationError, OSError, ValueError, RecursionError):
        report.update(result="blocked", failure="preflight_archive_unverified")
        return False
    if (
        not isinstance(archive, dict)
        or archive.get("package_name") != PACKAGE_NAME
        or archive.get("result") not in ("checked", "quota_limited")
        or archive.get("oauth") != outcome("ok", 200)
        or not isinstance(archive.get("tracks"), dict)
        or set(archive["tracks"]) != set(GUARDED_TRACKS)
    ):
        report.update(result="blocked", failure="preflight_archive_unverified")
        return False
    try:
        observed_at = datetime.fromisoformat(archive.get("observed_at", "").replace("Z", "+00:00"))
        if observed_at.utcoffset() != timedelta(0):
            raise ValueError("expected UTC observation")
        age = (datetime.now(timezone.utc) - observed_at).total_seconds()
    except (AttributeError, TypeError, ValueError, OverflowError):
        report.update(result="blocked", failure="preflight_timestamp_invalid")
        return False
    if not 0 <= age <= MAX_PREFLIGHT_AGE_SECONDS:
        report.update(result="blocked", failure="preflight_outside_age_limit")
        return False

    quota_tracks = []
    proven_tracks = {}
    for track in GUARDED_TRACKS:
        snapshot = archive["tracks"][track]
        if not isinstance(snapshot, dict) or type(snapshot.get("http_status")) is not int:
            report.update(result="blocked", failure="preflight_track_unverified", blocking_track=track)
            return False
        if (
            snapshot.get("result") == "forbidden"
            and snapshot.get("http_status") == 403
            and snapshot.get("quota") == QUOTA_REASON
        ):
            if track == PHONE_ALPHA:
                report["preflight_tracks"][track] = outcome("forbidden", 403, quota=QUOTA_REASON)
                report.update(result="blocked", failure="alpha_preflight_quota_unverified", blocking_track=track)
                return False
            quota_tracks.append(track)
            proven_tracks[track] = outcome("forbidden", 403, quota=QUOTA_REASON)
            continue
        if track in EXPECTED_ACTIVE:
            allowed = [outcome("ok", 200, releases=[published_release(EXPECTED_ACTIVE[track])])]
        else:
            allowed = [outcome("no_content", 204)]
        if track == PHONE_ALPHA:
            unsent = {"lifecycle_state": NOT_SENT, "version_codes": [PHONE_PREDECESSOR]}
            previous = published_release(PHONE_PRIOR_PUBLISHED)
            for releases in ([unsent], [unsent, previous], [previous, unsent]):
                allowed.append(outcome("pending_release", 200, releases=releases))
        if snapshot not in allowed:
            report.update(result="blocked", failure="preflight_track_unverified", blocking_track=track)
            return False
        proven_tracks[track] = snapshot
    if bool(quota_tracks) != (archive["result"] == "quota_limited"):
        report.update(result="blocked", failure="preflight_archive_unverified")
        return False

    report["preflight_tracks"] = proven_tracks
    report["lifecycle_validation"] = {
        "result": "quota_fallback" if quota_tracks else "signed_preflight",
        "quota_tracks": quota_tracks,
        "archive_build": report["build_number"],
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "observed_at": observed_at.isoformat(),
        "fresh_inventory": "not_attempted",
    }
    if quota_tracks:
        report["lifecycle_validation"]["historical_evidence_builds"] = {
            track: OTHER_TRACKS_AUDIT_BUILD for track in quota_tracks
        }
    return True


def check_edit_tracks(opener, token, edit_url, report):
    inventory = read_edit_section(
        opener, token, f"{edit_url}/tracks", summarize_track_inventory,
    )
    report["track_inventory"] = inventory
    if "targets" in inventory:
        report["edit_tracks"] = inventory.pop("targets")
    report["lifecycle_validation"]["fresh_inventory"] = (
        "verified" if inventory["result"] == "ok" else inventory["result"]
    )
    if inventory["result"] == "unrelated_pending":
        report.update(result="blocked", failure="unrelated_pending_release")
        return False
    if inventory["result"] == "inventory_mismatch":
        report.update(result="blocked", failure="fresh_inventory_mismatch")
        return False
    return inventory["result"] == "ok"


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
            staged_release = receipt["releases"][0]
            matched_notes = {}
            if "releaseNotes" in staged_release:
                notes = staged_release["releaseNotes"]
                if (
                    not isinstance(notes, list) or len(notes) != len(RELEASE_NOTES)
                    or not all(note in notes for note in RELEASE_NOTES)
                ):
                    raise PreflightError("track_receipt_notes_mismatch", status)
                matched_notes["matched_note_locales"] = [note["language"] for note in RELEASE_NOTES]
        except PreflightError as error:
            report["track_updates"][track] = error_outcome(error)
            return False
        report["track_updates"][track] = outcome("staged", status, release=expected_release, **matched_notes)
    return True


def commit_edit(opener, token, edit_url, edit_id):
    receipt, status = publisher_request(opener, token, "POST", f"{edit_url}:commit?{COMMIT_QUERY}")
    if require_edit_id(receipt, status) != edit_id:
        raise PreflightError("edit_receipt_mismatch", status)
    return status


def observe_submission(opener, token):
    tracks = {}
    for track, code in TRACKS.items():
        snapshot = read_live_track(opener, token, track, collect_error=lifecycle_error)
        states = {
            release["lifecycle_state"] for release in snapshot.get("releases", [])
            if release["version_codes"] == [code]
        }
        tracks[track] = outcome(
            snapshot["result"], snapshot["http_status"],
            expected_version_code=code, lifecycle_states=sorted(states),
        )
        if "quota" in snapshot:
            tracks[track]["quota"] = snapshot["quota"]
    observed = all(
        entry["lifecycle_states"] and set(entry["lifecycle_states"]) <= SUBMITTED_STATES
        for entry in tracks.values()
    )
    if observed:
        result = "observed_submitted"
    elif tracks[PHONE_ALPHA]["lifecycle_states"] == [NOT_SENT]:
        result = "observed_not_sent_for_review"
    else:
        result = "unconfirmed"
    return {"result": result, "tracks": tracks}


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
        predecessor = report["bundle_catalog"]["predecessor"]["result"]
        if predecessor == "hash_conflict":
            report.update(result="blocked", failure="predecessor_hash_conflict")
            return
        if predecessor != "hash_matches":
            report.update(result="blocked", failure="predecessor_hash_unavailable")
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
        "preflight_tracks": {},
        "lifecycle_validation": {"result": "not_attempted"},
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
    if not check_preflight_archive(input_directory, report):
        return report

    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report["oauth"] = error_outcome(error)
        return report
    report["oauth"] = outcome("ok", status)

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
    return 0 if report["result"] in {"committed", "submission_observed"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
