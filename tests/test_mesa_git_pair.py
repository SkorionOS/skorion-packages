"""Offline coverage for both Mesa production publication paths."""
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
spec = importlib.util.spec_from_file_location('publication', ROOT / 'scripts/download-release-packages.py')
publ = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publ)

OLD_COMMIT = '0e9e1086bb8'
NEW_COMMIT = '6f23942285e'
FULL_COMMIT = NEW_COMMIT + '1' * 29


def asset(name, count=234174, commit=OLD_COMMIT, patch_digest='d41d8cd', pkgrel='1'):
    basename = f'{name}-27.0.0_devel.{count}.{commit}.{patch_digest}-{pkgrel}-x86_64.pkg.tar.zst'
    return {'name': basename, 'digest': 'sha256:' + hashlib.sha256(basename.encode()).hexdigest(),
            'size': len(basename), 'browser_download_url': 'https://example.invalid/' + basename}


def compare(args, **kwargs):
    _, left, right = args
    # Fixtures differ by a fixed-width commit count, so lexical ordering
    # models vercmp for these examples only.
    return str((left > right) - (left < right))


class MesaPairTests(unittest.TestCase):
    def setUp(self):
        self.mesa_old = asset('mesa-git')
        self.lib32_old = asset('lib32-mesa-git')
        self.mesa_new = asset('mesa-git', 234237, NEW_COMMIT)
        self.lib32_new = asset('lib32-mesa-git', 234237, NEW_COMMIT, 'ae19bbb', '2')
        self.other = asset('unrelated')
        self.history = [self.mesa_old, self.lib32_old]
        self.vercmp = patch.object(publ.subprocess, 'check_output', side_effect=compare)
        self.vercmp.start()
        self.addCleanup(self.vercmp.stop)

    def test_lone_new_half_keeps_old_pair_and_unrelated_update(self):
        for staged in (self.mesa_new, self.lib32_new):
            with self.subTest(name=staged['name']):
                self.assertCountEqual(publ.latest_packages(self.history + [staged, self.other]),
                                      [*self.history, self.other])

    def test_pair_promotes_together_for_same_commit_even_if_patch_digest_differs(self):
        self.assertCountEqual(publ.latest_packages(self.history + [self.mesa_new, self.lib32_new, self.other]),
                              [self.mesa_new, self.lib32_new, self.other])

    def test_wrong_commit_matching_count_and_invalid_digest_never_advance(self):
        for unusable in (asset('lib32-mesa-git', 234237, '123456789ab'),
                         dict(self.lib32_new, digest=None)):
            with self.subTest(name=unusable['name'], digest=unusable['digest']):
                self.assertCountEqual(publ.latest_packages(self.history + [self.mesa_new, unusable]),
                                      self.history)

    def test_no_valid_pair_fails_closed_instead_of_removing_mesa(self):
        with self.assertRaisesRegex(ValueError, 'No complete verified Mesa git pair'):
            publ.latest_packages([self.lib32_new, self.other])
        self.assertEqual(publ.latest_packages([self.other]), [self.other])

    def test_rebuild_checks_download_digest_before_using_pair(self):
        candidate = publ.latest_packages(self.history + [self.mesa_new, self.lib32_new])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)

            def fetch(url, dest):
                name = url.rsplit('/', 1)[-1]
                content = name if name == self.lib32_new['name'] else 'X' * len(name)
                dest.write_bytes(content.encode())

            with patch.object(publ, 'fetch', side_effect=fetch), self.assertRaisesRegex(ValueError, 'SHA256'):
                publ.download_packages(candidate, path)
            self.assertFalse((path / self.mesa_new['name']).exists())

    def test_rebuild_cli_does_not_invalidate_old_index_until_both_members_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / 'skorion.db.tar.gz'
            assets = self.history + [self.lib32_new, self.other]
            with tarfile.open(index, 'w:gz') as archive:
                for item in [*self.history, self.other]:
                    filename = item['name']
                    desc = (f'%FILENAME%\n{filename}\n\n%CSIZE%\n{item["size"]}\n\n'
                            f'%SHA256SUM%\n{item["digest"][7:]}\n').encode()
                    member = tarfile.TarInfo(f'{filename}/desc')
                    member.size = len(desc)
                    archive.addfile(member, io.BytesIO(desc))

            def fetch(url, dest):
                if url.endswith('/releases/tags/latest'):
                    dest.write_text(json.dumps({'assets': assets}))
                else:
                    dest.write_text(url.rsplit('/', 1)[-1])

            with patch.object(publ, 'fetch', side_effect=fetch):
                with patch('sys.argv', ['download', 'org/repo', '--check-database', str(index)]):
                    publ.main()
                    assets.append(self.mesa_new)
                    with self.assertRaises(SystemExit) as result:
                        publ.main()
                    self.assertEqual(result.exception.code, 1)
                output = root / 'output'
                with patch('sys.argv', ['download', 'org/repo', '--output', str(output)]):
                    publ.main()
                self.assertCountEqual([p.name for p in output.iterdir()],
                                      [a['name'] for a in [self.mesa_new, self.lib32_new, self.other]])

    def test_incremental_single_package_bypass_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'output').mkdir()
            (root / 'output' / self.lib32_new['name']).touch()
            result = subprocess.run(['bash', str(ROOT / 'scripts/update-repo-database.sh'), 'org/repo'],
                                    cwd=root, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('not eligible for direct incremental indexing', result.stderr)


class MesaMatrixTests(unittest.TestCase):
    def test_companion_is_added_or_both_are_explicitly_skipped(self):
        workflow = yaml.load((ROOT / '.github/workflows/build.yml').read_text(), Loader=yaml.BaseLoader)
        shell = next(s['run'] for s in workflow['jobs']['list-packages']['steps'] if s.get('id') == 'set-all')
        for detected, skip, expected in (
            (['mesa-git', 'unrelated'], '', ['mesa-git', 'lib32-mesa-git', 'unrelated']),
            (['lib32-mesa-git', 'unrelated'], '', ['mesa-git', 'lib32-mesa-git', 'unrelated']),
            (['mesa-git', 'unrelated'], 'foo, lib32-mesa-git ', ['unrelated']),
            (['unrelated'], '', ['unrelated']),
        ):
            with self.subTest(detected=detected, skip=skip), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'out'
                script = shell.replace('${{ steps.set-aur.outputs.matrix }}', json.dumps(detected))
                script = script.replace('${{ steps.set-local.outputs.matrix }}', '["local-pkg"]')
                subprocess.run(['bash', '-ec', script], check=True, capture_output=True,
                               env=dict(os.environ, GITHUB_OUTPUT=str(output), SKIP_PACKAGES=skip))
                matrix = json.loads(output.read_text().removeprefix('matrix='))
                self.assertCountEqual(matrix, [{'package': name, 'type': 'aur'} for name in expected] +
                                      [{'package': 'local-pkg', 'type': 'local'}])

    def test_workflow_has_no_direct_mesa_index_publication(self):
        workflow = yaml.load((ROOT / '.github/workflows/build.yml').read_text(), Loader=yaml.BaseLoader)
        jobs = workflow['jobs']
        steps = jobs['build-packages']['steps']
        for action in ('Acquire database lock', 'Upload package to latest release'):
            condition = next(s['if'] for s in steps if s.get('name') == action)
            self.assertIn("matrix.package != 'mesa-git'", condition)
            self.assertIn("matrix.package != 'lib32-mesa-git'", condition)
        for job in ('generate-snapshot', 'finalize-incremental'):
            commands = ''.join(s.get('run', '') for s in jobs[job]['steps'])
            self.assertIn('scripts/download-release-packages.py', commands)


class MesaStageTests(unittest.TestCase):
    def test_verified_filename_is_staged_without_clobber_and_old_asset_preserved(self):
        for scenario in ('new', 'exists', 'wrong-source'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output = root / 'out'
                output.mkdir()
                item = asset('mesa-git', 234237, OLD_COMMIT if scenario == 'wrong-source' else NEW_COMMIT)
                (output / item['name']).write_text(item['name'])
                (root / 'manifest').write_text(json.dumps({'assets': [item] if scenario == 'exists' else []}))
                gh = root / 'gh'
                gh.write_text('#!/bin/sh\nif [ "$1" = api ]; then cat manifest; else echo "$*" >> uploads; fi\n')
                gh.chmod(0o755)
                result = subprocess.run(['bash', str(ROOT / 'scripts/stage-mesa-git.sh'), 'org/repo',
                                         'mesa-git', FULL_COMMIT, 'out'], cwd=root,
                                        env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH']),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, scenario != 'wrong-source', result.stderr)
                if scenario == 'new':
                    self.assertEqual((root / 'uploads').read_text().strip(),
                                     'release upload latest out/' + item['name'] + ' --repo org/repo')
                else:
                    self.assertFalse((root / 'uploads').exists())


if __name__ == '__main__':
    unittest.main()
