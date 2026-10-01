#!/usr/bin/env python3
"""Select changed local recipes and newly listed AUR packages from two commits.

PR validation compares the base commit to the checked-out merge commit. It never
contacts releases or compares package versions: an unchanged pkgver still builds.
"""

import argparse
import json
import re
import subprocess
import sys


PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9@._+\-]*", re.ASCII)
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)


def validate_package_name(name):
    # Names are later passed through the existing build-in-container.sh shell.
    # Reject quotes, whitespace, path components and leading option characters.
    if not PACKAGE_NAME.fullmatch(name):
        raise ValueError(f"Unsafe package name: {name!r}")
    return name


def git(*args):
    return subprocess.check_output(["git", *args])


def resolve_commit(ref):
    if ref != "HEAD" and not COMMIT.fullmatch(ref):
        raise ValueError("Commit must be HEAD or a full hexadecimal commit ID")
    return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def read_file(commit, path):
    entry = git("ls-tree", commit, "--", path)
    if not entry:
        return None
    if entry.split(b" ", 1)[0] not in (b"100644", b"100755"):
        raise ValueError(f"Expected a regular file: {path}")
    return git("show", f"{commit}:{path}").decode()


def aur_packages(commit):
    packages = set()
    for line in (read_file(commit, "aur.conf") or "").splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            packages.add(validate_package_name(name))
    return packages


def select_packages(base, head):
    base = resolve_commit(base)
    head = resolve_commit(head)
    local = set()
    # Disable rename detection so a moved package includes its new directory and
    # deleted patches still cause their surviving local recipe to be rebuilt.
    paths = git("diff", "--name-only", "--no-renames", "-z", base, head, "--", "local/")
    for raw_path in paths.split(b"\0"):
        if not raw_path:
            continue
        parts = raw_path.decode().split("/", 2)
        if len(parts) == 3:
            name = validate_package_name(parts[1])
            if read_file(head, f"local/{name}/PKGBUILD") is not None:
                local.add(name)
    aur = aur_packages(head) - aur_packages(base)
    return ([{"package": name, "type": "aur"} for name in sorted(aur)] +
            [{"package": name, "type": "local"} for name in sorted(local)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="PR base commit ID")
    parser.add_argument("--head", default="HEAD", help="Checked-out merge commit (default: HEAD)")
    args = parser.parse_args()
    try:
        print(json.dumps(select_packages(args.base, args.head), separators=(",", ":")))
    except (ValueError, subprocess.CalledProcessError, UnicodeError) as exc:
        print(f"Package selection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
