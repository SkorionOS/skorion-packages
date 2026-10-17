#!/usr/bin/env python3
"""Fail closed when stable lib32 Mesa needs a source-controlled LLVM rebuild.

Read package metadata only. Never edit a recipe, choose a new pkgrel, build,
install, or publish anything. Detection uses current Arch metadata; the builder
passes its own synchronized multilib.db so mirror changes are checked again.
"""

import argparse
import io
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request


SPLITS = ("lib32-mesa", "lib32-opencl-mesa", "lib32-vulkan-radeon",
          "lib32-vulkan-swrast")
REPOSITORY_DB = "https://github.com/SkorionOS/skorion-packages/releases/download/latest/skorion.db"
PROVIDER_DB = "https://geo.mirror.pkgbuild.com/multilib/os/x86_64/multilib.db"
VERSION = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9._+]+-[0-9]+(?:\.[0-9]+)?", re.ASCII)
UPSTREAM_VERSION = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9._+]+", re.ASCII)
MAX_DATABASE_BYTES = 4 * 1024 * 1024


def read_database(source):
    if source.startswith("https://"):
        request = urllib.request.Request(source, headers={"User-Agent": "skorion-llvm-preflight"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(MAX_DATABASE_BYTES + 1)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    elif "://" in source:
        raise ValueError("Database URLs must use HTTPS")
    else:
        with Path(source).open("rb") as source_file:
            data = source_file.read(MAX_DATABASE_BYTES + 1)
    if not data or len(data) > MAX_DATABASE_BYTES:
        raise ValueError("Empty or oversized package database")
    return data


def package_records(data, names, *, require_all=True):
    records = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for index, entry in enumerate(archive):
            if index >= 10000:
                raise ValueError("Too many package database entries")
            if not entry.name.endswith("/desc"):
                continue
            if not entry.isfile() or entry.size > 65536:
                raise ValueError("Invalid package description entry")
            content = archive.extractfile(entry).read().decode("utf-8")
            fields = {}
            for section in content.strip().split("\n\n"):
                lines = section.splitlines()
                if not lines or not re.fullmatch(r"%[A-Z0-9_]+%", lines[0]):
                    raise ValueError("Malformed package database fields")
                key = lines[0][1:-1]
                if key in fields:
                    raise ValueError(f"Duplicate package database field: {key}")
                fields[key] = lines[1:]
            name = single(fields, "NAME")
            if name in names:
                if name in records:
                    raise ValueError(f"Duplicate package record: {name}")
                records[name] = fields
    missing = set(names) - records.keys()
    if missing and require_all:
        raise ValueError("Missing package metadata: " + ", ".join(sorted(missing)))
    return records


def single(record, key):
    values = record.get(key, [])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"Expected one {key} field")
    return values[0]


def full_version(value):
    if not VERSION.fullmatch(value):
        raise ValueError(f"Invalid package version: {value!r}")
    return value


def vercmp(left, right):
    result = subprocess.check_output(["vercmp", left, right], text=True).strip()
    if result not in ("-1", "0", "1"):
        raise ValueError("Unexpected result from pacman's vercmp")
    return int(result)


def check(recipe_version, repository_data, provider_data, *, assets=None, compare=vercmp):
    recipe_version = full_version(recipe_version)
    splits = package_records(repository_data, SPLITS, require_all=False)
    versions, pins = set(), set()
    issues = [f"missing {name}" for name in SPLITS if name not in splits]
    if not splits:
        raise ValueError("No stable lib32 Mesa split metadata; inspect the published repository before rebuilding")
    if assets is not None:
        # The detector compares release asset versions later. A newer asset
        # with an older DB must not turn an incomplete publication into a
        # successful same-version skip.
        for name in SPLITS:
            db_version = single(splits[name], "VERSION") if name in splits else None
            asset_version = assets.get(name)
            if asset_version != db_version or asset_version is None:
                raise ValueError(f"Published asset/database mismatch for {name}: "
                                 f"asset={asset_version}, database={db_version}; "
                                 "inspect and reconcile the incomplete publication before rebuilding")
    for name, record in splits.items():
        if single(record, "BASE") != "lib32-mesa":
            raise ValueError(f"Unexpected pkgbase for {name}")
        versions.add(full_version(single(record, "VERSION")))
        dependencies = [d for d in record.get("DEPENDS", []) if d.startswith("lib32-llvm-libs")]
        if len(dependencies) != 1 or not dependencies[0].startswith("lib32-llvm-libs="):
            issues.append(f"missing or ambiguous LLVM pin in {name}")
            continue
        pin = dependencies[0].split("=", 1)[1]
        if not UPSTREAM_VERSION.fullmatch(pin):
            issues.append(f"invalid LLVM pin in {name}")
            continue
        pins.add(pin)
    if len(versions) != 1 or len(pins) != 1:
        issues.append("inconsistent split versions or LLVM pins")
    provider = package_records(provider_data, ("lib32-llvm-libs",))["lib32-llvm-libs"]
    available = full_version(single(provider, "VERSION")).rsplit("-", 1)[0]
    orders = [compare(recipe_version, published) for published in versions]
    if any(order < 0 for order in orders):
        raise ValueError(f"Recipe {recipe_version} is older than published splits {sorted(versions)}; do not overwrite or downgrade them")
    if all(order > 0 for order in orders):
        repair = "; repair " + ", ".join(issues) if issues else ""
        return (f"lib32-mesa: source version {recipe_version} exceeds all published splits; "
                f"rebuild the complete family against lib32-llvm-libs={available}{repair}")
    if issues:
        raise ValueError("Published lib32 Mesa metadata: " + ", ".join(issues) +
                         ". Bump pkgrel beyond every published split and rebuild the complete family")
    published = versions.pop()
    pin = pins.pop()
    if pin != available:
        raise ValueError(
            f"lib32-mesa {published} pins lib32-llvm-libs={pin}, but the provider is {available}. "
            "Bump pkgrel in local/lib32-mesa/PKGBUILD and rebuild the complete split family; "
            "FORCE_REBUILD with the same version would overwrite published packages")
    return f"lib32-mesa: published {published} LLVM pin matches lib32-llvm-libs={available}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe-version", required=True)
    parser.add_argument("--repository-db", default=REPOSITORY_DB, help="HTTPS URL or local database file")
    parser.add_argument("--provider-db", default=PROVIDER_DB, help="HTTPS URL or local multilib.db")
    parser.add_argument("--asset-versions", help="Detector's local name=version snapshot of release assets")
    args = parser.parse_args()
    try:
        assets = None
        if args.asset_versions:
            assets = {}
            for line in Path(args.asset_versions).read_text().splitlines():
                name, separator, version = line.partition("=")
                if name in SPLITS:
                    if not separator or name in assets:
                        raise ValueError(f"Invalid asset version metadata for {name}")
                    assets[name] = full_version(version)
        print(check(args.recipe_version, read_database(args.repository_db), read_database(args.provider_db), assets=assets))
    except (OSError, ValueError, tarfile.TarError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"lib32-mesa LLVM preflight failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
