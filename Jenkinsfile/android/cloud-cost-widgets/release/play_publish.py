"""Advance Wear OS Internal testing to 200009 in one guarded edit; every phone track stays unchanged."""

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
    APPLICATION_URL, HTTP_TIMEOUT_SECONDS, PACKAGE_NAME, NoRedirect,
    PreflightError, get_access_token, request_json,
)
from play_submission_preflight import (
    edit_request, error_outcome,
    filter_google_error, outcome, read_edit_section, read_google_error, read_live_track,
    repeated_field, require_edit_id, summarize_bundles, summarize_edit_track,
)
from verify_submission_artifacts import ArtifactVerificationError, load_release_bundles, read_archive


WEAR_NOTES = [
    {"language": "en-US", "text": (
        "The Tile shows the budget on a card: its name, the amount and where the amount came from. Its button "
        "now reads Open and fits. The Tile picker shows a preview and the app icon, and the watch-face "
        "complication list uses the app icon too."
    )},
    {"language": "zh-TW", "text": (
        "資訊方塊改用卡片顯示預算名稱、金額和資料來源，按鈕改成「開啟」，不會再被截斷。"
        "新增資訊方塊時會看到預覽圖和 App 圖示，錶面資料欄位清單也改用 App 圖示。"
    )},
]
PHONE_ALPHA = "alpha"
PHONE_INTERNAL = "internal"
WEAR_INTERNAL = "wear:internal"
WEAR_VERSION_NAME = "1.0.8"
# Wear OS 1.0.8 changes only the Tile and its picker entry, so it ships alone beside phone 1.0.10.
TRACKS = {WEAR_INTERNAL: 200009}
FORM_FACTORS = {WEAR_INTERNAL: "wear"}
SIGNED_DIGEST_PARAMETERS = {WEAR_INTERNAL: "SIGNED_WEAR_SHA256"}
# The alpha-status run of android/cloud-cost-widgets/release #2 (2026-11-24) read phone Alpha 100009, phone
# Internal 100011 and Wear OS Internal 200008 as published. The phone tracks are guarded and left unchanged.
PREDECESSORS = {WEAR_INTERNAL: 200008}
UNCHANGED_PHONE = {PHONE_ALPHA: 100009, PHONE_INTERNAL: 100011}
RELEASE_NOTES = {WEAR_INTERNAL: WEAR_NOTES}
# Build #22's edit inventory also listed 1.0.4 on both Open testing tracks. Play Console shows the phone track
# paused with that release superseded and the Wear OS track inactive. This release leaves both unchanged.
INACTIVE_OPEN_TESTING = {"beta": 100005, "wear:beta": 200005}
EXPECTED_ACTIVE = {**UNCHANGED_PHONE, **INACTIVE_OPEN_TESTING, **PREDECESSORS}
# The tracks whose lifecycle the archived status read covers; all of them hold a release. A track without one,
# such as production, must be absent from the fresh edit inventory, which is compared exactly before any write.
GUARDED_TRACKS = (*UNCHANGED_PHONE, *TRACKS)
REPORTED_TRACKS = {
    f"{prefix}{name}" for prefix in ("", "wear:") for name in ("production", "beta", "alpha", "internal")
}
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
QUOTA_REASON = "listing_releases_quota_exceeded"
MAX_PREFLIGHT_AGE_SECONDS = 30 * 60
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


