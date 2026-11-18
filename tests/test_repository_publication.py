"""Offline regression tests for non-destructive repository publication."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('download_release_packages', ROOT / 'scripts/download-release-packages.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def asset(name, data=b'package'):
    return {'name': name, 'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
            'browser_download_url': 'https://example.invalid/' + name}


class PackageSetTests(unittest.TestCase):
    def test_names_and_epoch(self):
        for filename, identity, version in [
            ('lib32-mesa-git-27.0_devel.1-1-x86_64.pkg.tar.zst', ('lib32-mesa-git', 'x86_64'), '27.0_devel.1-1'),
            ('foo-bar-2--1.0-1.2-any.pkg.tar.zst', ('foo-bar', 'any'), '2:1.0-1.2'),
        ]:
            self.assertEqual(module.package_identity(filename), (identity, version))
        with self.assertRaises(ValueError):
            module.package_identity('../foo-1-1-any.pkg.tar.zst')

    def test_latest_uses_arch_version_comparator_and_retains_assets(self):
        assets = [asset('foo-1.0-1-any.pkg.tar.zst'), asset('foo-2--0.5-1-any.pkg.tar.zst'),
                  asset('bar-1-1-x86_64.pkg.tar.zst')]
        original = json.dumps(assets)
        with patch.object(module.subprocess, 'check_output', return_value='1\n') as compare:
            selected = module.latest_packages(assets)
        compare.assert_called_once_with(['vercmp', '2:0.5-1', '1.0-1'], text=True)
        self.assertEqual({a['name'] for a in selected}, {assets[1]['name'], assets[2]['name']})
        self.assertEqual(json.dumps(assets), original)

    def test_check_ignores_history_only_after_selection(self):
        new = asset('foo-2-1-any.pkg.tar.zst')
        entries = {new['name']: {'CSIZE': str(new['size']), 'SHA256SUM': new['digest'][7:]}}
        self.assertTrue(module.matches_database([new], entries))
        self.assertFalse(module.matches_database([], entries))
        self.assertFalse(module.matches_database([dict(new, size=999)], entries))
        self.assertFalse(module.matches_database([dict(new, digest='sha256:wrong')], entries))

    def test_partial_or_wrong_download_never_becomes_package(self):
        for data in (b'bad', b'PACKAGE'):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                item = asset('foo-1-1-any.pkg.tar.zst')
                def fetch(url, path):
                    path.write_bytes(data)
                with patch.object(module, 'fetch', side_effect=fetch), self.assertRaises(ValueError):
                    module.download_packages([item], output)
                self.assertEqual(list(output.iterdir()), [])

    def test_success_and_local_extra_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            item = asset('foo-1-1-any.pkg.tar.zst')
            with patch.object(module, 'fetch', side_effect=lambda url, path: path.write_bytes(b'package')):
                module.download_packages([item], output)
            self.assertEqual((output / item['name']).read_bytes(), b'package')
            (output / 'foo-0-1-any.pkg.tar.zst').touch()
            with patch.object(module, 'fetch') as fetch, self.assertRaises(ValueError):
                module.download_packages([item], output)
            fetch.assert_not_called()

    def test_curl_failure_is_not_swallowed(self):
        with patch.object(module.subprocess, 'run', side_effect=subprocess.CalledProcessError(92, 'curl')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                module.fetch('https://example.invalid/pkg', Path('/tmp/unused'))
        args = run.call_args.args[0]
        for option in ('--fail', '--show-error', '--http1.1', '--retry-all-errors'):
            self.assertIn(option, args)
        self.assertTrue(run.call_args.kwargs['check'])

    def test_snapshot_digest_comes_from_database(self):
        with tempfile.TemporaryDirectory() as directory:
            item = asset('foo-1-1-any.pkg.tar.zst')
            with patch.object(module, 'fetch', side_effect=lambda url, path: path.write_bytes(b'package')):
                with self.assertRaises(ValueError):
                    module.download_packages([item], Path(directory), {item['name']: {'SHA256SUM': 'bad'}})

    def test_missing_digest_fails_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            item = dict(asset('foo-1-1-any.pkg.tar.zst'), digest=None)
            with patch.object(module, 'fetch') as fetch, self.assertRaisesRegex(ValueError, 'Missing trustworthy'):
                module.download_packages([item], Path(directory))
            fetch.assert_not_called()

    def test_database_can_supply_missing_asset_digest(self):
        item = dict(asset('foo-1-1-any.pkg.tar.zst'), digest=None)
        entries = {item['name']: {'CSIZE': str(item['size']), 'SHA256SUM': hashlib.sha256(b'package').hexdigest()}}
        self.assertFalse(module.matches_database([item], entries))
        self.assertTrue(module.matches_database([item], entries, require_digest=False))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, 'fetch', side_effect=lambda url, path: path.write_bytes(b'package')):
                module.download_packages([item], Path(directory), entries)
            self.assertEqual((Path(directory) / item['name']).read_bytes(), b'package')

    def test_download_failure_leaves_no_usable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(url, path):
                path.write_bytes(b'partial')
                raise subprocess.CalledProcessError(92, 'curl')
            with patch.object(module, 'fetch', side_effect=fail), self.assertRaises(subprocess.CalledProcessError):
                module.download_packages([asset('foo-1-1-any.pkg.tar.zst')], Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


class IncrementalIndexTests(unittest.TestCase):
    def test_failed_or_corrupt_download_cannot_truncate_index(self):
        for scenario in ('network', 'corrupt', 'one_missing', 'success', 'both_missing'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / 'bin'
                binary.mkdir()
                with tarfile.open(root / 'fixture.tar.gz', 'w:gz') as archive:
                    data = b'existing index entry'
                    member = tarfile.TarInfo('existing-1/desc')
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                scripts = {
                    'pacman': '#!/bin/sh\nexit 0\n',
                    'curl': """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = '-o' ]; then shift; output="$1"; fi
  shift
