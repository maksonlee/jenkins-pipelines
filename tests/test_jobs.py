"""Keep jobs/jenkins_jobs.groovy, the per-project Jenkinsfiles and the seed job consistent."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSL = (ROOT / "jobs/jenkins_jobs.groovy").read_text(encoding="utf-8")
SEED = (ROOT / "Jenkinsfile/admin/seed-jobs/Jenkinsfile").read_text(encoding="utf-8")
ENTRY = re.compile(r"\[folder: '([^']+)', roles: \[([^\]]*)\], repo: '([^']+)', type: '([^']+)'\]")
PER_PROJECT_ROLES = ("ci", "release")


def entries():
    return [
        (folder, [r.strip().strip("'") for r in roles.split(",")], repo, kind)
        for folder, roles, repo, kind in ENTRY.findall(DSL)
    ]


class JobDefinitionTest(unittest.TestCase):
    def test_every_entry_has_its_jenkinsfile_and_description(self):
        self.assertGreater(len(entries()), 0)
        for folder, roles, repo, kind in entries():
            for role in roles:
                with self.subTest(job=f"{folder}/{role}"):
                    jenkinsfile = ROOT / "Jenkinsfile" / folder / role / "Jenkinsfile"
                    self.assertTrue(jenkinsfile.is_file(), jenkinsfile)
                    self.assertIn(f"'{kind}/{role}'", DSL)
                    text = jenkinsfile.read_text(encoding="utf-8")
                    self.assertTrue(f"repo: '{repo}'" in text or f"maksonlee/{repo}.git" in text, text[:200])

    def test_every_per_project_jenkinsfile_is_listed(self):
        listed = {f"{folder}/{role}" for folder, roles, _, _ in entries() for role in roles}
        found = {
            path.parent.relative_to(ROOT / "Jenkinsfile").as_posix()
            for path in (ROOT / "Jenkinsfile").glob("*/*/*/Jenkinsfile")
            if path.parent.name in PER_PROJECT_ROLES
        }
        self.assertEqual(found - listed, set())

    def test_top_level_folders_are_never_declared(self):
        for folder in re.findall(r"folder\(([^)]*)\)", DSL):
            self.assertEqual(folder.strip(), "project.folder")
        for folder, _, _, _ in entries():
            self.assertEqual(folder.count("/"), 1, folder)

    def test_seed_only_creates_and_never_removes(self):
        for setting in ("ignoreExisting: true", "removedJobAction: 'IGNORE'", "removedViewAction: 'IGNORE'",
                        "targets: 'jobs/jenkins_jobs.groovy'"):
            self.assertIn(setting, SEED)
        self.assertNotIn("DELETE", SEED)


if __name__ == "__main__":
    unittest.main()
