#!/usr/bin/env python3
"""Read-only inventory of every Jenkins job, optionally checked against jobs/jenkins_jobs.groovy.

Usage:
  python3 tools/jenkins_inventory.py            # one line per job
  python3 tools/jenkins_inventory.py --json     # machine-readable inventory
  python3 tools/jenkins_inventory.py --check    # also fail when a job from jobs/jenkins_jobs.groovy is missing
                                                # or its SCM, branch, script path or lightweight flag drifted

Only HTTP GET requests are sent. JENKINS_URL, JENKINS_USER and JENKINS_API_TOKEN are read from literal
assignments in ~/.bashrc and are never printed.
"""

import argparse
import base64
import datetime
import json
import pathlib
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE_REPO = "git@github.com:maksonlee/jenkins-pipelines.git"
SETTINGS = ("JENKINS_URL", "JENKINS_USER", "JENKINS_API_TOKEN")


def load_settings(path):
    pattern = re.compile(r"^\s*(?:export\s+)?(" + "|".join(SETTINGS) + r")=(.*)$")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line)
        if match:
            tokens = shlex.split(match.group(2), comments=False, posix=True)
            if len(tokens) != 1:
                raise SystemExit("Jenkins .bashrc assignment is not a literal value")
            values[match.group(1)] = tokens[0]
    if set(values) != set(SETTINGS):
        raise SystemExit("Jenkins API settings are unavailable")
    if not values["JENKINS_URL"].startswith("https://"):
        raise SystemExit("Jenkins URL must use HTTPS")
    return values


class Jenkins:
    def __init__(self, settings):
        self.base = settings["JENKINS_URL"].rstrip("/")
        token = f"{settings['JENKINS_USER']}:{settings['JENKINS_API_TOKEN']}".encode()
        self.auth = "Basic " + base64.b64encode(token).decode()

    def get(self, path):
        request = urllib.request.Request(self.base + path, headers={"Authorization": self.auth}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            raise SystemExit(f"Jenkins GET {path.split('?')[0]} returned HTTP {error.code}") from None

    def json(self, path):
        return json.loads(self.get(path))


def job_path(full_name):
    return "".join("/job/" + urllib.parse.quote(part, safe="") for part in full_name.split("/"))


def tag(element):
    return element.tag.rsplit(".", 1)[-1]


def describe(config_xml):
    root = ET.fromstring(config_xml)
    definition = root.find("definition")
    info = {
        "disabled": root.findtext("disabled") == "true",
        "description": bool((root.findtext("description") or "").strip()),
        "definition": definition.get("class", "").rsplit(".", 1)[-1] if definition is not None else None,
        "scm": None, "branch": None, "scriptPath": None, "lightweight": None,
        "triggers": [], "parameters": [], "buildDiscarder": False,
    }
    if definition is not None:
        remote = definition.find(".//hudson.plugins.git.UserRemoteConfig")
        info["scm"] = remote.findtext("url") if remote is not None else None
        info["branch"] = definition.findtext(".//hudson.plugins.git.BranchSpec/name")
        info["scriptPath"] = definition.findtext("scriptPath")
        info["lightweight"] = definition.findtext("lightweight") == "true"
    for prop in root.find("properties") if root.find("properties") is not None else []:
        name = tag(prop)
        if name == "BuildDiscarderProperty":
            info["buildDiscarder"] = True
        elif name == "PipelineTriggersJobProperty":
            info["triggers"] = sorted({tag(t) for t in prop.iter() if t is not prop and t.tag.endswith("Trigger")})
        elif name == "ParametersDefinitionProperty":
            info["parameters"] = [p.findtext("name") for p in prop.iter() if p.tag.endswith("ParameterDefinition")]
    return info


def walk(jenkins, path=""):
    tree = "jobs[name,fullName,_class,lastBuild[number,result,timestamp]]"
    for job in jenkins.json(f"{path}/api/json?tree={tree}").get("jobs", []):
        kind = job["_class"].rsplit(".", 1)[-1]
        if kind == "Folder":
            yield from walk(jenkins, job_path(job["fullName"]))
            continue
        entry = {"job": job["fullName"], "class": kind}
        last = job.get("lastBuild") or {}
        entry["lastBuild"] = last.get("number")
        entry["lastResult"] = last.get("result")
        entry["lastBuildDate"] = (
            datetime.datetime.fromtimestamp(last["timestamp"] / 1000).strftime("%Y-%m-%d") if last.get("timestamp") else None
        )
        entry.update(describe(jenkins.get(job_path(job["fullName"]) + "/config.xml")))
        yield entry


def expected_jobs(dsl_path):
    text = dsl_path.read_text(encoding="utf-8")
    pattern = re.compile(r"\[folder: '([^']+)', roles: \[([^\]]*)\]")
    jobs = [f"{folder}/{role.strip().strip(chr(39))}" for folder, roles in pattern.findall(text) for role in roles.split(",")
            if role.strip()]  # a folder-only entry has no roles
    jobs += re.findall(r"\[job: '([^']+)', description:", text)
    return {job: f"Jenkinsfile/{job}/Jenkinsfile" for job in jobs}


def check(inventory, expected):
    by_name = {entry["job"]: entry for entry in inventory}
    problems = []
    for name, script_path in sorted(expected.items()):
        entry = by_name.get(name)
        if entry is None:
            problems.append(f"{name}: missing")
            continue
        for key, wanted in (("scm", PIPELINE_REPO), ("branch", "*/main"), ("scriptPath", script_path), ("lightweight", True)):
            if entry[key] != wanted:
                problems.append(f"{name}: {key} is {entry[key]!r}, expected {wanted!r}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the inventory as JSON")
    parser.add_argument("--check", action="store_true", help="compare with jobs/jenkins_jobs.groovy")
    args = parser.parse_args(argv)
    jenkins = Jenkins(load_settings(pathlib.Path.home() / ".bashrc"))
    inventory = list(walk(jenkins))
    if args.json:
        print(json.dumps(inventory, indent=2))
    else:
        for e in inventory:
            print(f"{e['job']:55} {'DISABLED ' if e['disabled'] else ''}last=#{e['lastBuild']} {e['lastResult']} "
                  f"{e['lastBuildDate']} script={e['scriptPath']} branch={e['branch']} "
                  f"triggers={','.join(e['triggers']) or '-'} discarder={e['buildDiscarder']}")
    if args.check:
        problems = check(inventory, expected_jobs(ROOT / "jobs/jenkins_jobs.groovy"))
        for problem in problems:
            print("DRIFT " + problem, file=sys.stderr)
        print(f"Checked {len(expected_jobs(ROOT / 'jobs/jenkins_jobs.groovy'))} defined jobs: {len(problems)} problem(s).",
              file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
