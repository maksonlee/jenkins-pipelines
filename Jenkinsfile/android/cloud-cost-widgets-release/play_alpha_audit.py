"""Read back the phone Alpha candidate in one disposable Play edit."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from play_preflight import PACKAGE_NAME, NoRedirect, PreflightError, get_access_token
from play_publish import EDIT_ROOT, PHONE_ALPHA, PHONE_CANDIDATE, RELEASE_NOTES, VERSION_NAME, cleanup_edit
from play_submission_preflight import (
    edit_request, error_outcome, outcome, read_edit_section, require_edit_id, summarize_bundles, summarize_edit_track,
)


def summarize_alpha(content, status):
    snapshot = summarize_edit_track(content, status, PHONE_ALPHA)
    expected = [{"status": "completed", "version_codes": [PHONE_CANDIDATE]}]
    if (snapshot["releases"] != expected
            or content["releases"][0]["versionCodes"] != [str(PHONE_CANDIDATE)]):
        snapshot["result"] = "unexpected_release"
        return snapshot

    notes = content["releases"][0].get("releaseNotes", [])
    if not isinstance(notes, list):
        raise PreflightError("invalid_response", status)
    texts = {}
    for note in notes:
        if not isinstance(note, dict):
            raise PreflightError("invalid_response", status)
        language = note.get("language")
        text = note.get("text", "")
        if not isinstance(language, str) or not isinstance(text, str) or language in texts:
            raise PreflightError("invalid_response", status)
        texts[language] = text

    comparisons = {}
    for note in RELEASE_NOTES:
        language = note["language"]
        if language not in texts:
            comparisons[language] = "absent"
        else:
            comparisons[language] = "matches" if texts[language] == note["text"] else "text_mismatch"
    snapshot["release_notes"] = comparisons
    snapshot["result"] = "release_notes_mismatch" if "text_mismatch" in comparisons.values() else "candidate_observed"
    return snapshot


def run_audit(encoded, signed_digest):
    report = {
        "result": "unconfirmed",
        "package_name": PACKAGE_NAME,
        "track": PHONE_ALPHA,
        "version_name": VERSION_NAME,
        "version_code": PHONE_CANDIDATE,
        "public_lifecycle": outcome("not_verified"),
        "oauth": outcome(),
        "edit_insert": outcome(),
        "alpha_track": outcome(),
        "bundle_catalog": outcome(),
        "cleanup": outcome(),
    }
    if not isinstance(signed_digest, str) or re.fullmatch(r"[0-9a-f]{64}", signed_digest) is None:
        report["result"] = "invalid_signed_sha256"
        return report
    report["signed_sha256"] = signed_digest

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

    try:
        report["alpha_track"] = read_edit_section(
            opener, token, f"{edit_url}/tracks/{PHONE_ALPHA}", summarize_alpha,
        )
        report["bundle_catalog"] = read_edit_section(
            opener, token, f"{edit_url}/bundles", summarize_bundles,
            {PHONE_ALPHA: PHONE_CANDIDATE}, {PHONE_CANDIDATE: signed_digest},
        )
        candidate = report["bundle_catalog"].get("candidates", {}).get(PHONE_ALPHA, {})
        if report["alpha_track"]["result"] == "candidate_observed" and candidate.get("result") == "hash_matches":
            report["result"] = "observed_in_edit"
    finally:
        report["cleanup"] = cleanup_edit(opener, token, edit_url)
        if report["cleanup"]["result"] != "deleted":
            report["result"] = "cleanup_failed"
    return report


def main(argv):
    if len(argv) != 2:
        print("Usage: play_alpha_audit.py REPORT_PATH", file=sys.stderr)
        return 2
    report = run_audit(os.environ.pop("PLAY_SERVICE_JSON_B64", ""), os.environ.get("SIGNED_SHA256", ""))
    try:
        Path(argv[1]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play Alpha audit evidence.", file=sys.stderr)
        return 1
    return 0 if report["result"] == "observed_in_edit" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
