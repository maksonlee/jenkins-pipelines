"""Observe Cloud Spend for AWS track lifecycles through the Play read-only API."""

import json
import os
import sys
import urllib.request
from pathlib import Path

from play_preflight import PACKAGE_NAME, NoRedirect, PreflightError, get_access_token
from play_submission_preflight import error_outcome, outcome, read_live_track


TRACK_NAMES = ('alpha', 'internal', 'production', 'wear:internal', 'wear:production')


def read_status(encoded):
    report = {'package_name': PACKAGE_NAME, 'result': 'failed', 'oauth': outcome(), 'tracks': {}}
    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report['oauth'] = error_outcome(error)
        return report
    report['oauth'] = outcome('ok', status)
    report['tracks'] = {track: read_live_track(opener, token, track) for track in TRACK_NAMES}
    alpha = report['tracks']['alpha']
    if alpha['http_status'] in {200, 204} and alpha['result'] in {'ok', 'pending_release', 'no_content'}:
        report['result'] = 'checked'
    return report


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: play_alpha_status.py REPORT_PATH')
    report = read_status(os.environ.pop('PLAY_SERVICE_JSON_B64', ''))
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['result'] == 'checked' else 1


if __name__ == '__main__':
    sys.exit(main())
