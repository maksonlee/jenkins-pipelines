"""Inspect one uncommitted Google Play edit for the fixed 1.0.5 candidate."""

import http.client
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from play_preflight import (
    APPLICATION_URL,
    PACKAGE_NAME,
    TRACKS,
    NoRedirect,
    PreflightError,
    get_access_token,
    read_track,
    request_json,
)


SOURCE_SHA = "f6cce0cc9e7aa060b3013a447c220d149b31f80b"
BUNDLE_SHA256 = {
    100006: "1c9619931bf521d367e65cd43abeb82424ac38b35478b67a01c1efe8d14894ae",
    200006: "528bbe634cbc35475200461e91252bd4b06bf6ac4989ae440ad3eee8747cb30a",
}
PENDING_STATES = {
    "RELEASE_LIFECYCLE_STATE_UNSPECIFIED",
    "RELEASE_LIFECYCLE_STATE_DRAFT",
    "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW",
    "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
    "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED",
}
EDIT_TRACK_STATES = {"statusUnspecified", "draft", "inProgress", "halted", "completed"}
GOOGLE_ERROR_STATUSES = {
    "INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND",
    "FAILED_PRECONDITION", "ABORTED", "ALREADY_EXISTS", "RESOURCE_EXHAUSTED",
    "UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL",
}
GOOGLE_ERROR_REASONS = {
    "authError", "forbidden", "insufficientPermissions", "notFound", "accessNotConfigured",
    "applicationNotFound", "packageNotFound",
    "conflict", "duplicate", "rateLimitExceeded", "SERVICE_DISABLED",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "IAM_PERMISSION_DENIED", "CHANGES_ALREADY_IN_REVIEW",
}
MAX_ERROR_BYTES = 32_768
LOCALE_PATTERN = re.compile(r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?")


def outcome(result="not_attempted", status=None, **details):
    return {"result": result, "http_status": status, **details}


def error_outcome(error):
    return outcome(error.result, error.http_status, **error.details)


def read_google_error(response):
    try:
        body = response.read(MAX_ERROR_BYTES + 1)
        if len(body) > MAX_ERROR_BYTES:
            return {}
        content = json.loads(body)
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return {}
    error = content.get("error") if isinstance(content, dict) else None
    return error if isinstance(error, dict) else {}


def filter_google_error(error):
    filtered = {}
    status = error.get("status")
    if isinstance(status, str) and status in GOOGLE_ERROR_STATUSES:
        filtered["google_status"] = status
    reasons = set()
    for field in ("errors", "details"):
        items = error.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if field == "details" and item.get("@type") != "type.googleapis.com/google.rpc.ErrorInfo":
                continue
            reason = item.get("reason")
            if isinstance(reason, str) and reason in GOOGLE_ERROR_REASONS:
                reasons.add(reason)
    if reasons:
        filtered["google_reasons"] = sorted(reasons)
    return filtered


def google_error_details(response):
    return filter_google_error(read_google_error(response))


def edit_request(opener, token, method, url):
    headers = {"Authorization": f"Bearer {token}"}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=b"{}" if method == "POST" else None, headers=headers, method=method,
    )
    return request_json(
        opener, request, allow_no_content=method == "DELETE",
        collect_error=google_error_details if method == "POST" else None,
    )


