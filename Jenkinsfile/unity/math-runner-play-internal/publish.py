#!/usr/bin/env python3
"""Verify one Jenkins Math Runner AAB, then publish it only on explicit invocation.

The fetch command is the replaceable artifact acquisition adapter. All other
commands operate on pinned, verified local bytes. No Unity or upload JKS input.
"""

import base64
import fcntl
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


PACKAGE = "com.maksonlee.mathrunner"
SOURCE_JOB = "/job/unity/job/math-runner-android-build-in-docker"
PLAY_ORIGIN = "https://androidpublisher.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SIGNER_SHA256 = "E11BC3918C83D0082BF28B72F2F99BC6F2867C38559F701CF4FDCE0F3702CEC7"
ARTIFACT_PATHS = (
    "Builds/Release/Android/SportsDayMath.aab",
    "Builds/Release/Android/SportsDayMath.aab.sha256",
    "Builds/Release/Android/SportsDayMath.aab.signer-sha256",
    "Builds/Release/Android/release-verification.json",
    "Builds/Release/Android/AndroidManifest.xml",
    "Builds/Release/Android/signature-verification.txt",
    "Evidence/Checks/Builds/PlayAab/Android-AndroidBuildReport.json",
)
PLAY_LOCK = pathlib.Path("/home/administrator/jenkins/play/mathrunner-publish.lock")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class PublishError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise PublishError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_selection(build_number, expected_sha, release_status=None):
    require(re.fullmatch(r"[1-9][0-9]{0,7}", build_number or ""), "Invalid source build number")
    require(SHA256.fullmatch(expected_sha or ""), "Expected AAB SHA-256 must be 64 lowercase hex characters")
    if release_status is not None:
        require(release_status in ("draft", "completed"), "Release status must be draft or completed")
    return int(build_number)


def validate_replacement(replace_code, replace_sha, release_status, target_code):
    require(bool(replace_code) == bool(replace_sha),
            "Previous draft code and SHA-256 must be provided together")
    if not replace_code:
        return None
    require(re.fullmatch(r"[1-9][0-9]{0,7}", replace_code),
            "Previous draft versionCode must be a positive integer")
    require(SHA256.fullmatch(replace_sha),
            "Previous draft SHA-256 must be 64 lowercase hex characters")
    require(release_status == "draft", "A draft replacement cannot complete a release")
    previous = int(replace_code)
    require(previous < int(target_code), "Replacement draft versionCode must increase")
    return previous


def safe_error(error):
    try:
        document = json.loads(error.read(4096))
        detail = document.get("error", {})
        if isinstance(detail, dict):
            status = detail.get("status")
            message = detail.get("message", "HTTP error")
        else:
            status, message = None, str(detail)
    except (ValueError, AttributeError):
        status, message = None, "non-JSON HTTP error"
    message = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", str(message))
    message = re.sub(r"[A-Za-z0-9_./+=-]{40,}", "[redacted]", message)
    return f"HTTP {error.code} status={status or 'unknown'} message={message[:180]}"


def source_build_url(build_number):
    return f"https://jenkins.maksonlee.com{SOURCE_JOB}/{build_number}/"


def verify_archive_signer(aab_path):
    signature = subprocess.run(
        ["jarsigner", "-verify", "-strict", str(aab_path)],
        capture_output=True, text=True, timeout=120,
    )
    report = signature.stdout + signature.stderr
    lower = report.lower()
    require(not any(word in lower for word in ("unsigned entries", "treated as unsigned", "jar is unsigned")),
            "Copied AAB has unsigned entries")
    if signature.returncode == 0:
        require("jar verified." in lower, "Copied AAB JAR verification was not confirmed")
    elif signature.returncode == 4:
        require("jar verified, with signer errors." in lower and "Error:" in report,
                "Copied AAB has unexpected JAR signature errors")
        errors = report.split("Error:", 1)[1].split("Warning:", 1)[0].strip().splitlines()
        allowed = (
            "This jar contains entries whose certificate chain is invalid. Reason: PKIX path building failed:",
            "This jar contains entries whose signer certificate is self-signed.",
        )
        require(errors and all(any(line.strip().startswith(item) for item in allowed) for line in errors),
                "Copied AAB JAR errors exceed the expected self-signed certificate")
    else:
        raise PublishError(f"Copied AAB JAR strict verification exit {signature.returncode}")
    certificate = subprocess.run(
        ["keytool", "-printcert", "-jarfile", str(aab_path)],
        capture_output=True, text=True, timeout=60,
    )
    require(certificate.returncode == 0, "Could not read copied AAB signer certificate")
    matches = re.findall(r"(?m)^\s*SHA256:\s*([0-9A-Fa-f:]{95})\s*$", certificate.stdout)
    require(len(matches) == 1, "Copied AAB must have exactly one public signer fingerprint")
    fingerprint = matches[0].replace(":", "").upper()
    require(fingerprint == SIGNER_SHA256, "Copied AAB signer differs from pinned upload certificate")


