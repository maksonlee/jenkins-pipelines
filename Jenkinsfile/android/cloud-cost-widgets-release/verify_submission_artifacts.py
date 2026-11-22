"""Load only the reviewed signed bundles archived by Jenkins build #2."""

import hashlib
import json
import sys
from pathlib import Path

from play_preflight import PACKAGE_NAME
from play_submission_preflight import BUNDLE_SHA256, SOURCE_SHA


ARCHIVE_PIPELINE_SHA = '963ba58f0e54e69ecde473808675a9121c9e7067'
UPLOAD_CERTIFICATE_SHA256 = 'e11bc3918c83d0082bf28b72f2f99bc6f2867c38559f701cf4fdce0f3702cec7'
VERSION_NAME = '1.0.5'
BUNDLE_FILES = {
    100006: ('phone', 'phone-1.0.5-100006.aab', '26', '36'),
    200006: ('wear', 'wear-1.0.5-200006.aab', '30', '35'),
}


class ArtifactVerificationError(Exception):
    pass


def read_archive(root, relative, limit=16_384):
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ArtifactVerificationError(f'Invalid build #2 archive path for {relative}')
        with path.open('rb') as file:
            data = file.read(limit + 1)
    except OSError:
        raise ArtifactVerificationError(f'Could not read build #2 archive file {relative}') from None
    if len(data) > limit:
        raise ArtifactVerificationError(f'Build #2 archive file exceeds its size limit: {relative}')
    return data


def load_bundles(input_directory):
    root = input_directory.resolve()
    metadata = {
        'source-commit.txt': SOURCE_SHA,
        'pipeline-commit.txt': ARCHIVE_PIPELINE_SHA,
        'upload-certificate-sha256.txt': UPLOAD_CERTIFICATE_SHA256,
    }
    for file, expected in metadata.items():
        if read_archive(root, file).strip() != expected.encode('ascii'):
            raise ArtifactVerificationError(f'Build #2 archive identity verification failed: {file}')

    expected_reports = [
        {
            'package': PACKAGE_NAME,
            'version_name': VERSION_NAME,
            'version_code': str(code),
            'min_sdk': minimum,
            'target_sdk': target,
            'form_factor': form,
            'sha256': BUNDLE_SHA256[code],
            'upload_certificate_sha256': UPLOAD_CERTIFICATE_SHA256,
            'file': file,
        }
        for code, (form, file, minimum, target) in BUNDLE_FILES.items()
    ]
    try:
        report = json.loads(read_archive(root, 'signed-bundles.json'))
    except (ValueError, RecursionError):
        raise ArtifactVerificationError('Build #2 signature report is invalid') from None
    if (not isinstance(report, dict) or report.get('result') != 'passed'
            or report.get('source_commit') != SOURCE_SHA or report.get('bundles') != expected_reports):
        raise ArtifactVerificationError('Build #2 bundle manifest or signature evidence does not match the reviewed release')

    expected_sums = [f'{BUNDLE_SHA256[code]}  {fields[1]}'.encode('ascii') for code, fields in BUNDLE_FILES.items()]
    actual_sums = read_archive(root, 'bundles/SHA256SUMS').splitlines()
    if sorted(actual_sums) != sorted(expected_sums):
        raise ArtifactVerificationError('Build #2 SHA256SUMS does not match the reviewed release')

    bundles = {}
    for code, (_, file, _, _) in BUNDLE_FILES.items():
        data = read_archive(root, f'bundles/{file}', limit=32 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != BUNDLE_SHA256[code]:
            raise ArtifactVerificationError(f'Build #2 AAB bytes do not match the reviewed release: {file}')
        bundles[code] = data
    return bundles


def main(argv):
    if len(argv) != 3:
        raise SystemExit('Usage: verify_submission_artifacts.py INPUT_DIRECTORY REPORT_PATH')
    try:
        load_bundles(Path(argv[1]))
    except ArtifactVerificationError as error:
        raise SystemExit(str(error)) from None
    report = {
        'result': 'passed',
        'source_commit': SOURCE_SHA,
        'source_build': 2,
        'source_pipeline_commit': ARCHIVE_PIPELINE_SHA,
        'package_name': PACKAGE_NAME,
        'version_name': VERSION_NAME,
        'bundle_sha256': BUNDLE_SHA256,
    }
    Path(argv[2]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv)
