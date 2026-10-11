"""Offline source-version and upgrade-order checks for steamos-manager."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "local/steamos-manager/PKGBUILD"
VERCMP = os.environ.get("VERCMP") or shutil.which("vercmp")


class SteamOSManagerRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / "steamos-manager"
        self.crate = self.source / "steamos-manager"
        self.crate.mkdir(parents=True)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        git = self.bin / "git"
        git.write_text('''#!/bin/bash
set -eu
[[ "$PWD" == "$EXPECTED_SOURCE" ]]
case "$*" in
  'rev-list --count HEAD') printf '%s\\n' "$REVISION" ;;
  'rev-parse --short=7 HEAD') printf '%s\\n' "$COMMIT" ;;
  *) exit 97 ;;
esac
''')
        git.chmod(0o755)

    def run_recipe(self, commands, *, revision="780", commit="55c6bd6"):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; ' + commands,
             "recipe-test", str(RECIPE)],
            cwd=self.work, text=True, capture_output=True, timeout=10,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                 "EXPECTED_SOURCE": str(self.source),
                 "REVISION": revision, "COMMIT": commit},
        )

    def test_syntax_and_existing_source(self):
        subprocess.run(["bash", "-n", str(RECIPE)], check=True)
        result = self.run_recipe('printf "%s\\n" "${source[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "steamos-manager::git+https://github.com/honjow/steamos-manager.git#branch=dev")

    def test_version_uses_built_crate_and_local_checkout(self):
        # Workspace and dependency versions must never become the package version.
        (self.source / "Cargo.toml").write_text('[workspace.package]\nversion = "99.0.0"\n')
        (self.crate / "Cargo.toml").write_text(
            '[package]\nname = "steamos-manager"\nversion = "26.4.1"\n'
            '[dependencies.example]\nversion = "99.0.0"\n')
        for revision, commit in (("780", "55c6bd6"), ("781", "0000000")):
            result = self.run_recipe("pkgver", revision=revision, commit=commit)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"26.4.1.r{revision}.g{commit}")

    def test_missing_invalid_and_dependency_only_versions_fail(self):
        for text in ('[package]\nname = "steamos-manager"\n',
                     '[dependencies.example]\nversion = "99.0.0"\n',
                     '[package]\nversion = ""\n',
                     '[package]\nversion = "26.4.1;bad"\n'):
            with self.subTest(text=text):
                (self.crate / "Cargo.toml").write_text(text)
                self.assertNotEqual(self.run_recipe("pkgver").returncode, 0)

    def test_invalid_revision_and_commit_fail(self):
        (self.crate / "Cargo.toml").write_text('[package]\nversion = "26.4.1"\n')
        self.assertNotEqual(self.run_recipe("pkgver", revision="").returncode, 0)
        self.assertNotEqual(self.run_recipe("pkgver", commit="not-a-hash").returncode, 0)

    @unittest.skipUnless(VERCMP, "set VERCMP to pacman's comparator")
    def test_upgrade_order_without_epoch(self):
        for new, old in (
            ("26.4.1.r780.g55c6bd6-1", "26.4.1.dev1.r0.gd906535-1"),
            ("26.4.1.r780.g55c6bd6-1", "26.0.1.r110.g55c6bd6-1"),
            ("26.4.1.r781.g0000000-1", "26.4.1.r780.gfffffff-1"),
        ):
            self.assertEqual(subprocess.check_output([VERCMP, new, old], text=True).strip(), "1")
        self.assertEqual(self.run_recipe('printf "%s" "${epoch:-}"').stdout, "")


if __name__ == "__main__":
    unittest.main()
