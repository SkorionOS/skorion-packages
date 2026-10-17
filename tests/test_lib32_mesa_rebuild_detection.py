"""Offline metadata and real shell-entrypoint checks; no install/build/network."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/check-lib32-mesa-llvm.py"
spec = importlib.util.spec_from_file_location("mesa_rebuild", HELPER)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def database(records):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for i, record in enumerate(records):
            content = "".join(f"%{key}%\n" + "\n".join(values) + "\n\n"
                              for key, values in record.items()).encode()
            entry = tarfile.TarInfo(f"fixture-{i}/desc")
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


def family(version="1:26.2.4.sk-1", pin="1:23.1.1"):
    return [{"NAME": [name], "BASE": ["lib32-mesa"], "VERSION": [version],
             "DEPENDS": ["lib32-glibc", f"lib32-llvm-libs={pin}"]}
            for name in guard.SPLITS]


def provider(version="1:23.1.3-1"):
    return database([{"NAME": ["lib32-llvm-libs"], "VERSION": [version]}])


def fixture_compare(left, right):
    # Test fixture ordering only. Production always uses pacman's vercmp.
    def key(value):
        epoch, tail = value.split(":") if ":" in value else ("0", value)
        version, release = tail.rsplit("-", 1)
        return int(epoch), version, tuple(map(int, release.split(".")))
    return (key(left) > key(right)) - (key(left) < key(right))


class LLVMRebuildMetadataTests(unittest.TestCase):
    def check(self, records=None, recipe="1:26.2.4.sk-1", available="1:23.1.3-1"):
        return guard.check(recipe, database(family() if records is None else records),
                           provider(available), compare=fixture_compare)

    def test_matching_provider_and_packaging_only_change_can_skip(self):
        for version in ("1:23.1.1-1", "1:23.1.1-2", "1:23.1.1-2.1"):
            self.assertIn("matches", self.check(available=version))

    def test_upgrade_rollback_and_epoch_change_require_bump(self):
        for version in ("1:23.1.3-1", "1:23.1.0-1", "2:23.1.1-1"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "Bump pkgrel.*FORCE_REBUILD"):
                self.check(available=version)

    def test_source_controlled_release_or_source_bump_allows_rebuild(self):
        for recipe in ("1:26.2.4.sk-2", "1:26.2.5.sk-1", "2:26.2.4.sk-1"):
            with self.subTest(recipe=recipe):
                self.assertIn("rebuild the complete family", self.check(recipe=recipe))

    def test_newer_published_release_must_not_be_downgraded(self):
        with self.assertRaisesRegex(ValueError, "older than published"):
            self.check(family(version="1:26.2.4.sk-3"), recipe="1:26.2.4.sk-2")

    def test_every_missing_split_fails_unless_recipe_is_newer(self):
        for index in range(4):
            records = family()
            removed = records.pop(index)["NAME"][0]
            with self.subTest(name=removed):
                with self.assertRaisesRegex(ValueError, f"missing {removed}"):
                    self.check(records)
                self.assertIn("repair missing " + removed,
                              self.check(records, recipe="1:26.2.4.sk-2"))
        with self.assertRaisesRegex(ValueError, "No stable.*metadata"):
            self.check([])

    def test_missing_unversioned_invalid_or_mixed_pins_do_not_skip(self):
        for dependency in ([], ["lib32-llvm-libs"], ["lib32-llvm-libs>=1:23.1.1"],
                           ["lib32-llvm-libs=bad value"], ["lib32-llvm-libs=1:23.1.3"],
                           ["lib32-llvm-libs=1:23.1.1", "lib32-llvm-libs=1:23.1.3"]):
            records = family()
            records[1]["DEPENDS"] = dependency
            with self.subTest(dependency=dependency):
                with self.assertRaisesRegex(ValueError, "metadata.*Bump pkgrel"):
                    self.check(records)
                self.assertIn("repair", self.check(records, recipe="1:26.2.4.sk-2"))

    def test_partial_publish_requires_version_newer_than_every_split(self):
        records = family()
        records[2]["VERSION"] = ["1:26.2.4.sk-2"]
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.check(records, recipe="1:26.2.4.sk-2")
        self.assertIn("repair", self.check(records, recipe="1:26.2.4.sk-3"))

    def test_corrupt_ambiguous_and_invalid_metadata_fail_closed(self):
        for records in (family() + [family()[0]],
                        [{**family()[0], "VERSION": ["bad"]}] + family()[1:],
                        [{**family()[0], "BASE": ["unrelated"]}] + family()[1:]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.check(records, recipe="1:26.2.4.sk-2")
        with self.assertRaises(tarfile.TarError):
            guard.check("1:26.2.4.sk-2", b"not an archive", provider())
        with self.assertRaisesRegex(ValueError, "Missing package metadata"):
            guard.check("1:26.2.4.sk-2", database(family()), database([]))

    def test_network_failure_is_not_interpreted_as_first_publication(self):
        with mock.patch.object(guard.urllib.request, "urlopen", side_effect=OSError("unavailable")) as request, \
             mock.patch.object(guard.time, "sleep"):
            with self.assertRaisesRegex(OSError, "unavailable"):
                guard.read_database(guard.REPOSITORY_DB)
        self.assertEqual(request.call_count, 3)

    def test_version_comparison_uses_vercmp_without_shell(self):
        with mock.patch.object(guard.subprocess, "check_output", return_value="1\n") as command:
            self.assertEqual(guard.vercmp("1:26.2.4.sk-2", "1:26.2.4.sk-1"), 1)
        command.assert_called_once_with(["vercmp", "1:26.2.4.sk-2", "1:26.2.4.sk-1"], text=True)
        with mock.patch.object(guard.subprocess, "check_output", return_value="bogus"):
            with self.assertRaises(ValueError):
                guard.vercmp("1:26.2.4.sk-2", "1:26.2.4.sk-1")

    @unittest.skipUnless(shutil.which("vercmp"), "pacman's comparator required")
    def test_real_pacman_version_ordering(self):
        for recipe in ("1:26.2.4.sk-2", "1:26.2.10.sk-1", "2:26.2.3.sk-1"):
            self.assertIn("rebuild the complete family",
                          guard.check(recipe, database(family()), provider()))
        with self.assertRaisesRegex(ValueError, "older than published"):
            guard.check("1:26.2.3.sk-20", database(family()), provider())


@unittest.skipUnless(shutil.which("jq"), "jq required for the real update detector")
class LLVMRebuildEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        (self.workspace / "scripts").mkdir(parents=True)
        for filename in ("check-updates.sh", "build-single-package.sh", HELPER.name):
            shutil.copyfile(ROOT / "scripts" / filename, self.workspace / "scripts" / filename)
        self.recipe = self.workspace / "local/lib32-mesa/PKGBUILD"
        self.recipe.parent.mkdir(parents=True)
        self.recipe.write_text("epoch=1\npkgver=26.2.4.sk\npkgrel=1\n")
        (self.workspace / "aur.conf").write_text("")
        self.repo_db = self.root / "repository.db"
        self.repo_db.write_bytes(database(family()))
        self.provider_db = self.root / "provider.db"
        self.provider_db.write_bytes(provider())
        self.assets = self.root / "release.json"
        self.assets.write_text(json.dumps({"assets": [
            {"name": f"{name}-1--26.2.4.sk-1-x86_64.pkg.tar.zst"} for name in guard.SPLITS]}))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "REAL_PYTHON": sys.executable, "FIXTURE_REPO_DB": str(self.repo_db),
                    "FIXTURE_PROVIDER_DB": str(self.provider_db), "GITHUB_WORKSPACE": str(self.workspace),
                    "OUTPUT_DIR": str(self.root / "output"), "WORK_DIR": str(self.root / "work"),
                    "PIKAUR_MARKER": str(self.root / "pikaur-ran"), "SRCDEST": "", "GH_TOKEN": "",
                    "HELPER_ARGUMENTS": str(self.root / "helper-arguments"),
                    "FIXTURE_RELEASE_JSON": str(self.assets),
                    "FORCE_REBUILD": "", "SKIP_PACKAGES": "", "PARALLEL_JOBS": "2"}
        self.command("python3", '''
case "$1" in
  */check-lib32-mesa-llvm.py)
    printf '%s\\n' "$@" >> "$HELPER_ARGUMENTS"
    exec "$REAL_PYTHON" "$@" --repository-db "$FIXTURE_REPO_DB" --provider-db "$FIXTURE_PROVIDER_DB" ;;
  *) exec "$REAL_PYTHON" "$@" ;;
esac
''')
        self.command("vercmp", '''
exec "$REAL_PYTHON" -c 'import sys
def key(v):
 e,t=v.split(":"); v,r=t.rsplit("-",1); return int(e),v,tuple(map(int,r.split(".")))
a,b=map(key,sys.argv[1:]); print((a>b)-(a<b))' "$@"
''')
        self.command("curl", '''
case "${!#}" in
 https://api.github.com/repos/*/releases/tags/latest)
  cat "$FIXTURE_RELEASE_JSON" ;;
 https://aur.archlinux.org/rpc/v5/info*) printf '%s\\n' '{"results":[]}' ;;
 *) exit 91 ;;
esac
''')
        self.command("pikaur", 'touch "$PIKAUR_MARKER"\n')

    def command(self, name, source):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -eu\n" + source)
        path.chmod(0o755)

    def run_script(self, script, *args):
        return subprocess.run(["bash", str(self.workspace / "scripts" / script), *args],
                              cwd=self.workspace, env=self.env, capture_output=True, text=True, timeout=15)

    def assert_detection_blocked(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning title=lib32-mesa build blocked::", result.stderr)
        self.assertIn("Blocked local packages: 1", result.stdout)
        self.assertNotIn("lib32-mesa: 版本未变化", result.stdout)
        self.assertIn("lib32-mesa:", (self.workspace / "blocked-local-packages.txt").read_text())
        self.assertNotIn("lib32-mesa", (self.workspace / "updated-local-packages.txt").read_text())

    def test_real_detection_blocks_only_this_package_even_when_forced(self):
        for forced in ("", "lib32-mesa"):
            self.env["FORCE_REBUILD"] = forced
            result = self.run_script("check-updates.sh")
            self.assert_detection_blocked(result)
            self.assertIn("Bump pkgrel", result.stderr)

    def test_blocked_mesa_does_not_stop_unrelated_updates(self):
        unrelated = self.workspace / "local/unrelated/PKGBUILD"
        unrelated.parent.mkdir()
        unrelated.write_text("pkgver=1\npkgrel=1\n")
        for corrupt in (False, True):
            if corrupt:
                self.repo_db.write_bytes(b"bad archive")
            result = self.run_script("check-updates.sh")
            self.assert_detection_blocked(result)
            self.assertEqual((self.workspace / "updated-local-packages.txt").read_text(), "unrelated\n")

    def test_bumped_recipe_is_selected_and_unchanged_matching_recipe_is_skipped(self):
        self.recipe.write_text("epoch=1\npkgver=26.2.4.sk\npkgrel=2\n")
        result = self.run_script("check-updates.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "updated-local-packages.txt").read_text(), "lib32-mesa\n")
        self.recipe.write_text("epoch=1\npkgver=26.2.4.sk\npkgrel=1\n")
        self.provider_db.write_bytes(provider("1:23.1.1-2"))
        result = self.run_script("check-updates.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "updated-local-packages.txt").read_text(), "")

    def test_explicit_skip_is_respected(self):
        self.env["SKIP_PACKAGES"] = "lib32-mesa"
        result = self.run_script("check-updates.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("LLVM preflight", result.stderr)

    def test_build_rechecks_provider_and_stops_before_pikaur(self):
        self.provider_db.write_bytes(provider("1:23.1.1-1"))
        self.assertEqual(self.run_script("check-updates.sh").returncode, 0)
        # The provider changes after successful detection, before the build.
        self.provider_db.write_bytes(provider("1:23.1.3-1"))
        result = self.run_script("build-single-package.sh", "lib32-mesa", "local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Bump pkgrel", result.stderr)
        self.assertFalse(Path(self.env["PIKAUR_MARKER"]).exists())
        arguments = Path(self.env["HELPER_ARGUMENTS"]).read_text().splitlines()
        self.assertIn("/var/lib/pacman/sync/multilib.db", arguments)

    def test_same_provider_forced_rebuild_keeps_existing_behavior(self):
        self.env["FORCE_REBUILD"] = "lib32-mesa"
        self.provider_db.write_bytes(provider("1:23.1.1-2"))
        result = self.run_script("check-updates.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.workspace / "updated-local-packages.txt").read_text(), "lib32-mesa\n")

    def test_metadata_failure_is_visible_without_stopping_unrelated_detection(self):
        self.repo_db.write_bytes(b"bad archive")
        result = self.run_script("check-updates.sh")
        self.assert_detection_blocked(result)
        self.assertIn("LLVM preflight failed", result.stderr)

    def test_newer_assets_with_old_database_cannot_silently_skip(self):
        self.recipe.write_text("epoch=1\npkgver=26.2.4.sk\npkgrel=2\n")
        self.assets.write_text(self.assets.read_text().replace(".sk-1-", ".sk-2-"))
        result = self.run_script("check-updates.sh")
        self.assert_detection_blocked(result)
        self.assertIn("asset/database mismatch", result.stderr)
        self.assertNotIn("lib32-mesa: 版本未变化", result.stdout)

    def test_missing_split_asset_stops_detection(self):
        metadata = json.loads(self.assets.read_text())
        metadata["assets"].pop()
        self.assets.write_text(json.dumps(metadata))
        result = self.run_script("check-updates.sh")
        self.assert_detection_blocked(result)
        self.assertIn("asset/database mismatch", result.stderr)

    def test_unrelated_local_build_never_calls_the_guard(self):
        unrelated = self.workspace / "local/unrelated/PKGBUILD"
        unrelated.parent.mkdir()
        unrelated.write_text("pkgver=1\npkgrel=1\n")
        result = self.run_script("build-single-package.sh", "unrelated", "local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(Path(self.env["PIKAUR_MARKER"]).exists())
        self.assertFalse(Path(self.env["HELPER_ARGUMENTS"]).exists())

    def test_build_accepts_source_bump_but_not_missing_metadata(self):
        self.recipe.write_text("epoch=1\npkgver=26.2.4.sk\npkgrel=2\n")
        result = self.run_script("build-single-package.sh", "lib32-mesa", "local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(Path(self.env["PIKAUR_MARKER"]).exists())
        self.repo_db.write_bytes(b"bad archive")
        Path(self.env["PIKAUR_MARKER"]).unlink()
        result = self.run_script("build-single-package.sh", "lib32-mesa", "local")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(self.env["PIKAUR_MARKER"]).exists())


if __name__ == "__main__":
    unittest.main()