def require_edit_id(content, status):
    edit_id = content.get("id")
    if not isinstance(edit_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", edit_id):
        raise PreflightError("invalid_edit_receipt", status)
    return edit_id


def read_live_track(opener, token, track):
    try:
        releases, status = read_track(opener, token, track)
    except PreflightError as error:
        return error_outcome(error)
    if releases is None:
        return outcome("no_content", status)
    pending = any(release["lifecycle_state"] in PENDING_STATES for release in releases)
    return outcome("pending_release" if pending else "ok", status, releases=releases)


def repeated_field(content, field, status, empty_response):
    if field not in content and content and content != empty_response:
        raise PreflightError("invalid_response", status)
    items = content.get(field, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise PreflightError("invalid_response", status)
    return items


def summarize_bundles(content, status):
    bundles = repeated_field(
        content, "bundles", status, {"kind": "androidpublisher#bundlesListResponse"},
    )
    codes = {}
    for bundle in bundles:
        code = bundle.get("versionCode")
        if type(code) is not int or not 0 < code <= 2_147_483_647 or code in codes:
            raise PreflightError("invalid_response", status)
        codes[code] = bundle.get("sha256")

    candidates = {}
    for track, code in TRACKS.items():
        expected = BUNDLE_SHA256[code]
        digest = codes.get(code)
        if code not in codes:
            comparison = "not_seen"
        elif not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            comparison = "hash_unavailable"
        elif digest.lower() != expected:
            comparison = "hash_conflict"
        else:
            comparison = "hash_matches"
        candidates[track] = {"version_code": code, "expected_sha256": expected, "result": comparison}
    conflict = any(
        candidate["result"] in {"hash_conflict", "hash_unavailable"}
        for candidate in candidates.values()
    )
    return outcome("candidate_conflict" if conflict else "ok", status, candidates=candidates)


def summarize_edit_track(content, status, track):
    if content.get("track") != track:
        raise PreflightError("invalid_response", status)
    releases = []
    for release in repeated_field(content, "releases", status, {"track": track}):
        state = release.get("status")
        codes = release.get("versionCodes", [])
        if not isinstance(state, str) or state not in EDIT_TRACK_STATES:
            raise PreflightError("invalid_response", status)
        if not isinstance(codes, list) or not all(
            isinstance(code, str) and re.fullmatch(r"[1-9][0-9]{0,9}", code)
            and int(code) <= 2_147_483_647 for code in codes
        ):
            raise PreflightError("invalid_response", status)
        releases.append({"status": state, "version_codes": sorted({int(code) for code in codes})})
    draft = any(release["status"] in {"draft", "statusUnspecified"} for release in releases)
    return outcome("pending_release" if draft else "ok", status, releases=releases)


def summarize_listings(content, status):
    locales = []
    listings = repeated_field(
        content, "listings", status, {"kind": "androidpublisher#listingsListResponse"},
    )
    for listing in listings:
        locale = listing.get("language")
        if not isinstance(locale, str) or not LOCALE_PATTERN.fullmatch(locale):
            raise PreflightError("invalid_response", status)
        text_complete = all(
            isinstance(listing.get(field), str) and listing[field].strip()
            for field in ("title", "shortDescription", "fullDescription")
        )
        locales.append({"locale": locale, "text_complete": text_complete})
    return outcome("ok", status, locales=sorted(locales, key=lambda entry: entry["locale"]))


def read_edit_section(opener, token, url, summarize, *args):
    try:
        content, status = edit_request(opener, token, "GET", url)
        return summarize(content, status, *args)
    except PreflightError as error:
        return error_outcome(error)


def run_preflight(encoded):
    report = {
        "result": "failed",
        "package_name": PACKAGE_NAME,
        "source_sha": SOURCE_SHA,
        "version_name": "1.0.5",
        "oauth": outcome(),
        "live_tracks": {track: outcome() for track in TRACKS},
        "edit_insert": outcome(),
        "bundles": outcome(),
        "edit_tracks": {track: outcome() for track in TRACKS},
        "listings": outcome(),
        "cleanup": outcome(),
    }
    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report["oauth"] = error_outcome(error)
        return report
    report["oauth"] = outcome("ok", status)
    for track in TRACKS:
        report["live_tracks"][track] = read_live_track(opener, token, track)

    live_results = {entry["result"] for entry in report["live_tracks"].values()}
    if live_results - {"ok", "no_content", "pending_release"}:
        return report
    if "pending_release" in live_results:
        report["result"] = "blocked"
        return report

    edits_url = f"{APPLICATION_URL}/edits"
    try:
        receipt, status = edit_request(opener, token, "POST", edits_url)
        edit_id = require_edit_id(receipt, status)
    except PreflightError as error:
        report["edit_insert"] = error_outcome(error)
        return report
    report["edit_insert"] = outcome("ok", status)

    edit_url = f"{edits_url}/{edit_id}"
    try:
        report["bundles"] = read_edit_section(
            opener, token, f"{edit_url}/bundles", summarize_bundles,
        )
        for track in TRACKS:
            report["edit_tracks"][track] = read_edit_section(
                opener, token, f"{edit_url}/tracks/{track}", summarize_edit_track, track,
            )
        report["listings"] = read_edit_section(
            opener, token, f"{edit_url}/listings", summarize_listings,
        )
    finally:
        try:
            content, status = edit_request(opener, token, "DELETE", edit_url)
            if content:
                raise PreflightError("invalid_response", status)
        except PreflightError as error:
            report["cleanup"] = error_outcome(error)
        else:
            report["cleanup"] = outcome("ok", status)

    readback_results = {
        report["bundles"]["result"], report["listings"]["result"],
        *(entry["result"] for entry in report["edit_tracks"].values()),
    }
    if report["cleanup"]["result"] != "ok":
        report["failure"] = "cleanup_failed"
    elif readback_results == {"ok"}:
        report["result"] = "passed"
    elif readback_results <= {"ok", "candidate_conflict", "pending_release"}:
        report["result"] = "blocked"
    return report


def main(argv):
    if len(argv) != 2:
        print("Usage: play_submission_preflight.py REPORT_PATH", file=sys.stderr)
        return 2
    report = run_preflight(os.environ.pop("PLAY_SERVICE_JSON_B64", ""))
    try:
        Path(argv[1]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play submission preflight evidence.", file=sys.stderr)
        return 1
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
