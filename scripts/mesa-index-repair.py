"""Create new skorion indexes, replacing exactly the lib32 Mesa Git entry."""
import copy
import gzip
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile

OLD = "27.0.0_devel.234174.0e9e1086bb8.d41d8cd-1"
NEW = "27.0.0_devel.234237.6f23942285e.d41d8cd-1"
NAME = "lib32-mesa-git"
OLD_SHA = "d19bdd9f6c510f2442d812328fbcceeac6f27a5f0759fb70824ceeaf5e0e4d16"
NEW_SHA = "aadb98b118132982d1b8fece98e3020950f3f64410f12b2d5e3ea278aca08b2f"
INDEXES = {
    "skorion.db": "1d8c793e9bb5f5ee0460999d78a30b1333dce4ce5b3478f8f0ec52027cd29004",
    "skorion.files": "e6f450dc6726d39fc2e8caa61f2e2e99e3894722270a93ac9f2f76c26a70fc28",
}
FIELDS = {
    "FILENAME": "filename", "NAME": "pkgname", "BASE": "pkgbase", "VERSION": "pkgver",
    "DESC": "pkgdesc", "CSIZE": "csize", "ISIZE": "size", "SHA256SUM": "sha256",
    "URL": "url", "LICENSE": "license", "ARCH": "arch", "BUILDDATE": "builddate",
    "PACKAGER": "packager", "CONFLICTS": "conflict", "PROVIDES": "provides",
    "DEPENDS": "depend", "OPTDEPENDS": "optdepend", "MAKEDEPENDS": "makedepend",
}

def sha(file):
    return hashlib.file_digest(file.open("rb"), "sha256").hexdigest()

def package_path(directory, version):
    return directory / f"{NAME}-{version}-x86_64.pkg.tar.zst"

def parse_package(path, version, expected_sha):
    if sha(path) != expected_sha:
        raise ValueError(f"wrong package digest: {path.name}")
    payload, metadata = [], {}
    with subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as pkg:
            for item in pkg:
                if item.name == ".PKGINFO":
                    for line in pkg.extractfile(item).read().decode().splitlines():
                        if " = " in line and not line.startswith("#"):
                            key, value = line.split(" = ", 1)
                            metadata.setdefault(key, []).append(value)
                if item.name.startswith("."):
                    continue
                if not item.name.startswith("usr") or item.name.split("/")[0] != "usr" or ".." in item.name.split("/"):
                    raise ValueError(f"unexpected package path: {item.name}")
                payload.append(item.name + ("/" if item.isdir() else ""))
        # Consume possible archive padding before waiting on the decompressor.
        while proc.stdout.read(65536):
            pass
        if proc.wait() != 0:
            raise ValueError("zstd failed")
    if metadata.get("pkgname") != [NAME] or metadata.get("pkgver") != [version] or metadata.get("arch") != ["x86_64"]:
        raise ValueError("unexpected package identity")
    metadata.update(filename=[path.name], csize=[str(path.stat().st_size)], sha256=[expected_sha])
    if set(metadata) != set(FIELDS.values()) | {"xdata"} or len(set(payload)) != len(payload):
        raise ValueError("unexpected package metadata or duplicate payload names")
    desc = "".join("%" + field + "%\n" + "\n".join(metadata[key]) + "\n\n" for field, key in FIELDS.items())
    files = "%FILES%\n" + "\n".join(payload) + "\n"
    return {"desc": desc.encode(), "files": files.encode()}

def read_index(path):
    ans = {}
    with tarfile.open(path, "r:gz") as archive:
        for entry in archive:
            if entry.name in ans or not (entry.isdir() or entry.isfile()):
                raise ValueError(f"unexpected index member: {entry.name}")
            data = archive.extractfile(entry).read() if entry.isfile() else None
            ans[entry.name] = (entry, data)
    return ans

def repair(original, output):
    output.mkdir(parents=True, exist_ok=True)
    old_pkg = parse_package(package_path(original, OLD), OLD, OLD_SHA)
    new_pkg = parse_package(package_path(original, NEW), NEW, NEW_SHA)
    old_dir = f"{NAME}-{OLD}"
    new_dir = f"{NAME}-{NEW}"
    matching_64 = f"mesa-git-{OLD}/desc"
    for basename, digest in INDEXES.items():
        path = original / basename
        if sha(path) != digest or sha(original / (basename + ".tar.gz")) != digest:
            raise ValueError(f"unexpected starting hash: {basename}")
        source = read_index(path)
        if matching_64 not in source or b"\n" + OLD.encode() + b"\n" not in source[matching_64][1]:
            raise ValueError("expected 64-bit package absent")
        keys = {x for x in source if x.split("/")[0].startswith(f"{NAME}-")}
        subfiles = {"desc"} if basename.endswith(".db") else {"desc", "files"}
        if keys != {new_dir} | {new_dir + "/" + s for s in subfiles}:
            raise ValueError(f"unexpected 32-bit entries: {keys}")
        actual = {x: source[new_dir + "/" + x][1] for x in subfiles}
        if actual != {x: new_pkg[x] for x in subfiles}:
            for x in sorted(subfiles):
                if actual[x] != new_pkg[x]:
                    print(f"regeneration differs for {x}: old={len(actual[x])} new={len(new_pkg[x])}", file=sys.stderr)
            raise ValueError("regenerated entries do not match current package")
        result = output / basename
        with result.open("wb") as f, gzip.GzipFile(filename="", fileobj=f, mode="wb", compresslevel=9, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as dest:
            for k, (info, data) in source.items():
                if k in keys:
                    info = copy.copy(info)
                    info.pax_headers = {}  # only modified members are rebuilt
                    info.name = old_dir + k[len(new_dir):]
                    if not info.isdir():
                        data = old_pkg[k.split("/")[-1]]
                        info.size = len(data)
                dest.addfile(info, io.BytesIO(data) if data is not None else None)
        repaired = read_index(result)
        if set(repaired) != {old_dir + k[len(new_dir):] if k in keys else k for k in source}:
            raise ValueError("unexpected output member set")
        if any(repaired[k][1] != src[1] or any(v != repaired[k][0].get_info()[kk] for kk, v in src[0].get_info().items() if kk != "chksum")
               for k, src in source.items() if k not in keys):
            raise ValueError("unrelated entry was modified")
        if any(repaired[old_dir + "/" + x][1] != old_pkg[x] for x in subfiles):
            raise ValueError("unexpected rebuilt members")
        (output / (basename + ".tar.gz")).write_bytes(result.read_bytes())
        print(f"{basename} {sha(result)}: preserved {len(source) - len(keys)} unrelated members")

if __name__ == "__main__":
    repair(Path(sys.argv[1]), Path(sys.argv[2]))
