#!/usr/bin/env python3
"""Download release assets with bounded retries, size/digest checks and atomic saves."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.request


def fetch_json(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"}), timeout=60) as response:
                return json.load(response)
        except (OSError, ValueError, http.client.HTTPException):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def release(repository, tag):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        raise ValueError("Invalid release tag")
    result = fetch_json(f"https://api.github.com/repos/{repository}/releases/tags/{tag}")
    assets = result.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Release has no assets")
    if len({a['name'] for a in assets}) != len(assets):
        raise ValueError("Duplicate asset names")
    for asset in assets:
        if Path(asset['name']).name != asset['name'] or asset['name'] in ('', '.', '..'):
            raise ValueError("Unsafe asset name")
        if not asset['browser_download_url'].startswith(f"https://github.com/{repository}/releases/download/"):
            raise ValueError("Unexpected asset destination")
    return result


def fingerprint(document, packages_only=False):
    return sorted((a['name'], a['id'], a['size'], a.get('digest'), a['updated_at'])
                  for a in document['assets'] if not packages_only or a['name'].endswith('.pkg.tar.zst'))


def download(asset, directory):
    """Never accept an HTTP error body, short response, or stale cached asset."""
    name = asset['name']
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Unsafe asset name')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or asset['size'] <= 0:
        raise ValueError(f'Missing size/SHA256 for {name}')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        fd, temporary = tempfile.mkstemp(prefix='.' + name + '.', dir=directory)
        try:
            hasher = hashlib.sha256()
            size = 0
            with os.fdopen(fd, 'wb') as output:
                with urllib.request.urlopen(asset['browser_download_url'], timeout=180) as response:
                    if response.status != 200:
                        raise ValueError(f'HTTP {response.status}')
                    while data := response.read(1024 * 1024):
                        size += len(data)
                        if size > asset['size']:
                            raise ValueError('Response exceeds asset size')
                        hasher.update(data)
                        output.write(data)
            if size != asset['size'] or 'sha256:' + hasher.hexdigest() != digest:
                raise ValueError(f'Size or SHA256 mismatch for {name}')
            target = directory / name
            os.replace(temporary, target)
            print(f'Verified {name}: {size} bytes', flush=True)
            return target
        except (OSError, ValueError, http.client.HTTPException):
            Path(temporary).unlink(missing_ok=True)
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository')
    parser.add_argument('tag')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    document = release(args.repository, args.tag)
    assets = [a for a in document['assets'] if a['name'].endswith('.pkg.tar.zst')]
    if not assets:
        raise ValueError('No package assets')
    for asset in assets:
        download(asset, args.directory)
    if fingerprint(document, True) != fingerprint(release(args.repository, args.tag), True):
        raise ValueError('Release packages changed during download; refusing this snapshot')
    print(f'Verified {len(assets)} package assets')


if __name__ == '__main__':
    main()
