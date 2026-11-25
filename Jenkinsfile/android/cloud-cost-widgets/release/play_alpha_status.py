"""Observe Cloud Spend for AWS track lifecycles through the Play read-only API."""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from play_preflight import PACKAGE_NAME, NoRedirect, PreflightError, get_access_token
from play_submission_preflight import error_outcome, filter_google_error, outcome, read_google_error, read_live_track


# The tracks that hold a release. Play limits this lifecycle read far below the documented API quota: about a
# dozen reads of one app within a few minutes are answered with 403 "Listing releases quota exceeded." for up to
# half an hour (observed 2026-11-24 and 25; the limit is not documented). Reading only these three keeps a release
# to a handful of reads. The empty production tracks are covered by the publisher's exact edit inventory instead.
TRACK_NAMES = ('alpha', 'internal', 'wear:internal')
QUOTA_REASONS = {
    'dailyLimitExceeded', 'quotaExceeded', 'rateLimitExceeded', 'userRateLimitExceeded',
    'QUOTA_EXCEEDED', 'RATE_LIMIT_EXCEEDED',
}


def lifecycle_error(response):
    error = read_google_error(response)
    safe_details = filter_google_error(error)
    message = error.get('message')
    if isinstance(message, str) and 'listing releases quota exceeded' in message.casefold():
        safe_details['quota'] = 'listing_releases_quota_exceeded'
    reasons = {
        item['reason']
        for field in ('errors', 'details')
        if isinstance(error.get(field), list)
        for item in error[field]
        if isinstance(item, dict) and isinstance(item.get('reason'), str) and item['reason'] in QUOTA_REASONS
    }
    if reasons:
        safe_details['quota_reasons'] = sorted(reasons)
    return safe_details


def read_status(encoded):
    report = {'package_name': PACKAGE_NAME, 'result': 'failed', 'oauth': outcome(), 'tracks': {}}
    opener = urllib.request.build_opener(NoRedirect())
    try:
        token, status = get_access_token(opener, encoded)
    except PreflightError as error:
        report['oauth'] = error_outcome(error)
        return report
    report['oauth'] = outcome('ok', status)
    report['observed_at'] = datetime.now(timezone.utc).isoformat()
    report['tracks'] = {track: read_live_track(opener, token, track, collect_error=lifecycle_error) for track in TRACK_NAMES}
    quota_limited = False
    for track in report['tracks'].values():
        if track['http_status'] == 200 and track['result'] in {'ok', 'pending_release'}:
            continue
        if track['http_status'] == 204 and track['result'] == 'no_content':
            continue
        if track['http_status'] == 403 and track.get('quota') == 'listing_releases_quota_exceeded':
            quota_limited = True
            continue
        return report
    report['result'] = 'quota_limited' if quota_limited else 'checked'
    return report


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: play_alpha_status.py REPORT_PATH')
    report = read_status(os.environ.pop('PLAY_SERVICE_JSON_B64', ''))
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['result'] in {'checked', 'quota_limited'} else 1


if __name__ == '__main__':
    sys.exit(main())
