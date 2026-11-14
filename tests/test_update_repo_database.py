"""Offline updater regression tests: python3 -m unittest discover -s tests -v."""

import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update-repo-database.sh"
ARCHIVES = ("skorion.db.tar.gz", "skorion.files.tar.gz")


class UpdateRepoDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "working directory"
        self.work.mkdir()
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "MOCK_LOG": str(self.log),
            "MOCK_FIXTURES": str(self.fixtures),
            "MOCK_FAILURES": "{}",
            "MOCK_REPO_MODE": "success",
        }
        (self.work / "output").mkdir()
        for name in ("new package-1-1-any.pkg.tar.zst", "other-1-1-any.pkg.tar.zst"):
            (self.work / "output" / name).write_bytes(b"mock package")
        for name in ARCHIVES:
            self.make_archive(self.fixtures / name, "existing-package-1/desc")
            self.make_archive(self.work / name, "previous-local-package-1/desc")
        (self.work / "skorion.db").symlink_to(ARCHIVES[0])
        (self.work / "skorion.files").symlink_to(ARCHIVES[1])
        self.original = {name: (self.work / name).read_bytes() for name in ARCHIVES}
        self.write_command("pacman", 'import sys\nsys.exit(0)\n')
        self.write_command("curl", r'''
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith("https://"))
name = url.rsplit("/", 1)[1]
output = pathlib.Path(args[args.index("-o") + 1] if "-o" in args else args[args.index("--output") + 1])
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write(json.dumps({"command": "curl", "args": args, "output": str(output.resolve())}) + "\n")
status = json.loads(os.environ["MOCK_FAILURES"]).get(name, 0)
if status:
    output.write_bytes(b"partial failed download")
    sys.exit(status)
shutil.copyfile(pathlib.Path(os.environ["MOCK_FIXTURES"]) / name, output)
''')
        self.write_command("repo-add", r'''
import io, json, os, pathlib, sys, tarfile
args = sys.argv[1:]
database = pathlib.Path(next(arg for arg in args if arg.endswith(".db.tar.gz")))
files = database.with_name("skorion.files.tar.gz")
archives = [database, files]
entries = {}
for archive in archives:
    with tarfile.open(archive, "r:gz") as source:
        entries[archive.name] = {member.name: source.extractfile(member).read() for member in source.getmembers() if member.isfile()}
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write(json.dumps({"command": "repo-add", "args": args, "database": str(database.resolve()), "entries": {name: list(value) for name, value in entries.items()}, "packages": [str(pathlib.Path(arg).resolve()) for arg in args if arg.endswith(".pkg.tar.zst")]}) + "\n")
mode = os.environ["MOCK_REPO_MODE"]
if mode == "fail":
    database.write_bytes(b"partial repo-add write")
    sys.exit(1)
for archive in archives:
    with tarfile.open(archive, "w:gz") as target:
        for name, data in {**entries[archive.name], "new-package-1/desc": b"new entry"}.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
if mode in ("mismatched", "shrink"):
    targets = [files] if mode == "mismatched" else archives
    for archive in targets:
        with tarfile.open(archive, "w:gz") as target:
            data = b"replacement"
            member = tarfile.TarInfo("replacement-1/desc")
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
if mode.startswith("corrupt:"):
    database.with_name(mode.split(":", 1)[1]).write_bytes(b"invalid archive")
if mode.startswith("empty:"):
    with tarfile.open(database.with_name(mode.split(":", 1)[1]), "w:gz"):
        pass
if mode.startswith("missing:"):
    database.with_name(mode.split(":", 1)[1]).unlink()
''')

    @staticmethod
    def make_archive(path, name=None):
        with tarfile.open(path, "w:gz") as archive:
            if name:
                data = b"fixture package entry\n"
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def write_command(self, name, source):
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o755)

    def run_update(self):
        if self.log.exists():
            self.log.unlink()
        result = subprocess.run(
            ["bash", str(SCRIPT), "example/packages"], cwd=self.work,
            env=self.env, text=True, capture_output=True, timeout=15,
        )
        events = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, events

    def assert_outputs_unchanged(self):
        for name in ARCHIVES:
            self.assertEqual((self.work / name).read_bytes(), self.original[name])
        for alias, target in zip(("skorion.db", "skorion.files"), ARCHIVES):
            self.assertTrue((self.work / alias).is_symlink())
            self.assertEqual(os.readlink(self.work / alias), target)

    def assert_download_rejected(self):
        result, events = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(event["command"] == "repo-add" for event in events))
        self.assert_outputs_unchanged()

    def test_download_failure_never_creates_replacement_index(self):
        # curl 22 covers HTTP failures such as 404/5xx, 6/28 network failures.
        for name in ARCHIVES:
            for status in (22, 6, 28):
                with self.subTest(archive=name, curl_status=status):
                    self.env["MOCK_FAILURES"] = json.dumps({name: status})
                    self.assert_download_rejected()

    def test_failed_first_run_does_not_create_any_publishable_outputs(self):
        for name in (*ARCHIVES, "skorion.db", "skorion.files"):
            (self.work / name).unlink()
        self.env["MOCK_FAILURES"] = json.dumps({ARCHIVES[1]: 22})
        result, events = self.run_update()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(event["command"] == "repo-add" for event in events))
        for name in (*ARCHIVES, "skorion.db", "skorion.files"):
            self.assertFalse(os.path.lexists(self.work / name), name)

    def test_no_packages_aborts_without_modifying_indexes(self):
        for package in (self.work / "output").iterdir():
            package.unlink()
        self.assert_download_rejected()

    def test_invalid_downloads_never_reach_repo_add(self):
        for name in ARCHIVES:
            original = (self.fixtures / name).read_bytes()
            for content in (b"", b"<html>upstream error</html>", original[:len(original) // 2]):
                with self.subTest(archive=name, size=len(content)):
                    (self.fixtures / name).write_bytes(content)
                    self.assert_download_rejected()
            (self.fixtures / name).write_bytes(original)

    def test_mismatched_existing_package_names_are_rejected(self):
        self.make_archive(self.fixtures / ARCHIVES[1], "different-package-1/desc")
        self.assert_download_rejected()

    def test_mismatched_result_or_shrinking_index_is_not_published(self):
        for mode in ("mismatched", "shrink"):
            with self.subTest(mode=mode):
                # Two original entries ensure the shrink case still contains a
                # valid nonempty index, but loses packages unexpectedly.
                for name in ARCHIVES:
                    with tarfile.open(self.fixtures / name, "w:gz") as archive:
                        for entry in ("existing-package-1/desc", "second-package-1/desc"):
                            member = tarfile.TarInfo(entry)
                            member.size = 1
                            archive.addfile(member, io.BytesIO(b"x"))
                self.env["MOCK_REPO_MODE"] = mode
                result, events = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(sum(event["command"] == "repo-add" for event in events), 1)
                self.assert_outputs_unchanged()

    def test_memberless_archive_is_rejected(self):
        for name in ARCHIVES:
            original = (self.fixtures / name).read_bytes()
            with self.subTest(archive=name):
                self.make_archive(self.fixtures / name)
                self.assert_download_rejected()
            (self.fixtures / name).write_bytes(original)

    def test_valid_existing_pair_is_updated_in_staging_then_published(self):
        result, events = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        downloads = [event for event in events if event["command"] == "curl"]
        self.assertEqual({Path(event["output"]).name for event in downloads}, set(ARCHIVES))
        for event in downloads:
            self.assertNotEqual(Path(event["output"]).parent, self.work)
            args = event["args"]
            self.assertTrue("--fail" in args or "--fail-with-body" in args or any(arg.startswith("-") and not arg.startswith("--") and "f" in arg for arg in args))
            self.assertIn("--retry-all-errors", args)
            self.assertIn("--retry", args)
            self.assertGreater(int(args[args.index("--retry") + 1]), 0)
        updates = [event for event in events if event["command"] == "repo-add"]
        self.assertEqual(len(updates), 1)
        update = updates[0]
        self.assertNotEqual(Path(update["database"]).parent, self.work)
        self.assertEqual(set(update["packages"]), {str(path) for path in (self.work / "output").glob("*.pkg.tar.zst")})
        for name in ARCHIVES:
            self.assertIn("existing-package-1/desc", update["entries"][name])
            with tarfile.open(self.work / name, "r:gz") as archive:
                self.assertEqual(set(archive.getnames()), {"existing-package-1/desc", "new-package-1/desc"})
        for alias, target in zip(("skorion.db", "skorion.files"), ARCHIVES):
            self.assertTrue((self.work / alias).is_symlink())
            self.assertEqual(os.readlink(self.work / alias), target)

    def test_repo_add_failure_or_invalid_result_does_not_publish(self):
        for mode in ("fail", *(f"{failure}:{name}" for failure in ("corrupt", "missing", "empty") for name in ARCHIVES)):
            with self.subTest(mode=mode):
                self.env["MOCK_REPO_MODE"] = mode
                result, events = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(sum(event["command"] == "repo-add" for event in events), 1)
                self.assert_outputs_unchanged()


if __name__ == "__main__":
    unittest.main()
