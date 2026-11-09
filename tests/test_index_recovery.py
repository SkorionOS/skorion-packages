"""Offline integrity and publication guards for explicit repository recovery."""

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import urllib.error

import yaml


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_assets = load_script("release_assets", "release_assets.py")
with mock.patch.dict(sys.modules, {"release_assets": release_assets}):
    recovery = load_script("repair_repo_index", "repair-repo-index.py")


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status


def asset(name, data, identifier=1):
    return {
        "name": name,
        "size": len(data),
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "id": identifier,
        "updated_at": "2026-11-09T00:00:00Z",
        "browser_download_url": "https://github.com/example/packages/releases/download/latest/" + name,
    }


class ReleaseAssetDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b"verified package contents"
        self.asset = asset("package.pkg.tar.zst", self.payload)
        self.target = self.root / self.asset["name"]
        self.target.write_bytes(b"previous good asset")

    def test_download_rejects_http_size_and_digest_failures_without_replacing_target(self):
        cases = {
            "http404": lambda: Response(b"not found", 404),
            "http503": lambda: Response(b"unavailable", 503),
            "short": lambda: Response(self.payload[:-1]),
            "oversized": lambda: Response(self.payload + b"!"),
            "wrong_digest": lambda: Response(b"x" * len(self.payload)),
        }
        for name, response in cases.items():
            with self.subTest(case=name), mock.patch.object(
                release_assets.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: response()
            ) as urlopen, mock.patch.object(release_assets.time, "sleep") as sleep:
                with self.assertRaises(ValueError):
                    release_assets.download(self.asset, self.root)
                self.assertEqual(urlopen.call_count, 5)
                self.assertEqual(sleep.call_args_list, [mock.call(n) for n in (1, 2, 4, 8)])
                self.assertEqual(self.target.read_bytes(), b"previous good asset")
                self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_urlopen_http_error_is_retried_without_replacing_target(self):
        error = urllib.error.HTTPError(self.asset["browser_download_url"], 404, "Not Found", {}, None)
        with mock.patch.object(release_assets.urllib.request, "urlopen", side_effect=error) as urlopen, mock.patch.object(release_assets.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                release_assets.download(self.asset, self.root)
        self.assertEqual(urlopen.call_count, 5)
        self.assertEqual(self.target.read_bytes(), b"previous good asset")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_successful_retry_atomically_replaces_target_with_verified_bytes(self):
        with mock.patch.object(release_assets.urllib.request, "urlopen", side_effect=[OSError("temporary failure"), Response(self.payload)]), mock.patch.object(release_assets.time, "sleep") as sleep:
            result = release_assets.download(self.asset, self.root)
        self.assertEqual(result, self.target)
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(list(self.root.iterdir()), [self.target])
        sleep.assert_called_once_with(1)

    def test_missing_digest_or_invalid_size_fails_before_network(self):
        for changes in ({"digest": None}, {"digest": ""}, {"size": 0}):
            with self.subTest(changes=changes), mock.patch.object(release_assets.urllib.request, "urlopen") as urlopen:
                with self.assertRaises((ValueError, TypeError)):
                    release_assets.download({**self.asset, **changes}, self.root)
                urlopen.assert_not_called()
                self.assertEqual(self.target.read_bytes(), b"previous good asset")


class RecoveryVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.package = asset("example-1-1-any.pkg.tar.zst", b"package bytes")
        self.manifest = {"assets": [self.package]}
        self.desc = self.description(self.package)
        self.write_indexes()

    @staticmethod
    def description(package):
        return ("%NAME%\nexample\n\n%FILENAME%\n" + package["name"] +
                "\n\n%CSIZE%\n" + str(package["size"]) +
                "\n\n%SHA256SUM%\n" + package["digest"].split(":", 1)[1] + "\n").encode()

    @staticmethod
    def write_tar(path, members):
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def write_indexes(self, db_desc=None, files_desc=None, include_files=True):
        self.write_tar(self.output / "skorion.db.tar.gz", {"example-1-1/desc": db_desc or self.desc})
        members = {"example-1-1/desc": files_desc or self.desc}
        if include_files:
            members["example-1-1/files"] = b"%FILES%\nusr/bin/example\n"
        self.write_tar(self.output / "skorion.files.tar.gz", members)

    def test_complete_indexes_are_accepted(self):
        self.assertEqual(recovery.verify(self.output, self.manifest), 1)

    def test_missing_package_is_rejected(self):
        extra = asset("missing-1-1-any.pkg.tar.zst", b"missing", 2)
        with self.assertRaises(ValueError):
            recovery.verify(self.output, {"assets": [self.package, extra]})

    def test_missing_file_listing_is_rejected(self):
        self.write_indexes(include_files=False)
        with self.assertRaisesRegex(ValueError, "file index"):
            recovery.verify(self.output, self.manifest)

    def test_mismatched_descriptions_are_rejected(self):
        self.write_indexes(files_desc=self.desc + b"\n%EXTRA%\nchanged\n")
        with self.assertRaisesRegex(ValueError, "file index"):
            recovery.verify(self.output, self.manifest)

    def test_mismatched_asset_hash_or_size_is_rejected(self):
        for changes in ({"size": self.package["size"] + 1}, {"digest": "sha256:" + "0" * 64}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "metadata mismatch"):
                recovery.verify(self.output, {"assets": [{**self.package, **changes}]})

    def test_wrong_package_filename_is_rejected(self):
        different = {**self.package, "name": "different-1-1-any.pkg.tar.zst"}
        with self.assertRaises((ValueError, KeyError)):
            recovery.verify(self.output, {"assets": [different]})

    def test_concurrent_manifest_change_prevents_every_upload(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        for key, value in (("id", 100), ("size", 200), ("digest", "sha256:" + "f" * 64), ("updated_at", "2026-11-09T01:00:00Z")):
            changed = copy.deepcopy(self.manifest)
            changed["assets"][0][key] = value
            with self.subTest(field=key), mock.patch.object(recovery, "release", return_value=changed), mock.patch.object(recovery.subprocess, "run") as run, mock.patch.object(recovery, "download") as download:
                with self.assertRaisesRegex(ValueError, "Concurrent release change"):
                    recovery.publish("example/packages", self.root)
                run.assert_not_called()
                download.assert_not_called()

    def prepare_publication_files(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        for alias, target in (("skorion.db", "skorion.db.tar.gz"), ("skorion.files", "skorion.files.tar.gz")):
            (self.output / alias).write_bytes((self.output / target).read_bytes())
        report = {"indexes": {name: hashlib.sha256((self.output / name).read_bytes()).hexdigest() for name in recovery.INDEXES}}
        (self.root / "report.json").write_text(json.dumps(report))

    def test_missing_or_modified_alias_is_rejected_before_every_upload(self):
        for alias in ("skorion.db", "skorion.files"):
            for mode in ("missing", "modified", "modified_with_updated_report"):
                with self.subTest(alias=alias, mode=mode):
                    self.prepare_publication_files()
                    if mode == "missing":
                        (self.output / alias).unlink()
                    else:
                        (self.output / alias).write_bytes(b"incorrect alias bytes")
                        if mode == "modified_with_updated_report":
                            report = json.loads((self.root / "report.json").read_text())
                            report["indexes"][alias] = hashlib.sha256((self.output / alias).read_bytes()).hexdigest()
                            (self.root / "report.json").write_text(json.dumps(report))
                    with mock.patch.object(recovery, "release", return_value=self.manifest), mock.patch.object(recovery.subprocess, "run") as run, mock.patch.object(recovery, "download") as download:
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            recovery.publish("example/packages", self.root)
                        run.assert_not_called()
                        download.assert_not_called()

    def test_prepare_rejects_latest_before_network_or_filesystem_changes(self):
        destination = self.root / "not-created"
        with mock.patch.object(recovery, "release") as release, mock.patch.object(recovery.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "historical snapshot"):
                recovery.prepare("example/packages", "latest", destination)
        self.assertFalse(destination.exists())
        release.assert_not_called()
        run.assert_not_called()


class RecoveryWorkflowTests(unittest.TestCase):
    @staticmethod
    def workflow(name):
        path = SCRIPTS.parent / ".github" / "workflows" / name
        return yaml.load(path.read_text(), Loader=yaml.BaseLoader)

    def test_repair_requires_master_and_marker_or_manual_trigger(self):
        workflow = self.workflow("repair-index.yml")
        self.assertEqual(set(workflow["on"]), {"push", "workflow_dispatch"})
        self.assertEqual(workflow["on"]["push"]["branches"], ["master"])
        condition = " ".join(workflow["jobs"]["repair"]["if"].split())
        self.assertEqual(condition,
            "github.ref == 'refs/heads/master' && "
            "(github.event_name == 'workflow_dispatch' || "
            "(github.event_name == 'push' && contains(github.event.head_commit.message, '[repair-index]')))")

    def test_all_publication_and_cleanup_jobs_share_non_cancelling_concurrency(self):
        expected = {"group": "skorion-release-publication", "cancel-in-progress": "false"}
        for name in ("repair-index.yml", "build.yml", "cleanup-duplicates.yml", "cleanup-locks.yml"):
            with self.subTest(workflow=name):
                self.assertEqual(self.workflow(name)["concurrency"], expected)

    def test_repair_marker_skips_detection_and_downstream_finalization(self):
        jobs = self.workflow("build.yml")["jobs"]
        self.assertEqual(jobs["list-packages"]["if"],
            "github.event_name != 'push' || !contains(github.event.head_commit.message, '[repair-index]')")
        for name in ("generate-snapshot", "finalize-incremental"):
            with self.subTest(job=name):
                self.assertIn("list-packages", jobs[name]["needs"])
                condition = " ".join(jobs[name]["if"].split())
                self.assertTrue(condition.startswith("always() && needs.list-packages.result == 'success' &&"), condition)


if __name__ == "__main__":
    unittest.main()
