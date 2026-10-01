"""Regression coverage for PR isolation and default-branch cleanup routing."""

import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's `on` key (YAML 1.1 otherwise treats it as True).
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = workflow("build.yml")
        cls.validation = workflow("validate.yml")
        cls.cleanup = workflow("cleanup-locks.yml")

    def test_pr_and_production_triggers_are_separate(self):
        self.assertEqual(set(self.validation["on"]), {"pull_request"})
        self.assertEqual(self.validation["on"]["pull_request"]["branches"], ["master"])
        self.assertEqual(set(self.production["on"]), {"push", "schedule", "workflow_dispatch"})
        self.assertEqual(self.production["on"]["push"]["branches"], ["master"])
        # Same-repository and fork PRs both use pull_request. Neither may run the
        # release workflow; pushes to feature branches also cannot publish.
        for event, branch, expected in (
            ("pull_request", "master", False),
            ("pull_request", "feature", False),
            ("pull_request_target", "master", False),
            ("push", "master", True),
            ("push", "feature", False),
            ("schedule", "master", True),
            ("workflow_dispatch", "master", True),
        ):
            with self.subTest(event=event, branch=branch):
                triggers = self.production["on"]
                actual = event in triggers and (event != "push" or branch in triggers[event]["branches"])
                self.assertEqual(actual, expected)

    def test_old_default_branch_cleanup_cannot_follow_validation(self):
        # Before this PR merges, the default-branch Cleanup Locks is unguarded
        # and listens to this exact workflow name. Changing only PR-copy guards
        # would not protect it: the new PR workflow must have a different name.
        old_cleanup_names = ["Build Packages"]
        self.assertEqual(self.production["name"], "Build Packages")
        self.assertEqual(self.validation["name"], "Validate Packages")
        self.assertNotIn(self.validation["name"], old_cleanup_names)
        self.assertEqual(self.cleanup["on"]["workflow_run"]["workflows"], old_cleanup_names)
        self.assertNotIn(self.validation["name"], self.cleanup["on"]["workflow_run"]["workflows"])
        self.assertEqual(self.cleanup["on"]["workflow_run"]["branches"], ["master"])

    def test_validation_has_read_only_credentials_and_no_production_side_effects(self):
        self.assertEqual(self.validation["permissions"], {"contents": "read"})
        text = (ROOT / ".github/workflows/validate.yml").read_text()
        for forbidden in ("secrets.", "github.token", "GH_TOKEN:", "GITHUB_TOKEN:", "actions/cache",
                          "action-gh-release", "upload-release-assets.sh", "update-repo-database.sh",
                          "check-updates.sh", "/git/refs", "/git/matching-refs", "docker.sock"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        for job in self.validation["jobs"].values():
            self.assertNotIn("permissions", job)
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertEqual(step["with"]["persist-credentials"], "false")
        self.assertIn('docker run --rm', text)
        self.assertIn('archlinux:latest', text)
        self.assertIn('bash /workspace/scripts/build-in-container.sh "$PACKAGE_NAME" "$PACKAGE_TYPE"', text)
        self.assertIn('PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}', text)
        self.assertIn('--base "$PR_BASE_SHA" --head HEAD', text)
        self.assertIn('fetch-depth: 0', text)
        for job in self.validation["jobs"].values():
            for step in job["steps"]:
                if "run" in step:
                    self.assertNotIn("${{ matrix.", step["run"])

    def test_cleanup_event_allowlist_repository_and_branch_guards(self):
        # Evaluate only this small checked-in condition, not arbitrary GitHub
        # expressions. All fixture values are local; no GitHub calls are made.
        expression = self.cleanup["jobs"]["cleanup"]["if"].replace("&&", " and ").replace("||", " or ")
        expression = " ".join(expression.split())
        cases = [
            ("workflow_run", "push", "SkorionOS/packages", "master", "Build Packages", True),
            ("workflow_run", "schedule", "SkorionOS/packages", "master", "Build Packages", True),
            ("workflow_run", "workflow_dispatch", "SkorionOS/packages", "master", "Build Packages", True),
            ("workflow_run", "pull_request", "SkorionOS/packages", "master", "Build Packages", False),
            ("workflow_run", "pull_request", "fork/packages", "master", "Build Packages", False),
            ("workflow_run", "pull_request_target", "SkorionOS/packages", "master", "Build Packages", False),
            ("workflow_run", "push", "fork/packages", "master", "Build Packages", False),
            ("workflow_run", "push", "SkorionOS/packages", "feature", "Build Packages", False),
            ("workflow_run", "push", "SkorionOS/packages", "master", "Validate Packages", False),
            ("workflow_run", "check_run", "SkorionOS/packages", "master", "Build Packages", False),
            ("workflow_dispatch", "", "SkorionOS/packages", "master", "", True),
            ("workflow_dispatch", "", "SkorionOS/packages", "feature", "", False),
            ("pull_request", "", "SkorionOS/packages", "master", "", False),
        ]
        for event, source_event, repo, branch, name, expected in cases:
            with self.subTest(event=event, source_event=source_event, repo=repo, branch=branch, name=name):
                github = SimpleNamespace(
                    event_name=event, ref=f"refs/heads/{branch}", repository="SkorionOS/packages",
                    event=SimpleNamespace(repository=SimpleNamespace(default_branch="master"),
                        workflow_run=SimpleNamespace(event=source_event, name=name, head_branch=branch,
                            head_repository=SimpleNamespace(full_name=repo))),
                )
                actual = eval(expression, {"__builtins__": {}}, {
                    "github": github, "fromJSON": json.loads,
                    "contains": lambda values, item: item in values,
                    "format": lambda template, value: template.format(value),
                })
                self.assertEqual(actual, expected)
        # Guarding cleanup must not expand its existing permissions.
        self.assertNotIn("permissions", self.cleanup)
        self.assertNotIn("permissions", self.cleanup["jobs"]["cleanup"])

    def test_bash_syntax_for_scripts_and_workflow_steps(self):
        for path in [*ROOT.glob("scripts/*.sh"), *ROOT.glob("tests/*.sh")]:
            with self.subTest(path=path.name):
                subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
        for path in ROOT.glob(".github/workflows/*.yml"):
            document = workflow(path.name)
            for job in document["jobs"].values():
                for step in job.get("steps", []):
                    if "run" in step:
                        with self.subTest(workflow=path.name, step=step.get("name", "run")):
                            source = re.sub(r"\$\{\{.*?\}\}", "placeholder", step["run"])
                            subprocess.run(["bash", "-n"], input=source, text=True, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
