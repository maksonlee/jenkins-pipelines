"""Load only the reviewed signed phone bundle archived by Jenkins."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from verify_bundles import (
    BUNDLETOOL_SHA, PACKAGE, PHONE_BUNDLE_FILENAME, PHONE_VERSION_CODE,
    UPLOAD_CERTIFICATE_SHA256, VERSION_NAME,
)


class ArtifactVerificationError(Exception):
    pass


def read_archive(root, relative, limit=16_384):
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ArtifactVerificationError(f'Invalid signed archive path for {relative}')
        with path.open('rb') as file:
            data = file.read(limit + 1)
    except OSError:
        raise ArtifactVerificationError(f'Could not read signed archive file {relative}') from None
    if len(data) > limit:
        raise ArtifactVerificationError(f'Signed archive file exceeds its size limit: {relative}')
    return data


def load_phone_bundle(input_directory, source_commit, pipeline_commit, digest):
    for name, value, length in (
        ('PRODUCT_SHA', source_commit, 40),
        ('PIPELINE_SHA', pipeline_commit, 40),
        ('SIGNED_SHA256', digest, 64),
    ):
        if not isinstance(value, str) or re.fullmatch(rf'[a-f0-9]{{{length}}}', value) is None:
            raise ArtifactVerificationError(f'Invalid {name} for signed archive verification')

    try:
        if input_directory.is_symlink():
            raise ArtifactVerificationError('Invalid signed archive directory')
        root = input_directory.resolve(strict=True)
        directory = root / 'bundles'
        if directory.is_symlink() or {path.name for path in directory.iterdir()} != {'SHA256SUMS', PHONE_BUNDLE_FILENAME}:
            raise ArtifactVerificationError('Signed archive must contain exactly one phone AAB and SHA256SUMS')
    except OSError:
        raise ArtifactVerificationError('Could not read signed archive bundle directory') from None

    metadata = {
        'source-commit.txt': source_commit,
        'pipeline-commit.txt': pipeline_commit,
        'upload-certificate-sha256.txt': UPLOAD_CERTIFICATE_SHA256,
    }
    for file, expected in metadata.items():
        if read_archive(root, file).splitlines() != [expected.encode('ascii')]:
            raise ArtifactVerificationError(f'Signed archive identity verification failed: {file}')

    expected_report = {
        'result': 'passed',
        'source_commit': source_commit,
        'bundletool_sha256': BUNDLETOOL_SHA,
        'bundles': [{
            'package': PACKAGE,
            'version_name': VERSION_NAME,
            'version_code': PHONE_VERSION_CODE,
            'min_sdk': '26',
            'target_sdk': '36',
            'form_factor': 'phone',
            'sha256': digest,
            'upload_certificate_sha256': UPLOAD_CERTIFICATE_SHA256,
            'file': PHONE_BUNDLE_FILENAME,
        }],
    }
    try:
        report = json.loads(read_archive(root, 'signed-bundles.json'))
    except (ValueError, RecursionError):
        raise ArtifactVerificationError('Signed archive signature report is invalid') from None
    if report != expected_report:
        raise ArtifactVerificationError('Signed archive manifest or signature evidence does not match the reviewed release')

    expected_sums = [f'{digest}  {PHONE_BUNDLE_FILENAME}'.encode('ascii')]
    actual_sums = read_archive(root, 'bundles/SHA256SUMS').splitlines()
    if actual_sums != expected_sums:
        raise ArtifactVerificationError('Signed archive SHA256SUMS does not match the reviewed release')

    data = read_archive(root, f'bundles/{PHONE_BUNDLE_FILENAME}', limit=32 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ArtifactVerificationError('Signed archive AAB bytes do not match the reviewed release')
    return data


def main(argv):
    if len(argv) != 3:
        raise SystemExit('Usage: verify_submission_artifacts.py INPUT_DIRECTORY REPORT_PATH')
    source_commit = os.environ.get('PRODUCT_SHA', '')
    pipeline_commit = os.environ.get('PIPELINE_SHA', '')
    digest = os.environ.get('SIGNED_SHA256', '')
    build = os.environ.get('SIGNED_BUILD', '')
    try:
        if re.fullmatch(r'[1-9][0-9]{0,9}', build) is None:
            raise ArtifactVerificationError('Invalid SIGNED_BUILD for signed archive verification')
        load_phone_bundle(Path(argv[1]), source_commit, pipeline_commit, digest)
    except ArtifactVerificationError as error:
        raise SystemExit(str(error)) from None
    report = {
        'result': 'passed',
        'source_commit': source_commit,
        'source_build': int(build),
        'source_pipeline_commit': pipeline_commit,
        'package_name': PACKAGE,
        'version_name': VERSION_NAME,
        'bundle_sha256': {PHONE_VERSION_CODE: digest},
    }
    Path(argv[2]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv)