def check_artifacts(files, expected_sha, build_number, aab_path, signer_check=verify_archive_signer):
    aab = files["SportsDayMath.aab"]
    actual_sha = digest(aab)
    require(aab and actual_sha == expected_sha, "AAB bytes differ from expected SHA-256")
    sidecar = files["SportsDayMath.aab.sha256"].decode().strip()
    require(sidecar == f"{actual_sha}  SportsDayMath.aab", "Archived AAB checksum sidecar mismatch")
    signer = files["SportsDayMath.aab.signer-sha256"].decode().strip()
    require(signer == SIGNER_SHA256, "Archived public upload signer differs from pinned certificate")
    report = json.loads(files["release-verification.json"])
    require(report.get("status") == "passed", "Source release report did not pass")
    require(FULL_SHA.fullmatch(str(report.get("source_commit") or "")), "Source report lacks a full Git commit SHA")
    require(report.get("package") == PACKAGE, "Source package mismatch")
    require(re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", str(report.get("version_name") or "")),
            "Source report versionName is invalid")
    require(isinstance(report.get("version_code"), int) and 1 <= report["version_code"] <= 2100000000,
            "Source report versionCode is invalid")
    require(report.get("aab_sha256") == actual_sha, "Source release report AAB hash mismatch")
    require(report.get("signer_sha256") == SIGNER_SHA256, "Source release report signer mismatch")
    require(report.get("abis") == ["arm64-v8a"], "Source ABI report mismatch")
    require(report.get("license_lifecycle") == "persistent-personal", "Source license lifecycle mismatch")
    manifest = ET.fromstring(files["AndroidManifest.xml"])
    android = "{http://schemas.android.com/apk/res/android}"
    require(manifest.attrib.get("package") == PACKAGE, "Archived manifest package mismatch")
    require(manifest.attrib.get(android + "versionName") == report["version_name"], "Archived manifest versionName mismatch")
    require(manifest.attrib.get(android + "versionCode") == str(report["version_code"]), "Archived manifest versionCode mismatch")
    signature = files["signature-verification.txt"].decode(errors="replace").lower()
    require("jar verified" in signature and "unsigned entries" not in signature and "treated as unsigned" not in signature,
            "Source signature report is not acceptable")
    build_report = json.loads(files["Android-AndroidBuildReport.json"])
    require(build_report.get("result") == "Succeeded" and build_report.get("errors") == 0 and build_report.get("target") == "Android",
            "Source Unity BuildReport is not successful")
    import io
    with zipfile.ZipFile(io.BytesIO(aab)) as bundle:
        names = set(bundle.namelist())
        abis = sorted({item.split("/")[2] for item in names if item.startswith("base/lib/") and item.endswith(".so")})
        require(abis == ["arm64-v8a"], "AAB contains another native ABI")
        require({"base/lib/arm64-v8a/libunity.so", "base/lib/arm64-v8a/libil2cpp.so"}.issubset(names),
                "AAB lacks Unity ARM64 IL2CPP libraries")
    signer_check(aab_path)
    return {
        "status": "verified",
        "source_job": SOURCE_JOB,
        "source_build_number": build_number,
        "source_build_url": source_build_url(build_number),
        "source_commit": report["source_commit"],
        "package": PACKAGE,
        "version_name": report["version_name"],
        "version_code": int(report["version_code"]),
        "aab_sha256": actual_sha,
        "aab_bytes": len(aab),
        "signer_sha256": SIGNER_SHA256,
        "fresh_jar_signature_and_signer_check": "passed",
        "image": report.get("image"),
        "unity_cli_version": report.get("unity_cli_version"),
    }


def write_exclusive(path, data):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)