def inspect_bundle_catalog(content, status, signed_digests):
    snapshot = summarize_bundles(
        content, status, TRACKS, {TRACKS[track]: digest for track, digest in signed_digests.items()},
    )
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
    # These predecessors were uploaded before the pipeline recorded digests, so only their presence is checked.
    # The exact fresh track inventory below still has to show each one as the only release on its track.
    snapshot["predecessors"] = {
        track: {"version_code": code, "result": "present" if code in hashes else "missing"}
        for track, code in PREDECESSORS.items()
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
        compatible = snapshot["releases"] == [{"status": "completed", "version_codes": [PREDECESSORS[track]]}]
        snapshot["result"] = "predecessor" if compatible and not custom_targeting else "incompatible_release"
        targets[track] = snapshot
    if unrelated_pending:
        return outcome("unrelated_pending", status, targets=targets)
    expected = {
        track: ([{"status": "completed", "version_codes": [code]}], False)
        for track, code in EXPECTED_ACTIVE.items()
    }
    if active != expected:
        # Name only the standard tracks; custom track names are counted, not echoed.
        observed = {
            track: {"releases": releases, "custom_targeting": targeting}
            for track, (releases, targeting) in sorted(active.items()) if track in REPORTED_TRACKS
        }
        return outcome(
            "inventory_mismatch", status, targets=targets, observed_active=observed,
            other_active_track_count=sum(track not in REPORTED_TRACKS for track in active),
        )
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
            # The Play lifecycle read quota ran out. The fresh edit inventory must still show this track's exact
            # releases before any write, and the guarded commit fails if changes are in review.
            quota_tracks.append(track)
            proven_tracks[track] = outcome("forbidden", 403, quota=QUOTA_REASON)
            continue
        allowed = outcome("ok", 200, releases=[published_release(EXPECTED_ACTIVE[track])])
        if snapshot != allowed:
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


def upload_bundles(opener, token, edit_id, bundles, signed_digests, report):
    for track, code in TRACKS.items():
        if report["bundle_catalog"]["candidates"][track]["result"] == "hash_matches":
            report["uploads"][track] = outcome("reused")
            continue
        url = f"{UPLOAD_URL}/{edit_id}/bundles?uploadType=media"
        try:
            receipt, status = publisher_request(
                opener, token, "POST", url, bundles[track], "application/octet-stream",
                UPLOAD_TIMEOUT_SECONDS,
            )
            digest = receipt.get("sha256")
            actual_code = receipt.get("versionCode")
            if (
                type(actual_code) is not int or actual_code != code
                or not isinstance(digest, str) or digest.lower() != signed_digests[track]
            ):
                raise PreflightError("bundle_receipt_mismatch", status)
        except PreflightError as error:
            report["uploads"][track] = error_outcome(error)
            return False
        report["uploads"][track] = outcome("uploaded", status)
    return True


def stage_target_tracks(opener, token, edit_url, report):
    for track, code in TRACKS.items():
        notes = RELEASE_NOTES[track]
        expected_release = {"status": "completed", "version_codes": [code]}
        body = {
            "track": track,
            "releases": [{
                "status": "completed",
                "versionCodes": [str(code)],
                "releaseNotes": notes,
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
                staged_notes = staged_release["releaseNotes"]
                if (
                    not isinstance(staged_notes, list) or len(staged_notes) != len(notes)
                    or not all(note in staged_notes for note in notes)
                ):
                    raise PreflightError("track_receipt_notes_mismatch", status)
                matched_notes["matched_note_locales"] = [note["language"] for note in notes]
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
    elif any(entry["lifecycle_states"] == [NOT_SENT] for entry in tracks.values()):
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


def publish_edit(opener, token, edit_id, bundles, signed_digests, report):
    edit_url = f"{EDIT_ROOT}/{edit_id}"
    cleanup_allowed = True
    try:
        report["bundle_catalog"] = read_edit_section(
            opener, token, f"{edit_url}/bundles", inspect_bundle_catalog, signed_digests,
        )
        if not check_edit_tracks(opener, token, edit_url, report):
            return
        if report["bundle_catalog"]["result"] == "candidate_conflict":
            report["result"] = "blocked"
            report["failure"] = "candidate_conflict"
            return
        if report["bundle_catalog"]["result"] != "ok":
            return
        missing = sorted(
            track for track, entry in report["bundle_catalog"]["predecessors"].items()
            if entry["result"] != "present"
        )
        if missing:
            report.update(result="blocked", failure="predecessor_missing", blocking_track=missing[0])
            return
        if not upload_bundles(opener, token, edit_id, bundles, signed_digests, report):
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
        "version_names": {"wear": WEAR_VERSION_NAME},
        "build_number": None,
        "candidates": {track: {"version_code": code, "sha256": None} for track, code in TRACKS.items()},
        "predecessor_version_codes": dict(PREDECESSORS),
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
    signed_digests = {
        track: release_inputs.get(parameter, "") for track, parameter in SIGNED_DIGEST_PARAMETERS.items()
    }
    if not (
        re.fullmatch(r"[0-9a-f]{40}", source_commit)
        and re.fullmatch(r"[0-9a-f]{40}", pipeline_commit)
        and re.fullmatch(r"[1-9][0-9]{0,9}", signed_build)
        and all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in signed_digests.values())
    ):
        report["artifact_verification"] = outcome("invalid_release_parameters")
        return report
    report.update(source_sha=source_commit, source_pipeline_sha=pipeline_commit, build_number=int(signed_build))
    for track, digest in signed_digests.items():
        report["candidates"][track]["sha256"] = digest
    try:
        loaded = load_release_bundles(
            input_directory, source_commit, pipeline_commit,
            {FORM_FACTORS[track]: digest for track, digest in signed_digests.items()},
        )
        bundles = {track: loaded[FORM_FACTORS[track]] for track in TRACKS}
    except (ArtifactVerificationError, KeyError, TypeError):
        report["artifact_verification"] = outcome("failed")
        return report
    if any(hashlib.sha256(bundles[track]).hexdigest() != signed_digests[track] for track in TRACKS):
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
    publish_edit(opener, token, edit_id, bundles, signed_digests, report)
    return report


def main(argv):
    if len(argv) != 3:
        print("Usage: play_publish.py INPUT_DIRECTORY REPORT_PATH", file=sys.stderr)
        return 2
    report = run_publish(Path(argv[1]), os.environ.pop("PLAY_SERVICE_JSON_B64", ""), os.environ)
    try:
        Path(argv[2]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play internal testing submission evidence.", file=sys.stderr)
        return 1
    return 0 if report["result"] in {"committed", "submission_observed"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
