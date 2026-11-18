#!/usr/bin/env python3
"""Download a verified repository package set without deleting release history."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.parse import unquote


def fetch(url, destination):
    # HTTP/1.1 avoids curl 92 (HTTP/2 stream errors); failures remain visible.
    subprocess.run([
        'curl', '--fail', '--show-error', '--silent', '--location', '--http1.1',
        '--retry', '4', '--retry-all-errors', '--retry-delay', '3',
        '--connect-timeout', '30', '--max-time', '1800',
        url, '--output', str(destination),
    ], check=True)


def package_identity(filename):
    match = re.fullmatch(r'(.+?)-(?:(\d+)--)?([^-]+)-([^-]+)-([^-]+)\.pkg\.tar\.zst', filename)
    if not match or '/' in filename or '\\' in filename:
        raise ValueError(f'Invalid package filename: {filename}')
    name, epoch, version, release, arch = match.groups()
    return (name, arch), f'{epoch + ":" if epoch else ""}{version}-{release}'


def latest_packages(assets):
    selected = {}
    for asset in assets:
        filename = unquote(asset['name'])
        if not filename.endswith('.pkg.tar.zst'):
            continue
        identity, version = package_identity(filename)
        if identity in selected:
            previous, previous_version = selected[identity]
            comparison = int(subprocess.check_output(['vercmp', version, previous_version], text=True).strip())
            if comparison == 0 and filename != unquote(previous['name']):
                raise ValueError(f'Ambiguous equal versions: {filename}, {previous["name"]}')
            if comparison <= 0:
                continue
        selected[identity] = (asset, version)
    if not selected:
        raise ValueError('Release has no packages')
    return sorted((item[0] for item in selected.values()), key=lambda a: a['name'])


def database_entries(path):
    entries = {}
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith('/desc'):
                continue
            text = archive.extractfile(member).read().decode()
            fields = {}
            for block in text.strip().split('\n\n'):
                lines = block.splitlines()
                if len(lines) >= 2:
                    fields[lines[0].strip('%')] = lines[1]
            filename = fields['FILENAME']
            package_identity(filename)
            if filename in entries:
                raise ValueError(f'Duplicate database entry: {filename}')
            entries[filename] = fields
    if not entries:
        raise ValueError('Empty repository database')
    return entries


def matches_database(assets, entries, require_digest=True):
    by_name = {unquote(asset['name']): asset for asset in assets}
    if set(by_name) != set(entries):
        return False
    for name, entry in entries.items():
        asset = by_name[name]
        if int(entry['CSIZE']) != asset['size']:
            return False
        if (require_digest or asset.get('digest')) and asset.get('digest') != 'sha256:' + entry['SHA256SUM']:
            return False
    return True


def download_packages(assets, output, entries=None):
    output.mkdir(parents=True, exist_ok=True)
    names = {unquote(asset['name']) for asset in assets}
    # Never accidentally include an older local package in repo-add's glob.
    extras = {p.name for p in output.glob('*.pkg.tar.zst')} - names
    if extras:
        raise ValueError(f'Output contains unselected packages: {sorted(extras)}')
    for asset in assets:
        name = unquote(asset['name'])
        package_identity(name)
        expected = ('sha256:' + entries[name]['SHA256SUM']) if entries is not None else asset.get('digest')
        if not expected or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected):
            raise ValueError(f'Missing trustworthy SHA256: {name}')
        print(f'Downloading {name}', flush=True)
        destination = output / name
        temporary = output / (name + '.part')
        try:
            fetch(asset['browser_download_url'], temporary)
            if temporary.stat().st_size != asset['size']:
                raise ValueError(f'Size mismatch: {name}')
            digest = hashlib.sha256()
            with temporary.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if expected != 'sha256:' + digest.hexdigest():
                raise ValueError(f'SHA256 mismatch: {name}')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository')
    parser.add_argument('--tag', default='latest')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--database', type=Path, help='Download exactly this database package set')
    parser.add_argument('--check-database', type=Path, help='Check database against selected latest versions; no package downloads')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        metadata = Path(directory) / 'release.json'
        fetch(f'https://api.github.com/repos/{args.repository}/releases/tags/{args.tag}', metadata)
        assets = json.loads(metadata.read_text())['assets']
    if args.database:
        entries = database_entries(args.database)
        selected = [a for a in assets if unquote(a['name']) in entries]
        if not matches_database(selected, entries, require_digest=False):
            raise ValueError('Release assets do not match snapshot database')
    else:
        entries = None
        selected = latest_packages(assets)
    if args.check_database:
        if not matches_database(selected, database_entries(args.check_database)):
            raise SystemExit(1)
    else:
        download_packages(selected, args.output, entries)
        print(f'Verified {len(selected)} packages; all release history retained')


if __name__ == '__main__':
    main()
