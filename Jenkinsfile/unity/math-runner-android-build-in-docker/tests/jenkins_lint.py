"""Read-only Jenkins Declarative linter; never prints the local API credential."""
import base64
import json
import pathlib
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def load_literal_assignments(path):
    wanted = {"JENKINS_URL", "JENKINS_USER", "JENKINS_API_TOKEN"}
    values = {}
    pattern = re.compile(r"^\s*(?:export\s+)?(" + "|".join(wanted) + r")=(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line)
        if not match:
            continue
        tokens = shlex.split(match.group(2), comments=False, posix=True)
        if len(tokens) != 1:
            raise SystemExit("Jenkins .bashrc assignment is not a literal value")
        values[match.group(1)] = tokens[0]
    if set(values) != wanted:
        raise SystemExit("Jenkins API settings are unavailable")
    if not values["JENKINS_URL"].startswith("https://"):
        raise SystemExit("Jenkins URL must use HTTPS")
    return values


def request(url, auth, data=None, crumb=None):
    headers = {"Authorization": "Basic " + auth}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if crumb is not None:
        headers[crumb[0]] = crumb[1]
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


if len(sys.argv) != 2:
    raise SystemExit("Usage: jenkins_lint.py Jenkinsfile")
settings = load_literal_assignments(pathlib.Path.home() / ".bashrc")
base = settings["JENKINS_URL"].rstrip("/")
auth = base64.b64encode(
    (settings["JENKINS_USER"] + ":" + settings["JENKINS_API_TOKEN"]).encode()
).decode()
try:
    status, body = request(base + "/crumbIssuer/api/json", auth)
    if status != 200:
        raise SystemExit(f"Jenkins crumb request returned HTTP {status}")
    crumb_doc = json.loads(body)
    crumb = crumb_doc["crumbRequestField"], crumb_doc["crumb"]
    source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    data = urllib.parse.urlencode({"jenkinsfile": source}).encode()
    status, body = request(
        base + "/pipeline-model-converter/validate", auth, data=data, crumb=crumb
    )
except urllib.error.HTTPError as error:
    raise SystemExit(f"Jenkins Declarative linter HTTP {error.code}") from None
if status != 200 or "Jenkinsfile successfully validated." not in body:
    raise SystemExit(f"Jenkins Declarative linter rejected candidate (HTTP {status}): {body[:500]}")
print("Jenkins Declarative linter: HTTP 200, Jenkinsfile successfully validated.")
job = base + "/job/unity/job/math-runner-android-build-in-docker"
try:
    status, config_text = request(job + "/config.xml", auth)
    config = ET.fromstring(config_text)
    disabled = config.findtext("disabled")
    if (status != 200 or disabled not in {"true", "false"} or
            config.findtext("./definition/scriptPath") !=
            "Jenkinsfile/unity/math-runner-android-build-in-docker/Jenkinsfile" or
            config.findtext("./definition/scm/branches/hudson.plugins.git.BranchSpec/name") !=
            "*/main" or
            config.findtext("./definition/scm/userRemoteConfigs/hudson.plugins.git.UserRemoteConfig/url") !=
            "git@github.com:maksonlee/jenkins-pipelines.git" or
            config.findtext("./definition/scm/userRemoteConfigs/hudson.plugins.git.UserRemoteConfig/credentialsId") !=
            "vault-github-ssh"):
        raise SystemExit("Jenkins job SCM or script configuration changed")
    status, api_text = request(
        job + "/api/json?tree=buildable,inQueue,lastBuild[number]", auth
    )
    job_state = json.loads(api_text)
    if (status != 200 or not isinstance(job_state.get("buildable"), bool) or
            not isinstance(job_state.get("inQueue"), bool)):
        raise SystemExit("Jenkins job runtime state is unavailable")
    last_build = job_state.get("lastBuild")
    if last_build is not None and (
            not isinstance(last_build, dict) or
            not isinstance(last_build.get("number"), int)):
        raise SystemExit("Jenkins lastBuild number is unavailable")
except urllib.error.HTTPError as error:
    raise SystemExit(f"Jenkins job readback HTTP {error.code}") from None
print(
    "Jenkins job readback: main/SCM/scriptPath retained; "
    f"disabled={disabled}, buildable={job_state['buildable']}, "
    f"inQueue={job_state['inQueue']}, "
    f"lastBuild={None if last_build is None else last_build['number']}."
)
