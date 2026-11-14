#!/usr/bin/env python3
"""Explicit index-only recovery from a verified snapshot plus current package assets.

Never builds, removes or replaces a package or historical release. Preparation
backs up current indexes; publication requires the release manifest to be unchanged.
Run only while holding the repository database lock and publication concurrency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from release_assets import download, fingerprint, release

INDEXES = ('skorion.db.tar.gz', 'skorion.files.tar.gz', 'skorion.db', 'skorion.files')


def entries(path):
    result = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            parts = member.name.rstrip('/').split('/')
            if any(p in ('', '.', '..') for p in parts) or len(parts) > 2 or member.name.startswith('/'):
                raise ValueError('Unsafe archive path')
            if member.isdir() and len(parts) == 1:
                continue
            if not member.isfile() or len(parts) != 2 or parts[1] not in ('desc', 'files') or member.size > 16 * 1024 * 1024:
                raise ValueError('Unexpected database member')
            if member.name in result:
                raise ValueError('Duplicate database member')
            result[member.name] = archive.extractfile(member).read()
    if not result:
        raise ValueError('Empty database')
    return result


def descriptions(path):
    result = {}
    for name, data in entries(path).items():
        if not name.endswith('/desc'):
            continue
        fields = {}
        for block in data.decode().strip().split('\n\n'):
            lines = block.splitlines()
            if lines and lines[0].startswith('%'):
                fields[lines[0].strip('%')] = lines[1:]
        package = fields['NAME'][0]
        if package in result:
            raise ValueError('Duplicate package name')
        result[package] = (name, data, fields)
    return result


def verify(directory, manifest):
    db = descriptions(directory / 'skorion.db.tar.gz')
    files = descriptions(directory / 'skorion.files.tar.gz')
    file_entries = entries(directory / 'skorion.files.tar.gz')
    assets = {a['name']: a for a in manifest['assets'] if a['name'].endswith('.pkg.tar.zst')}
    if not assets or set(db) != set(files) or len(db) != len(assets):
        raise ValueError('Package count/identity mismatch between indexes and release assets')
    seen = set()
    for package, (name, data, fields) in db.items():
        if files[package][:2] != (name, data) or name[:-4] + 'files' not in file_entries:
            raise ValueError(f'Missing/inconsistent file index for {package}')
        filename = fields['FILENAME'][0]
        asset = assets[filename]
        if filename in seen or int(fields['CSIZE'][0]) != asset['size'] or 'sha256:' + fields['SHA256SUM'][0] != asset.get('digest'):
            raise ValueError(f'Asset metadata mismatch for {package}')
        seen.add(filename)
    if seen != set(assets):
        raise ValueError('Incomplete repository index')
    return len(db)


def prepare(repository, snapshot, root):
    if snapshot == 'latest':
        raise ValueError('Recovery requires an explicit historical snapshot')
    root.mkdir(parents=True, exist_ok=False)
    latest = release(repository, 'latest')
    baseline = release(repository, snapshot)
    (root / 'manifest.json').write_text(json.dumps(latest, indent=2))
    (root / 'baseline.json').write_text(json.dumps(baseline, indent=2))
    before = {a['name']: a for a in latest['assets']}
    older = {a['name']: a for a in baseline['assets']}
    for name in INDEXES:
        download(before[name], root / 'backup')
    for name in INDEXES[:2]:
        download(older[name], root / 'output')
    # This proves the recovery source was internally complete before using it.
    verify(root / 'output', baseline)
    old_assets = {a['name']: a for a in baseline['assets'] if a['name'].endswith('.pkg.tar.zst')}
    wanted = {a['name']: a for a in latest['assets'] if a['name'].endswith('.pkg.tar.zst')}
    changed = []
    for name, asset in wanted.items():
        if name not in old_assets or (asset['size'], asset['digest']) != (old_assets[name]['size'], old_assets[name]['digest']):
            changed.append(download(asset, root / 'packages'))
    if changed:
        subprocess.run(['repo-add', 'skorion.db.tar.gz', *[str(p.resolve()) for p in changed]], cwd=root / 'output', check=True)
    count = verify(root / 'output', latest)
    for alias, target in [('skorion.db', 'skorion.db.tar.gz'), ('skorion.files', 'skorion.files.tar.gz')]:
        destination = root / 'output' / alias
        destination.unlink(missing_ok=True)
        shutil.copyfile(root / 'output' / target, destination)
    if fingerprint(latest) != fingerprint(release(repository, 'latest')):
        raise ValueError('Release changed during preparation; restart with fresh inputs')
    report = {'snapshot': snapshot, 'packages': count, 'downloaded_packages': [p.name for p in changed],
              'indexes': {n: hashlib.sha256((root / 'output' / n).read_bytes()).hexdigest() for n in INDEXES}}
    (root / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def publish(repository, root):
    before = json.loads((root / 'manifest.json').read_text())
    current = release(repository, 'latest')
    if fingerprint(before) != fingerprint(current):
        raise ValueError('Concurrent release change; nothing uploaded')
    verify(root / 'output', before)
    report = json.loads((root / 'report.json').read_text())
    for name in INDEXES:
        digest = hashlib.sha256((root / 'output' / name).read_bytes()).hexdigest()
        if digest != report['indexes'][name]:
            raise ValueError(f'Prepared index changed: {name}')
    for alias, target in [('skorion.db', 'skorion.db.tar.gz'), ('skorion.files', 'skorion.files.tar.gz')]:
        if (root / 'output' / alias).read_bytes() != (root / 'output' / target).read_bytes():
            raise ValueError(f'Index alias differs: {alias}')
    # Only these four index assets may change. Backup artifacts are retained by CI.
    for name in INDEXES:
        subprocess.run(['gh', 'release', 'upload', 'latest', str(root / 'output' / name), '--repo', repository, '--clobber'], check=True)
    after = release(repository, 'latest')
    if fingerprint(before, True) != fingerprint(after, True):
        raise ValueError('Package assets changed during publication')
    by_name = {a['name']: a for a in after['assets']}
    for name in INDEXES:
        received = download(by_name[name], root / 'verified')
        if received.read_bytes() != (root / 'output' / name).read_bytes():
            raise ValueError(f'Published bytes differ: {name}')
    count = verify(root / 'verified', after)
    print(f'Published and independently downloaded all four indexes: {count} packages; package assets unchanged')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'publish'])
    parser.add_argument('repository')
    parser.add_argument('directory', type=Path)
    parser.add_argument('--snapshot', default='2026.11.08')
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.repository, args.snapshot, args.directory)
    else:
        publish(args.repository, args.directory)


if __name__ == '__main__':
    main()
