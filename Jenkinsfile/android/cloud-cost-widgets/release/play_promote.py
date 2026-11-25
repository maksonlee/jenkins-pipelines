"""Promote the phone and Wear OS bundles on Internal testing to both Production tracks in one guarded edit.

`check` opens an edit, proves Play's bundle catalog and track inventory, deletes the edit and writes the approval
text. `submit` repeats the proof in a new edit, stages both Production releases and commits them. Managed publishing
is off, so Play sends the commit for review itself. No bundle is uploaded: both were built, signed and verified by
earlier release builds and are already on Play.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from play_alpha_status import TRACK_NAMES as STATUS_TRACKS, lifecycle_error
from play_preflight import APPLICATION_URL, PACKAGE_NAME, NoRedirect, PreflightError, get_access_token, request_json
from play_publish import PREREQUISITE_PATTERNS, cleanup_edit, publisher_request
from play_submission_preflight import (
    edit_request, error_outcome, filter_google_error, outcome, read_edit_section, read_google_error, read_live_track,
    repeated_field, require_edit_id, summarize_bundles, summarize_edit_track,
)


PHONE_NOTES = [
    {"language": "en-US", "text": (
        "First release: see the AWS-reported spending of your monthly cost budgets in the app, in a home-screen "
        "widget and on a paired Wear OS watch. Tap Try demo to look around without an AWS account."
    )},
    {"language": "zh-TW", "text": (
        "第一個正式版：在 App、桌面小工具和配對的 Wear OS 手錶上查看 AWS 回報的每月成本預算支出。"
        "點「體驗示範」，不需要 AWS 帳號也能先看看。"
    )},
]
WEAR_NOTES = [
    {"language": "en-US", "text": (
        "First release: see the AWS-reported spending of your monthly cost budgets in the watch app, a Tile and "
        "on your watch face, from your phone or read directly from AWS."
    )},
    {"language": "zh-TW", "text": (
        "第一個正式版：在手錶 App、資訊方塊和錶面上查看 AWS 回報的每月成本預算支出，資料可以來自手機，"
        "也可以直接向 AWS 讀取。"
    )},
]
PRODUCTION = "production"
WEAR_PRODUCTION = "wear:production"
SOURCE_SHA = "3b563cc10500d16c2175941d9eb8edfc5fc3b56a"
TRACKS = {PRODUCTION: 100013, WEAR_PRODUCTION: 200012}
VERSION_NAMES = {PRODUCTION: "1.0.12", WEAR_PRODUCTION: "1.0.11"}
FORM_FACTOR_NAMES = {PRODUCTION: "phone", WEAR_PRODUCTION: "Wear OS"}
RELEASE_NOTES = {PRODUCTION: PHONE_NOTES, WEAR_PRODUCTION: WEAR_NOTES}
# Release #14 built, signed and verified the phone bundle from SOURCE_SHA and #15 committed it to Internal
# testing; #16 and #17 did the same for the Wear OS bundle. These are the digests #14 and #16 archived.
BUNDLE_SHA256 = {
    100013: "0b5789a3b1757043ca61825f4a4d0ed2e6c62c99fdc606ad7d13361d133a0aab",
    200012: "e26d84c29443f01596acf2a63867311e6e259f245db15739a52e0f2cfde58cd3",
}
BUILT_BY = {PRODUCTION: (14, 15), WEAR_PRODUCTION: (16, 17)}
# Every track that holds a release before the promotion. Both Production tracks must be empty. The Open testing
# tracks are inactive but still list 1.0.4, as the internal publisher records.
EXPECTED_ACTIVE = {
    "alpha": 100009, "internal": 100013, "wear:internal": 200012, "beta": 100005, "wear:beta": 200005,
}
# The archived lifecycle read must show the promoted bundles published on Internal testing.
SOURCE_TRACKS = {PRODUCTION: "internal", WEAR_PRODUCTION: "wear:internal"}
REPORTED_TRACKS = {
    f"{prefix}{name}" for prefix in ("", "wear:") for name in ("production", "beta", "alpha", "internal")
}
PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
NOT_SENT = "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"
SUBMITTED_STATES = {
    "RELEASE_LIFECYCLE_STATE_IN_REVIEW", "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED", PUBLISHED,
}
QUOTA_REASON = "listing_releases_quota_exceeded"
MAX_STATUS_AGE_SECONDS = 30 * 60
# Build #18 also passed changesNotSentForReview=true, and Play rejected the commit with 400 INVALID_ARGUMENT
# without a named reason. With managed publishing off, Play sends a commit for review itself.
COMMIT_QUERY = "changesInReviewBehavior=ERROR_IF_IN_REVIEW"
# Play gives the cause of a rejected commit only in its message, which can quote private values, so only the name
# of a known cause is recorded. The first match wins.
COMMIT_CAUSES = (
    ("review_must_be_sent_from_console", r"cannot be sent for review automatically|changesNotSentForReview to true"),
    ("review_sent_automatically", r"sent for review automatically|changesNotSentForReview must not be set"),
    ("countries", r"\bcountr(?:y|ies)\b|\bregions?\b"),
    ("release_notes", r"release notes|recent changes|\blanguage\b"),
    ("version_code", r"version ?code"),
    ("in_review", r"\bin review\b"),
    *PREREQUISITE_PATTERNS.items(),
)
EDIT_ROOT = f"{APPLICATION_URL}/edits"


def approval_text():
    parts = [
        f"{FORM_FACTOR_NAMES[track]} {VERSION_NAMES[track]} ({code}) to Production"
        f"{' (Wear OS)' if track == WEAR_PRODUCTION else ''}, AAB SHA-256 {BUNDLE_SHA256[code]}, "
        f"built by release #{BUILT_BY[track][0]} and on Internal testing since #{BUILT_BY[track][1]}"
        for track, code in TRACKS.items()
    ]
    return (
        f"Promote {PACKAGE_NAME}: " + "; ".join(parts) + f". Source {SOURCE_SHA}. Play's bundle catalog matched "
        "both digests in this build. Every testing track stays unchanged. Play sends the commit for review."
    )


def check_status_archive(path, report):
    try:
        archive = json.loads(Path(path).read_bytes())
    except (OSError, ValueError, RecursionError):
        report.update(result="blocked", failure="status_archive_unverified")
        return False
    if (
        not isinstance(archive, dict)
        or archive.get("package_name") != PACKAGE_NAME
        or archive.get("result") not in ("checked", "quota_limited")
        or archive.get("oauth") != outcome("ok", 200)
        or not isinstance(archive.get("tracks"), dict)
        or set(archive["tracks"]) != set(STATUS_TRACKS)
    ):
        report.update(result="blocked", failure="status_archive_unverified")
        return False
    try:
        observed_at = datetime.fromisoformat(archive.get("observed_at", "").replace("Z", "+00:00"))
        if observed_at.utcoffset() != timedelta(0):
            raise ValueError("expected UTC observation")
        age = (datetime.now(timezone.utc) - observed_at).total_seconds()
    except (AttributeError, TypeError, ValueError, OverflowError):
        report.update(result="blocked", failure="status_timestamp_invalid")
        return False
    if not 0 <= age <= MAX_STATUS_AGE_SECONDS:
        report.update(result="blocked", failure="status_outside_age_limit")
        return False
    quota_tracks = []
    for track in STATUS_TRACKS:
        snapshot = archive["tracks"][track]
        if not isinstance(snapshot, dict):
            report.update(result="blocked", failure="status_track_unverified", blocking_track=track)
            return False
        if (
            snapshot.get("result") == "forbidden" and snapshot.get("http_status") == 403
            and snapshot.get("quota") == QUOTA_REASON
        ):
            # The fresh edit inventory still has to show this track's exact release before any write.
            quota_tracks.append(track)
            continue
        allowed = outcome("ok", 200, releases=[{"lifecycle_state": PUBLISHED, "version_codes": [EXPECTED_ACTIVE[track]]}])
        if snapshot != allowed:
            report.update(result="blocked", failure="status_track_unverified", blocking_track=track)
            return False
    if bool(quota_tracks) != (archive["result"] == "quota_limited"):
        report.update(result="blocked", failure="status_archive_unverified")
        return False
    report["lifecycle_validation"] = {
        "result": "quota_fallback" if quota_tracks else "published_on_internal",
        "quota_tracks": quota_tracks,
        "observed_at": observed_at.isoformat(),
    }
    return True


def inspect_bundles(content, status):
    snapshot = summarize_bundles(content, status, TRACKS, BUNDLE_SHA256)
    codes = [bundle["versionCode"] for bundle in content.get("bundles", [])]
    # Phone and Wear reserve separate 100,000-code ranges. A newer code means the pipeline's view is out of date.
    newer = {
        track: sorted(code for code in codes if expected < code < (expected // 100_000 + 1) * 100_000)
        for track, expected in TRACKS.items()
    }
    unexpected = sorted(code for code in codes if code >= 300_000)
    if snapshot["result"] == "ok" and (any(newer.values()) or unexpected):
        snapshot["result"] = "candidate_conflict"
    if any(newer.values()) or unexpected:
        snapshot["newer_version_codes"] = newer
        snapshot["unexpected_version_codes"] = unexpected
    if snapshot["result"] == "ok" and any(
        candidate["result"] != "hash_matches" for candidate in snapshot["candidates"].values()
    ):
        snapshot["result"] = "candidate_missing"
    return snapshot


def inspect_tracks(content, status):
    if not content:
        raise PreflightError("invalid_response", status)
    inventory = repeated_field(content, "tracks", status, {"kind": "androidpublisher#tracksListResponse"})
    seen = set()
    active = {}
    pending = False
    for entry in inventory:
        track = entry.get("track")
        if not isinstance(track, str) or not track.strip() or track in seen:
            raise PreflightError("invalid_response", status)
        seen.add(track)
        snapshot = summarize_edit_track(entry, status, track)
        pending |= snapshot["result"] == "pending_release"
        custom_targeting = any(
            raw.get("countryTargeting", {}) != {} or "userFraction" in raw for raw in entry.get("releases", [])
        )
        if snapshot["releases"]:
            active[track] = (snapshot["releases"], custom_targeting)
    if pending:
        return outcome("pending_release", status)
    expected = {
        track: ([{"status": "completed", "version_codes": [code]}], False) for track, code in EXPECTED_ACTIVE.items()
    }
    if active != expected:
        observed = {
            track: {"releases": releases, "custom_targeting": targeting}
            for track, (releases, targeting) in sorted(active.items()) if track in REPORTED_TRACKS
        }
        return outcome(
            "inventory_mismatch", status, observed_active=observed,
            other_active_track_count=sum(track not in REPORTED_TRACKS for track in active),
        )
    return outcome("ok", status)


def inspect_edit(opener, token, edit_url, report):
    # Both sections are read before deciding, so the evidence always shows the whole edit.
    report["bundle_catalog"] = read_edit_section(opener, token, f"{edit_url}/bundles", inspect_bundles)
    report["track_inventory"] = read_edit_section(opener, token, f"{edit_url}/tracks", inspect_tracks)
    for section in ("bundle_catalog", "track_inventory"):
        if report[section]["result"] != "ok":
            report.update(result="blocked", failure=report[section]["result"])
            return False
    return True


def commit_error_details(response):
    error = read_google_error(response)
    details = filter_google_error(error)
    message = error.get("message")
    if isinstance(message, str):
        details["cause"] = next(
            (name for name, pattern in COMMIT_CAUSES if re.search(pattern, message, re.IGNORECASE | re.DOTALL)),
            "unrecognized",
        )
    return details


def commit_edit(opener, token, edit_url, edit_id):
    request = urllib.request.Request(
        f"{edit_url}:commit?{COMMIT_QUERY}", headers={"Authorization": f"Bearer {token}"}, method="POST",
    )
    receipt, status = request_json(opener, request, collect_error=commit_error_details)
    if require_edit_id(receipt, status) != edit_id:
        raise PreflightError("edit_receipt_mismatch", status)
    return status


def stage_tracks(opener, token, edit_url, report):
    for track, code in TRACKS.items():
        notes = RELEASE_NOTES[track]
        expected_release = {"status": "completed", "version_codes": [code]}
        body = {"track": track, "releases": [{
            "status": "completed", "versionCodes": [str(code)], "releaseNotes": notes,
        }]}
        try:
            receipt, status = publisher_request(
                opener, token, "PUT", f"{edit_url}/tracks/{track}",
                json.dumps(body).encode("utf-8"), "application/json",
            )
            snapshot = summarize_edit_track(receipt, status, track)
            if snapshot["releases"] != [expected_release]:
                raise PreflightError("track_receipt_mismatch", status)
            staged_notes = receipt["releases"][0].get("releaseNotes", notes)
            if (
                not isinstance(staged_notes, list) or len(staged_notes) != len(notes)
                or not all(note in staged_notes for note in notes)
            ):
                raise PreflightError("track_receipt_notes_mismatch", status)
        except PreflightError as error:
            report["track_updates"][track] = error_outcome(error)
            return False
        report["track_updates"][track] = outcome("staged", status, release=expected_release)
    return True


def observe(opener, token):
    tracks = {}
    for track, code in TRACKS.items():
        snapshot = read_live_track(opener, token, track, collect_error=lifecycle_error)
        states = {
            release["lifecycle_state"] for release in snapshot.get("releases", []) if release["version_codes"] == [code]
        }
        tracks[track] = outcome(
            snapshot["result"], snapshot["http_status"], expected_version_code=code, lifecycle_states=sorted(states),
        )
        if "quota" in snapshot:
            tracks[track]["quota"] = snapshot["quota"]
    states = [entry["lifecycle_states"] for entry in tracks.values()]
    if all(state == [NOT_SENT] for state in states):
        result = "observed_not_sent_for_review"
    elif all(state and set(state) <= SUBMITTED_STATES for state in states):
        result = "observed_submitted"
    else:
        result = "unconfirmed"
    return {"result": result, "tracks": tracks}


def run(mode, status_path, encoded):
    report = {
        "mode": mode,
        "result": "failed",
        "package_name": PACKAGE_NAME,
        "source_sha": SOURCE_SHA,
        "candidates": {
            track: {"version_name": VERSION_NAMES[track], "version_code": code, "sha256": BUNDLE_SHA256[code]}
            for track, code in TRACKS.items()
        },
        "lifecycle_validation": {"result": "not_attempted"},
        "oauth": outcome(),
        "edit_insert": outcome(),
        "bundle_catalog": outcome(),
        "track_inventory": outcome(),
        "track_updates": {track: outcome() for track in TRACKS},
        "commit": outcome(),
        "review_observation": {"result": "not_attempted"},
        "cleanup": outcome(),
    }
    if not check_status_archive(status_path, report):
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

    edit_url = f"{EDIT_ROOT}/{edit_id}"
    cleanup_allowed = True
    try:
        if not inspect_edit(opener, token, edit_url, report):
            return report
        if mode == "check":
            report["result"] = "checked"
            return report
        if not stage_tracks(opener, token, edit_url, report):
            return report
        cleanup_allowed = False
        try:
            status = commit_edit(opener, token, edit_url, edit_id)
        except PreflightError as error:
            report["commit"] = error_outcome(error)
            if error.http_status in {400, 401, 403, 404, 409, 412, 422}:
                cleanup_allowed = True
                report["result"] = "rejected"
                return report
            report["result"] = "commit_indeterminate"
        else:
            report["commit"] = outcome("acknowledged", status)
            report["result"] = "committed"
        report["review_observation"] = observe(opener, token)
        if report["result"] == "commit_indeterminate" and report["review_observation"]["result"] != "unconfirmed":
            report["result"] = "commit_observed"
        return report
    finally:
        if cleanup_allowed:
            report["cleanup"] = cleanup_edit(opener, token, edit_url)
            if report["cleanup"]["result"] != "deleted":
                report["failure"] = "cleanup_failed"
                if report["result"] == "checked":
                    report["result"] = "failed"


def main(argv):
    if len(argv) not in (4, 5) or argv[1] not in ("check", "submit") or (argv[1] == "check") != (len(argv) == 5):
        print("Usage: play_promote.py check STATUS REPORT APPROVAL | play_promote.py submit STATUS REPORT",
              file=sys.stderr)
        return 2
    mode = argv[1]
    report = run(mode, argv[2], os.environ.pop("PLAY_SERVICE_JSON_B64", ""))
    try:
        Path(argv[3]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if mode == "check" and report["result"] == "checked":
            Path(argv[4]).write_text(approval_text() + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play production promotion evidence.", file=sys.stderr)
        return 1
    if mode == "check":
        return 0 if report["result"] == "checked" else 1
    return 0 if report["result"] in {"committed", "commit_observed"} else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
