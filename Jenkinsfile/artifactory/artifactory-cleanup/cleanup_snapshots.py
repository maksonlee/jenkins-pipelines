"""Delete old version folders from the Artifactory android-snapshots repository.

Every app under com/maksonlee keeps its newest --keep version folders, ordered by the newest file in each folder.
math-runner is never touched: its per-build folders are part of the Unity release evidence. Paths outside
com/maksonlee/<app>/<version>-SNAPSHOT are reported and never deleted. Without --delete the script only prints and
records the plan.
"""

import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

ARTIFACTORY_URL = 'https://artifactory.maksonlee.com/artifactory'
REPO = 'android-snapshots'
USERNAME = 'maksonlee'
PROTECTED_APPS = frozenset({'math-runner'})
VERSION_FOLDER = re.compile(r'com/maksonlee/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._+-]*-SNAPSHOT)')
MAX_KEEP = 99
TIMEOUT_SECONDS = 120


class CleanupError(Exception):
    pass


def created(item):
    return datetime.datetime.fromisoformat(item['created'].replace('Z', '+00:00'))


def plan(items, keep):
    """Return (delete, keep, skipped): version folders to delete and to keep, and paths left alone."""
    if not isinstance(keep, int) or not 1 <= keep <= MAX_KEEP:
        raise CleanupError(f'--keep must be between 1 and {MAX_KEEP}')
    folders = defaultdict(lambda: {'size': 0, 'files': 0, 'newest': None})
    skipped = set()
    for item in items:
        match = VERSION_FOLDER.fullmatch('/'.join(item['path'].split('/')[:4]))
        if match is None:
            skipped.add(item['path'])
            continue
        folder = folders[match.groups()]
        folder['size'] += int(item['size'])
        folder['files'] += 1
        folder['newest'] = max(filter(None, (folder['newest'], created(item))))

    by_app = defaultdict(list)
    for (app, version), folder in folders.items():
        by_app[app].append({'app': app, 'version': version, 'path': f'com/maksonlee/{app}/{version}', **folder})
    delete, kept = [], []
    for app in sorted(by_app):
        newest_first = sorted(by_app[app], key=lambda folder: folder['newest'], reverse=True)
        if app in PROTECTED_APPS:
            kept += newest_first
            continue
        kept += newest_first[:keep]
        delete += newest_first[keep:]
    return delete, kept, sorted(skipped)


def request(method, url, auth, data=None, content_type=None):
    headers = {'Authorization': auth}
    if content_type:
        headers['Content-Type'] = content_type
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method),
                                timeout=TIMEOUT_SECONDS) as response:
        return response.status, response.read()


def list_files(auth):
    query = f'items.find({{"repo":"{REPO}","type":"file"}}).include("path","name","size","created")'
    status, body = request('POST', f'{ARTIFACTORY_URL}/api/search/aql', auth, query.encode(), 'text/plain')
    if status != 200:
        raise CleanupError(f'AQL query failed (HTTP {status})')
    return json.loads(body)['results']


def delete_folder(path, auth):
    if VERSION_FOLDER.fullmatch(path) is None or path.split('/')[2] in PROTECTED_APPS:
        raise CleanupError(f'Refusing to delete {path}')
    try:
        status, _ = request('DELETE', f'{ARTIFACTORY_URL}/{REPO}/{urllib.parse.quote(path)}', auth)
    except urllib.error.HTTPError as error:
        status = error.code
    return status in (200, 202, 204)


def report_line(folder):
    return (f"{folder['path']:70} {folder['size'] / 2**20:9.1f} MiB {folder['files']:4} files "
            f"newest {folder['newest'].date()}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--keep', type=int, required=True, help='version folders to keep per app')
    parser.add_argument('--delete', action='store_true', help='delete the planned folders (default: report only)')
    parser.add_argument('--report', help='write the plan and results as JSON')
    args = parser.parse_args(argv)
    password = os.environ.get('ARTIFACTORY_PASSWORD')
    if not password:
        raise CleanupError('ARTIFACTORY_PASSWORD is not set')
    auth = 'Basic ' + base64.b64encode(f'{USERNAME}:{password}'.encode()).decode()

    delete, kept, skipped = plan(list_files(auth), args.keep)
    mode = 'Deleting' if args.delete else 'Would delete (report only)'
    print(f'{mode} {len(delete)} version folders, {sum(f["size"] for f in delete) / 2**30:.2f} GiB; '
          f'keeping {len(kept)} folders, {sum(f["size"] for f in kept) / 2**30:.2f} GiB '
          f'(newest {args.keep} per app; {", ".join(sorted(PROTECTED_APPS))} untouched)')
    for folder in delete:
        print('  ' + report_line(folder))
    for path in skipped:
        print(f'  [skipped, unexpected layout] {path}')

    failed = []
    if args.delete:
        for folder in delete:
            if not delete_folder(folder['path'], auth):
                failed.append(folder['path'])
                print(f'  [FAILED] {folder["path"]}')
    if args.report:
        serial = lambda folders: [{**f, 'newest': f['newest'].isoformat()} for f in folders]
        with open(args.report, 'w', encoding='utf-8') as file:
            json.dump({'repo': REPO, 'keep': args.keep, 'deleted': args.delete, 'delete': serial(delete),
                       'kept': serial(kept), 'skipped': skipped, 'failed': failed}, file, indent=2)
            file.write('\n')
    if failed:
        raise CleanupError(f'{len(failed)} deletions failed')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except CleanupError as error:
        raise SystemExit(str(error)) from None
