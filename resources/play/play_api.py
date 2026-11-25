"""Minimal Google Play Developer API client for the Flutter release job.

It signs the service-account JWT with openssl and uses only the Python standard library, so it runs with the
Jenkins agent's python3. The service-account JSON never leaves the file the job decodes for this call.
"""

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = 'https://androidpublisher.googleapis.com/androidpublisher/v3/applications/'
UPLOAD_API_BASE = 'https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/androidpublisher'


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def openssl_sign(signing_input: str, private_key: str) -> bytes:
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as key_file:
        key_file.write(private_key)
        key_path = key_file.name
    try:
        return subprocess.check_output(
            ['openssl', 'dgst', '-sha256', '-sign', key_path],
            input=signing_input.encode('ascii'),
        )
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass


def jwt_assertion(service: dict, now=None, sign=None) -> str:
    now = int(time.time()) if now is None else now
    sign = sign or openssl_sign
    header = {'alg': 'RS256', 'typ': 'JWT'}
    payload = {
        'iss': service['client_email'],
        'scope': SCOPE,
        'aud': TOKEN_URL,
        'iat': now,
        'exp': now + 3600,
    }
    signing_input = (
        b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))
        + '.'
        + b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    )
    return signing_input + '.' + b64url(sign(signing_input, service['private_key']))


def request_json(method, url, token=None, data=None, content_type='application/json', timeout=300):
    body = None
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if data is not None:
        body = json.dumps(data).encode('utf-8') if isinstance(data, (dict, list)) else data
        headers['Content-Type'] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        raise SystemExit(f'Google Play API {method} {url} failed: HTTP {error.code}\n{detail}')


def access_token(service: dict) -> str:
    response = request_json(
        'POST',
        TOKEN_URL,
        data=urllib.parse.urlencode({
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': jwt_assertion(service),
        }).encode('utf-8'),
        content_type='application/x-www-form-urlencoded',
    )
    return response['access_token']


def app_url(package_name: str) -> str:
    return API_BASE + urllib.parse.quote(package_name, safe='')


def upload_url(package_name: str) -> str:
    return UPLOAD_API_BASE + urllib.parse.quote(package_name, safe='')


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe='')


def load_service(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as file:
        return json.load(file)
