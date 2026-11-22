"""Read the Cloud Cost Widgets production tracks and write sanitized evidence."""

import base64
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PACKAGE_NAME = "com.maksonlee.cloudcost.aws"
TRACKS = {"production": 100006, "wear:production": 200006}
TOKEN_URL = "https://oauth2.googleapis.com/token"
APPLICATION_URL = (
    "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
    f"{PACKAGE_NAME}"
)
ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
HTTP_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1_048_576
RELEASE_STATES = {
    "RELEASE_LIFECYCLE_STATE_UNSPECIFIED",
    "RELEASE_LIFECYCLE_STATE_DRAFT",
    "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW",
    "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
    "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED",
    "RELEASE_LIFECYCLE_STATE_NOT_APPROVED",
    "RELEASE_LIFECYCLE_STATE_PUBLISHED",
}


class PreflightError(Exception):
    def __init__(self, result, http_status=None, details=None):
        super().__init__(result)
        self.result = result
        self.http_status = http_status
        self.details = details or {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def read_service_account(encoded):
    if not encoded.strip():
        raise PreflightError("missing_credentials")
    encoded = encoded.strip()
    if encoded.startswith("data:"):
        header, _, encoded = encoded.partition(",")
        if not header.endswith(";base64"):
            raise PreflightError("invalid_credentials")
    try:
        service = json.loads(base64.b64decode("".join(encoded.split()), validate=True))
    except (ValueError, RecursionError):
        raise PreflightError("invalid_credentials") from None
    if not isinstance(service, dict) or service.get("type", "service_account") != "service_account":
        raise PreflightError("invalid_credentials")
    for field in ("client_email", "private_key"):
        if not isinstance(service.get(field), str) or not service[field].strip():
            raise PreflightError("invalid_credentials")
    return service


def base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def sign_assertion(service):
    now = int(time.time())
    claims = {
        "iss": service["client_email"],
        "scope": ANDROID_PUBLISHER_SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    header = base64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = base64url(json.dumps(claims, separators=(",", ":")).encode("ascii"))
    signing_input = header + b"." + payload
    try:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "service-account.pem"
            key_file.write_text(service["private_key"], encoding="utf-8")
            signature = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(key_file)],
                input=signing_input,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=True,
            ).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        raise PreflightError("signing_failed") from None
    return signing_input + b"." + base64url(signature)


def request_json(
    opener, request, *, allow_no_content=False, collect_error=None,
    timeout=HTTP_TIMEOUT_SECONDS,
):
    status = None
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            if status == 204 and allow_no_content:
                return None, status
            if status != 200:
                raise PreflightError("unexpected_http_status", status)
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        with error:
            details = collect_error(error) if collect_error else None
        result = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
            status, "http_error"
        )
        if 300 <= status < 400:
            result = "redirect_blocked"
        raise PreflightError(result, status, details) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise PreflightError("request_failed", status) from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise PreflightError("response_too_large", status)
    try:
        content = json.loads(body)
    except (ValueError, RecursionError):
        raise PreflightError("invalid_response", status) from None
    if not isinstance(content, dict) or "error" in content:
        raise PreflightError("invalid_response", status)
    return content, status


def get_access_token(opener, encoded):
    assertion = sign_assertion(read_service_account(encoded))
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    content, status = request_json(opener, request)
    token = content.get("access_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
        raise PreflightError("invalid_response", status)
    return token, status


def read_track(opener, token, track):
    request = urllib.request.Request(
        f"{APPLICATION_URL}/tracks/{track}/releases",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    content, status = request_json(opener, request, allow_no_content=True)
    if content is None:
        return None, status
    # The API may omit the repeated releases field when the track has no releases.
    if content and "releases" not in content:
        raise PreflightError("invalid_response", status)
    releases = content.get("releases", [])
    if not isinstance(releases, list) or len(releases) > 20:
        raise PreflightError("invalid_response", status)

    summaries = []
    for release in releases:
        if not isinstance(release, dict) or release.get("track", track) != track:
            raise PreflightError("invalid_response", status)
        state = release.get("releaseLifecycleState")
        if not isinstance(state, str) or state not in RELEASE_STATES:
            raise PreflightError("invalid_response", status)
        artifacts = release.get("activeArtifacts", [])
        if not isinstance(artifacts, list):
            raise PreflightError("invalid_response", status)
        codes = []
        for artifact in artifacts:
            code = artifact.get("versionCode") if isinstance(artifact, dict) else None
            if type(code) is not int or not 0 < code <= 2_147_483_647:
                raise PreflightError("invalid_response", status)
            codes.append(code)
        summaries.append({"lifecycle_state": state, "version_codes": sorted(set(codes))})
    return summaries, status


def run_preflight(encoded):
    report = {
        "package_name": PACKAGE_NAME,
        "result": "failed",
        "oauth": {"result": "not_attempted", "http_status": None},
        "tracks": {
            track: {
                "expected_version_code": version,
                "result": "not_attempted",
                "http_status": None,
            }
            for track, version in TRACKS.items()
        },
    }
    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report["oauth"].update(result=error.result, http_status=error.http_status)
        return report
    report["oauth"].update(result="ok", http_status=status)

    for track, entry in report["tracks"].items():
        try:
            releases, status = read_track(opener, token, track)
        except PreflightError as error:
            entry.update(result=error.result, http_status=error.http_status)
        else:
            if releases is None:
                entry.update(result="no_content", http_status=status)
                continue
            entry.update(
                result="ok",
                http_status=status,
                releases=releases,
                expected_version_code_visible=any(
                    entry["expected_version_code"] in release["version_codes"]
                    for release in releases
                ),
            )

    track_results = {entry["result"] for entry in report["tracks"].values()}
    if track_results == {"ok"}:
        report["result"] = "passed"
    elif track_results <= {"ok", "no_content"}:
        report["result"] = "indeterminate"
    return report


def main(argv):
    if len(argv) != 2:
        print("Usage: play_preflight.py REPORT_PATH", file=sys.stderr)
        return 2
    report = run_preflight(os.environ.pop("PLAY_SERVICE_JSON_B64", ""))
    try:
        Path(argv[1]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Could not write Google Play preflight evidence.", file=sys.stderr)
        return 1
    return {"passed": 0, "failed": 1, "indeterminate": 3}[report["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
