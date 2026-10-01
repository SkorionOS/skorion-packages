"""Offline regression tests: python3 -m unittest discover -s tests -v."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "upload-release-assets.sh"


class UploadReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "snapshot assets"
        self.assets.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "MOCK_LOG": str(self.log),
            "MOCK_FAILURES": "{}",
        }
        # Only mocks run: no requests, credentials, or real sleeps are needed.
        self.write_command("gh", r'''
import json, os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ["MOCK_LOG"])
events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(["gh", *args]) + "\n")
name = pathlib.Path(args[3]).name
attempt = sum(event[0] == "gh" and event[4] == args[3] for event in events) + 1
if attempt <= json.loads(os.environ["MOCK_FAILURES"]).get(name, 0):
    print("socket hang up", file=sys.stderr)
    sys.exit(1)
''')
        self.write_command("sleep", r'''
import json, os, sys
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write(json.dumps(["sleep", *sys.argv[1:]]) + "\n")
''')

    def write_command(self, name, source):
        command = self.bin / name
        command.write_text("#!/usr/bin/env python3\n" + source)
        command.chmod(0o755)

    def run_upload(self, failures=None, args=None):
        self.env["MOCK_FAILURES"] = json.dumps(failures or {})
        result = subprocess.run(
            ["bash", str(SCRIPT), *(args if args is not None else [
                "example/packages", "2026.09.30", str(self.assets)
            ])],
            env=self.env, text=True, capture_output=True, timeout=10,
        )
        events = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, events

    def add_asset(self, name):
        asset = self.assets / name
        asset.write_text("package data")
        return asset

    def test_uploads_one_file_at_a_time_including_symlinks_and_spaces(self):
        first = self.add_asset("a package.pkg.tar.zst")
        database = self.add_asset("skorion.db.tar.gz")
        alias = self.assets / "skorion.db"
        alias.symlink_to(database.name)
        (self.assets / "subdirectory").mkdir()
        self.add_asset(".hidden")

        result, events = self.run_upload()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, [
            ["gh", "release", "upload", "2026.09.30", str(asset),
             "--repo", "example/packages", "--clobber"]
            for asset in [first, alias, database]
        ])
        self.assertIn("Uploaded 3 release assets", result.stdout)

    def test_retries_only_failed_asset_with_exponential_backoff(self):
        self.add_asset("a.pkg.tar.zst")
        self.add_asset("b.pkg.tar.zst")
        self.add_asset("c.pkg.tar.zst")

        result, events = self.run_upload({"b.pkg.tar.zst": 2})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [Path(event[4]).name if event[0] == "gh" else f"sleep {event[1]}" for event in events],
            ["a.pkg.tar.zst", "b.pkg.tar.zst", "sleep 5", "b.pkg.tar.zst",
             "sleep 10", "b.pkg.tar.zst", "c.pkg.tar.zst"],
        )

    def test_fails_after_five_attempts_without_uploading_later_assets(self):
        self.add_asset("a.pkg.tar.zst")
        self.add_asset("b.pkg.tar.zst")

        result, events = self.run_upload({"a.pkg.tar.zst": 99})

        self.assertNotEqual(result.returncode, 0)
        uploads = [event for event in events if event[0] == "gh"]
        self.assertEqual(len(uploads), 5)
        self.assertTrue(all(Path(event[4]).name == "a.pkg.tar.zst" for event in uploads))
        self.assertEqual([event[1] for event in events if event[0] == "sleep"], ["5", "10", "20", "40"])
        self.assertIn("Failed to upload a.pkg.tar.zst after 5 attempts", result.stderr)
        self.assertNotIn("Uploaded 2 release assets", result.stdout)

    def test_succeeds_on_last_attempt(self):
        self.add_asset("a.pkg.tar.zst")

        result, events = self.run_upload({"a.pkg.tar.zst": 4})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(event[0] == "gh" for event in events), 5)
        self.assertIn("Uploaded 1 release assets", result.stdout)

    def test_empty_directory_fails_without_upload(self):
        result, events = self.run_upload()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIn("No release assets", result.stderr)

    def test_invalid_arguments_fail_without_upload(self):
        for args in [[], ["example/packages", "2026.09.30", str(self.root / "missing")]]:
            with self.subTest(args=args):
                result, events = self.run_upload(args=args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events, [])
                self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