def verify(build_text, expected_sha, output):
    build_number = validate_selection(build_text, expected_sha)
    output = pathlib.Path(output)
    require(output.is_dir() and not output.is_symlink(), "Copied artifact directory is unavailable or linked")
    expected_names = {pathlib.PurePosixPath(path).name for path in ARTIFACT_PATHS}
    names = {item.name for item in output.iterdir()}
    require(names == expected_names, "Copied artifact inventory differs from seven source artifacts")
    files = {}
    for name in expected_names:
        path = output / name
        require(path.is_file() and not path.is_symlink(), "Copied artifact is not a regular file")
        require(path.stat().st_size <= (120 * 1024 * 1024 if name.endswith(".aab") else 2 * 1024 * 1024),
                "Copied artifact exceeds bounded size")
        files[name] = path.read_bytes()
    verified = check_artifacts(files, expected_sha, build_number, output / "SportsDayMath.aab")
    write_exclusive(output / "verified.json", (json.dumps(verified, indent=2) + "\n").encode())
    print("Verified copied source archive: " + f"build={build_number} source={verified['source_commit']} "
          + f"version={verified['version_name']} ({verified['version_code']}) "
          + f"sha256={verified['aab_sha256']}")


def load_verified(path, expected_sha):
    report = json.loads(pathlib.Path(path).read_text())
    require(report.get("status") == "verified", "Local artifact report is not verified")
    require(report.get("source_job") == SOURCE_JOB, "Local artifact source job mismatch")
    require(report.get("package") == PACKAGE and report.get("signer_sha256") == SIGNER_SHA256,
            "Local package/signer contract mismatch")
    require(report.get("aab_sha256") == expected_sha, "Local expected AAB hash mismatch")
    aab = pathlib.Path(path).with_name("SportsDayMath.aab")
    require(aab.is_file() and digest(aab.read_bytes()) == expected_sha, "Local AAB bytes changed after preflight")
    require(aab.stat().st_size == report.get("aab_bytes"), "Local AAB size changed")
    return report, aab


def describe(path, expected_sha, release_status, replace_code, replace_sha):
    report, _ = load_verified(path, expected_sha)
    validate_selection(str(report["source_build_number"]), expected_sha, release_status)
    previous = validate_replacement(replace_code, replace_sha, release_status, report["version_code"])
    replacement = (f"replace existing internal draft code {previous} with bundle SHA-256 {replace_sha}; "
                   if previous is not None else "no previous draft replacement; ")
    print(f"source build #{report['source_build_number']}, commit {report['source_commit']}, "
          f"{PACKAGE} {report['version_name']} (code {report['version_code']}), "
          f"AAB SHA-256 {report['aab_sha256']}, signer {SIGNER_SHA256}; "
          f"internal {release_status}; {replacement}"
          + ("draft is not served to testers" if release_status == "draft"
             else "completed can reach internal testers"))


