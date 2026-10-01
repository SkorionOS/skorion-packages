"""Offline, commit-based selection tests; no AUR or release API access."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-pr-packages.py"
SPEC = importlib.util.spec_from_file_location("select_pr_packages", SCRIPT)
SELECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)


class PackageNameTests(unittest.TestCase):
    def test_safe_names(self):
        for name in ("bootc-git", "libselinux", "lib32-mesa-git", "foo.bar", "foo+bar", "foo@1", "foo_bar"):
            with self.subTest(name=name):
                self.assertEqual(SELECTOR.validate_package_name(name), name)

    def test_shell_injection_paths_and_options_are_rejected(self):
        for name in ("", ".", "..", "../pkg", "foo/bar", "-pkg", ".pkg", "foo bar",
                     "foo\nbar", "foo'bar", 'foo"bar', "foo;id", "$(id)", "foo`id`", "foo\\bar", "fooé"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                SELECTOR.validate_package_name(name)


class SelectPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("aur.conf", "# AUR packages\nexisting\nremoved\n")
        self.write("local/bootc-git/PKGBUILD", "pkgver=1\npkgrel=1\n")
        self.write("local/untouched/PKGBUILD", "pkgver=1\n")
        self.write("local/deleted/PKGBUILD", "pkgver=1\n")
        self.write("local/patched/PKGBUILD", "pkgver=1\n")
        self.write("local/patched/fix.patch", "old patch\n")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def write(self, path, text):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test fixture")
        return self.git("rev-parse", "HEAD")

    def run_selector(self, base=None, head="HEAD"):
        return subprocess.run(["python3", str(SCRIPT), "--base", base or self.base, "--head", head],
                              cwd=self.root, capture_output=True, text=True, timeout=10)

    def selected(self, head="HEAD"):
        result = self.run_selector(head=head)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_changed_recipe_and_new_aur_entries_build_even_without_version_change(self):
        self.write("local/bootc-git/PKGBUILD", "pkgver=1\npkgrel=1\ndepends=(libselinux)\n")
        self.write("aur.conf", "# Reordered list\nlibsepol\nexisting\nlibselinux\nlibselinux\n")
        self.commit()
        self.assertEqual(self.selected(), [
            {"package": "libselinux", "type": "aur"},
            {"package": "libsepol", "type": "aur"},
            {"package": "bootc-git", "type": "local"},
        ])

    def test_deleted_patch_rebuilds_surviving_package_but_deleted_recipe_is_skipped(self):
        (self.root / "local/patched/fix.patch").unlink()
        (self.root / "local/deleted/PKGBUILD").unlink()
        self.commit()
        self.assertEqual(self.selected(), [{"package": "patched", "type": "local"}])

    def test_added_and_renamed_local_packages(self):
        self.write("local/new-package/PKGBUILD", "pkgver=1\n")
        (self.root / "local/untouched").rename(self.root / "local/renamed")
        self.commit()
        self.assertEqual(self.selected(), [
            {"package": "new-package", "type": "local"},
            {"package": "renamed", "type": "local"},
        ])

    def test_empty_recipe_is_selected_so_build_reports_error(self):
        self.write("local/bootc-git/PKGBUILD", "")
        self.commit()
        self.assertEqual(self.selected(), [{"package": "bootc-git", "type": "local"}])

    def test_docs_comments_and_removed_aur_entries_do_not_trigger_package_builds(self):
        self.write("local/README.md", "Documentation\n")
        self.write("README.md", "Updated documentation\n")
        self.write("aur.conf", "# Comments and blank lines\n\nexisting\n")
        self.commit()
        self.assertEqual(self.selected(), [])

    def test_reads_requested_commit_not_uncommitted_files(self):
        self.write("local/bootc-git/PKGBUILD", "pkgver=1\n# recipe change\n")
        head = self.commit()
        self.write("aur.conf", "injected-new-package\n")
        self.write("local/untouched/PKGBUILD", "uncommitted change\n")
        self.assertEqual(self.selected(head=head), [{"package": "bootc-git", "type": "local"}])

    def test_merge_head_includes_proposed_recipe_changes(self):
        self.git("checkout", "-qb", "pr")
        self.write("local/bootc-git/PKGBUILD", "pkgver=1\n# PR recipe change\n")
        self.commit()
        self.git("checkout", "-qb", "base", self.base)
        self.write("README.md", "Base advanced\n")
        self.base = self.commit()
        self.git("merge", "--no-ff", "-m", "Proposed merge", "pr")
        self.assertEqual(self.selected(), [{"package": "bootc-git", "type": "local"}])

    def test_invalid_aur_entry_fails_closed(self):
        self.write("aur.conf", "existing\nfoo';touch /tmp/injected;#\n")
        self.commit()
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe package name", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_local_directory_fails_closed(self):
        self.write("local/foo'bar/PKGBUILD", "pkgver=1\n")
        self.commit()
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe package name", result.stderr)

    def test_symlink_config_is_rejected(self):
        (self.root / "aur.conf").unlink()
        (self.root / "aur.conf").symlink_to("README.md")
        self.commit()
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected a regular file", result.stderr)

    def test_invalid_commit_arguments_fail_closed(self):
        for base in ("--all", "HEAD~1", "master", "a" * 40):
            with self.subTest(base=base):
                self.assertNotEqual(self.run_selector(base=base).returncode, 0)


if __name__ == "__main__":
    unittest.main()