done
case "$SCENARIO:$output" in
  network:*) printf 503; exit 22;;
  corrupt:*) printf broken > "$output"; printf 200;;
  one_missing:skorion.files.tar.gz|both_missing:*) printf 404; exit 22;;
  *) cp fixture.tar.gz "$output"; printf 200;;
esac
""",
                    'repo-add': """#!/bin/sh
if [ "$SCENARIO" = success ]; then
  test -f skorion.db.tar.gz && test -f skorion.files.tar.gz || exit 10
fi
touch repo-add-called
""",
                }
                for name, text in scripts.items():
                    path = binary / name
                    path.write_text(text)
                    path.chmod(0o755)
                env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], SCENARIO=scenario)
                result = subprocess.run(['bash', str(ROOT / 'scripts/update-repo-database.sh'), 'owner/repo'],
                                        cwd=root, env=env, capture_output=True, text=True)
                expected_success = scenario in ('success', 'both_missing')
                self.assertEqual(result.returncode == 0, expected_success, result.stdout + result.stderr)
                self.assertEqual((root / 'repo-add-called').exists(), expected_success)


class PublicationWorkflowTests(unittest.TestCase):
    def test_automatic_workflow_never_deletes_old_assets(self):
        text = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertNotIn('clean-old-assets.sh', text)
        self.assertNotIn('curl -sL', text)
        self.assertIn('--database output/skorion.db.tar.gz', text)
        self.assertIn('--check-database output/skorion.db.tar.gz', text)
        document = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(document['concurrency']['cancel-in-progress'], 'false')

    def test_manual_cleanup_defaults_to_read_only(self):
        document = yaml.load((ROOT / '.github/workflows/cleanup-duplicates.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(document['on']['workflow_dispatch']['inputs']['dry_run']['default'], 'true')

    def test_database_container_full_upgrade_and_both_indexes(self):
        text = (ROOT / 'scripts/update-repo-database.sh').read_text()
        self.assertIn('pacman -Syu --noconfirm pacman-contrib curl', text)
        self.assertNotIn('pacman -Sy --', text)
        self.assertIn('download_index skorion.db.tar.gz', text)
        self.assertIn('download_index skorion.files.tar.gz', text)
        self.assertIn('"$status" = "404"', text)

    def test_manual_validation_has_empty_build_matrix(self):
        text = (ROOT / '.github/workflows/validate.yml').read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertIn("matrix='[]'", text)
        self.assertIn('if [ -n "$PR_BASE_SHA" ]; then', text)


if __name__ == '__main__':
    unittest.main()