def service_token():
    encoded = os.environ.get("PLAY_SERVICE_JSON_B64", "")
    require(encoded, "Shared Play service JSON is unavailable")
    if encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    service = json.loads(base64.b64decode(re.sub(r"\s+", "", encoded), validate=True))
    require(service.get("type") == "service_account" and service.get("client_email") and service.get("private_key"),
            "Shared Play service JSON is invalid")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": service["client_email"],
        "scope": "https://www.googleapis.com/auth/androidpublisher",
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signing_input = encode(header) + "." + encode(claims)
    private_key = serialization.load_pem_private_key(service["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    assertion = signing_input + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            token = json.load(response).get("access_token")
    except urllib.error.HTTPError as error:
        raise PublishError("Google OAuth " + safe_error(error)) from None
    except (urllib.error.URLError, TimeoutError):
        raise PublishError("Google OAuth network result is unknown; do not retry automatically") from None
    require(token, "Google OAuth returned no access token")
    return token


class PlayClient:
    def __init__(self, token, attempt):
        self.token = token
        self.attempt = attempt
        self.base = PLAY_ORIGIN + "/androidpublisher/v3/applications/" + urllib.parse.quote(PACKAGE, safe="")
        self.upload_base = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/" + urllib.parse.quote(PACKAGE, safe="")

    def request(self, method, path, data=None, *, upload=False, operation):
        base = self.upload_base if upload else self.base
        url = base + path
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if isinstance(data, dict):
            data = json.dumps(data, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=300 if upload else 30) as response:
                body = response.read(2 * 1024 * 1024)
                self.attempt["api_events"].append({"operation": operation, "http": response.status})
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            self.attempt["api_events"].append({"operation": operation, "http": error.code})
            raise PublishError(f"Play {method} {path} " + safe_error(error)) from None
        except (urllib.error.URLError, TimeoutError):
            self.attempt["api_events"].append({"operation": operation, "http": None})
            raise PublishError(f"Play {operation} network result is unknown; do not retry automatically") from None


def internal_releases(track, target_code, release_status, version_name, previous=None):
    require(track.get("track") == "internal", "Play internal track response mismatch")
    retained = []
    previous_seen = 0
    for release in track.get("releases", []):
        codes = {str(code) for code in release.get("versionCodes", [])}
        require(len(codes) == 1 and release.get("status") in ("draft", "completed"),
                "Internal track has a complex release; manual decision required")
        code = next(iter(codes))
        require(code.isdecimal(), "Internal track has a nonnumeric versionCode")
        if code == target_code:
            require(previous is None, "Target release already exists while replacing another draft")
            require(not (release["status"] == "completed" and release_status == "draft"),
                    "Refusing to downgrade completed internal release to draft")
            continue
        if previous is not None and int(code) == previous:
            require(release["status"] == "draft", "Expected previous internal release is not draft")
            previous_seen += 1
            require(previous_seen == 1, "Duplicate previous draft release")
            continue
        require(release["status"] == "completed", "Unrelated draft internal release requires manual decision")
        require(int(code) < int(target_code), "A newer internal release exists")
        if release_status == "draft":
            retained.append(release)
    if previous is not None:
        require(previous_seen == 1, "Expected previous internal draft is missing")
    notes = [{"language": "en-US", "text": f"Math Runner {version_name} internal test build."}]
    retained.append({"versionCodes": [target_code], "status": release_status, "releaseNotes": notes})
    return retained


def publish_transaction(client, report, aab, release_status, attempt,
                        replace_code="", replace_sha=""):
    validate_selection(str(report["source_build_number"]), report["aab_sha256"], release_status)
    previous = validate_replacement(replace_code, replace_sha, release_status, report["version_code"])
    attempt["replaced_draft_version_code"] = previous
    attempt["replaced_draft_sha256"] = replace_sha if previous is not None else None
    attempt["stage"] = "edit_insert"
    edit = client.request("POST", "/edits", {}, operation="edit_insert")
    edit_id = edit.get("id")
    require(edit_id, "Play returned no edit ID")
    prefix = "/edits/" + urllib.parse.quote(edit_id, safe="")
    committed = False
    attempt["edit_created"] = True
    try:
        attempt["stage"] = "bundles_list"
        bundles = client.request("GET", prefix + "/bundles", operation="bundles_list").get("bundles", [])
        target_code = str(report["version_code"])
        require(all(str(bundle.get("versionCode", "")).isdecimal() for bundle in bundles),
                "Play bundles include an unrecognized versionCode")
        existing = [bundle for bundle in bundles if str(bundle.get("versionCode")) == target_code]
        require(len(existing) <= 1, "Duplicate target versionCode in Play bundles")
        if previous is not None:
            old = [bundle for bundle in bundles if int(bundle["versionCode"]) == previous]
            require(len(old) == 1 and old[0].get("sha256", "").lower() == replace_sha,
                    "Previous Play draft bundle SHA-256 does not match approval")
        higher_codes = [int(bundle["versionCode"]) for bundle in bundles
                        if str(bundle.get("versionCode", "")).isdecimal()
                        and int(bundle["versionCode"]) > int(target_code)]
        require(not higher_codes, "A newer Play bundle versionCode exists")
        if existing:
            require(existing[0].get("sha256", "").lower() == report["aab_sha256"],
                    "Existing Play versionCode lacks matching bundle SHA-256")
            action = "reused exact Play bundle"
        else:
            action = "uploaded exact AAB once"
        attempt["stage"] = "track_preflight"
        track = client.request("GET", prefix + "/tracks/internal", operation="track_get")
        releases = internal_releases(track, target_code, release_status, report["version_name"], previous)
        if not existing:
            attempt["stage"] = "bundle_upload"
            attempt["upload_attempted"] = True
            uploaded = client.request("POST", prefix + "/bundles?uploadType=media", aab,
                                      upload=True, operation="bundle_upload")
            attempt["upload_response_confirmed"] = True
            require(str(uploaded.get("versionCode")) == target_code, "Play upload returned another versionCode")
            require(uploaded.get("sha256", "").lower() == report["aab_sha256"],
                    "Play upload returned another bundle SHA-256")
        attempt["stage"] = "track_update"
        client.request("PUT", prefix + "/tracks/internal", {
            "track": "internal",
            "releases": releases,
        }, operation="track_update")
        attempt["stage"] = "edit_commit"
        attempt["commit_attempted"] = True
        client.request("POST", prefix + ":commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW",
                       operation="edit_commit")
        attempt["commit_response_confirmed"] = True
        committed = True
        return {"status": "committed", "action": action, "track": "internal",
                "release_status": release_status, "version_code": report["version_code"],
                "aab_sha256": report["aab_sha256"], "source_build_number": report["source_build_number"],
                "replaced_draft_version_code": previous,
                "replaced_draft_sha256": replace_sha if previous is not None else None}
    finally:
        if not committed:
            attempt["failure_stage"] = attempt["stage"]
            try:
                attempt["stage"] = "edit_delete"
                client.request("DELETE", prefix, operation="edit_delete")
                attempt["edit_delete_confirmed"] = True
            except Exception:
                # A timed-out commit may already have landed. Never retry it.
                attempt["edit_delete_confirmed"] = False
                print("Could not confirm deletion of this edit; inspect Play state before any new run.", file=sys.stderr)


def publish(report_path, expected_sha, release_status, replace_code, replace_sha):
    path = pathlib.Path(report_path)
    attempt = {
        "status": "started", "stage": "artifact_recheck", "api_events": [],
        "upload_attempted": False, "upload_response_confirmed": False,
        "commit_attempted": False, "commit_response_confirmed": False,
        "edit_created": False, "edit_delete_confirmed": None,
    }
    descriptor = None
    try:
        report, aab = load_verified(report_path, expected_sha)
        validate_selection(str(report["source_build_number"]), expected_sha, release_status)
        validate_replacement(replace_code, replace_sha, release_status, report["version_code"])
        PLAY_LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(PLAY_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublishError("Another Math Runner Play edit is in progress") from None
        attempt["stage"] = "oauth"
        client = PlayClient(service_token(), attempt)
        result = publish_transaction(client, report, aab.read_bytes(), release_status, attempt,
                                     replace_code, replace_sha)
        output = path.with_name("publish-result.json")
        write_exclusive(output, (json.dumps(result, indent=2) + "\n").encode())
        attempt["status"] = "committed"
        attempt["stage"] = "complete"
        print(f"Committed Play internal {release_status}: source build #{report['source_build_number']} "
              f"versionCode={report['version_code']} AAB SHA-256={report['aab_sha256']}")
    except Exception as error:
        attempt["status"] = "failed_or_uncertain"
        attempt["error_class"] = type(error).__name__
        attempt["error_summary"] = str(error)[:240] if isinstance(error, PublishError) else "non-API local failure"
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if path.parent.is_dir():
            try:
                write_exclusive(path.with_name("publish-attempt.json"),
                                (json.dumps(attempt, indent=2) + "\n").encode())
            except OSError:
                print("Could not retain sanitized publish attempt record.", file=sys.stderr)


def main(argv):
    os.umask(0o077)
    require(argv, "Usage: publish.py verify|describe|publish ...")
    if argv[0] == "verify" and len(argv) == 4:
        verify(argv[1], argv[2], argv[3])
    elif argv[0] == "describe" and len(argv) == 6:
        describe(argv[1], argv[2], argv[3], argv[4], argv[5])
    elif argv[0] == "publish" and len(argv) == 6:
        publish(argv[1], argv[2], argv[3], argv[4], argv[5])
    else:
        raise PublishError("Usage: publish.py verify BUILD SHA DIR | describe REPORT SHA STATUS OLD_CODE OLD_SHA | publish REPORT SHA STATUS OLD_CODE OLD_SHA")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (PublishError, ValueError, KeyError, json.JSONDecodeError, ET.ParseError, zipfile.BadZipFile) as error:
        print("Math Runner publisher refused: " + str(error), file=sys.stderr)
        sys.exit(1)
