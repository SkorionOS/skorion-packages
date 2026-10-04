"""Offline packaging contracts; no downloaded application is executed.

Run: python -m unittest discover -s tests -p test_desktop_recipe_updates.py -v
Optionally set DESKTOP_SOURCE_CACHE to a directory containing the exact source
archives named by the recipes to check their SHA256 and upstream source layout.
Set VERCMP to pacman's vercmp (or install it on PATH) to check epoch-aware bounds.
These tests do not replace clean Arch builds or GNOME/fan-control runtime tests.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERCMP = os.environ.get("VERCMP") or shutil.which("vercmp")
VERSIONS = {
    "cursor-byok": "1.0.1",
    "mint-themes": "2.4.2",
    "mint-y-icons": "1.9.6",
    "nbfc-linux": "0.5.3",
    "fpaste": "0.5.0.0",
    "sx": "2.5.0",
    "gnome-shell-extension-logo-menu": "25.2_280926",
}


def recipe(package):
    return ROOT / "local" / package / "PKGBUILD"


def metadata(package, field):
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; eval \'printf "%s\\n" "${\'"$2"\'[@]}"\'',
         "bash", str(recipe(package)), field],
        check=True, text=True, capture_output=True,
    )
    return result.stdout.splitlines()


class DesktopRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src = self.base / "src"
        self.pkg = self.base / "pkg"
        self.src.mkdir()
        self.pkg.mkdir()

    def write_source(self, path, content="fixture\n", executable=False):
        path = self.src / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755 if executable else 0o644)
        return path

    def package(self, package, extra_env=None):
        env = {**os.environ, "srcdir": str(self.src), "pkgdir": str(self.pkg)}
        env.update(extra_env or {})
        subprocess.run(
            ["bash", "-ec", 'source "$1"; cd "$srcdir"; package',
             "bash", str(recipe(package))],
            env=env, check=True, capture_output=True, text=True,
        )

    def test_syntax_versions_and_fixed_source_digests(self):
        for package, version in VERSIONS.items():
            with self.subTest(package=package):
                subprocess.run(["bash", "-n", str(recipe(package))], check=True)
                self.assertEqual(metadata(package, "pkgver"), [version])
                self.assertEqual(metadata(package, "pkgrel"), ["1"])
                sources = metadata(package, "source")
                hashes = metadata(package, "sha256sums")
                self.assertEqual(len(sources), len(hashes))
                for digest in hashes:
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")
                for source in sources:
                    if "://" in source:
                        self.assertTrue(source.split("::")[-1].startswith("https://"))
                        self.assertIn(version, source)
                    else:
                        self.assertEqual(hashlib.sha256((recipe(package).parent / source).read_bytes()).hexdigest(),
                                         hashes[sources.index(source)])

    def test_cursor_layout_desktop_and_license(self):
        self.write_source("cursor-byok-desktop", "never execute this fixture", True)
        self.write_source("cursor-byok-1.0.1/LICENSE", "MIT License\n")
        for size in (32, 128, 256, 512):
            self.write_source(f"cursor-byok-1.0.1/apps/desktop/src-tauri/icons/linux-{size}x{size}.png")
        shutil.copy(ROOT / "local/cursor-byok/cursor-byok.desktop", self.src)
        self.package("cursor-byok")
        binary = self.pkg / "usr/bin/cursor-byok"
        self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o755)
        self.assertFalse((self.pkg / "usr/bin/Cursor助手").exists())
        desktop = self.pkg / "usr/share/applications/cursor-byok.desktop"
        text = desktop.read_text()
        for expected in ("Exec=cursor-byok", "Icon=cursor-byok", "StartupWMClass=cursor-byok-desktop"):
            self.assertIn(expected, text.splitlines())
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(desktop)], check=True)
        for size in (32, 128, 256, 512):
            self.assertTrue((self.pkg / f"usr/share/icons/hicolor/{size}x{size}/apps/cursor-byok.png").is_file())
        self.assertTrue((self.pkg / "usr/share/licenses/cursor-byok/LICENSE").is_file())
        self.assertTrue({"webkit2gtk-4.1", "gtk3", "libsoup3", "openssl", "wayland",
                         "libayatana-appindicator"} <= set(metadata("cursor-byok", "depends")))
        self.assertEqual(metadata("cursor-byok", "license"), ["MIT"])

    def test_mint_layout_and_symlinks(self):
        for package in ("mint-themes", "mint-y-icons"):
            version = VERSIONS[package]
            top = f"{package}-{version}"
            self.write_source(f"{top}/usr/share/{package}/original")
            (self.src / top / "usr/share" / package / "alias").symlink_to("original")
            self.write_source(f"{top}/debian/copyright", "upstream attribution")
            self.package(package)
            self.assertTrue((self.pkg / "usr/share" / package / "alias").is_symlink())
            self.assertFalse((self.pkg / "usr/usr").exists())
        self.assertIn("CC-BY-SA-4.0", metadata("mint-y-icons", "license"))
        self.assertTrue((self.pkg / "usr/share/licenses/mint-y-icons/copyright").exists())

    def test_fpaste_codeberg_layout(self):
        self.write_source("fpaste/fpaste", "never execute fixture", True)
        self.write_source("fpaste/docs/man/en/fpaste.1")
        self.write_source("fpaste/completions/bash/fpaste.bash")
        self.package("fpaste")
        for path in ("usr/bin/fpaste", "usr/share/man/man1/fpaste.1",
                     "usr/share/bash-completion/completions/fpaste"):
            self.assertTrue((self.pkg / path).is_file())
        self.assertEqual(metadata("fpaste", "url"), ["https://codeberg.org/sanjay_ankur/fpaste"])

    def test_sx_remains_source_build_with_version_and_license(self):
        self.write_source("sx-2.5.0/sx", "never execute fixture", True)
        self.write_source("sx-2.5.0/LICENSE", "MIT License")
        self.package("sx")
        self.assertTrue((self.pkg / "usr/bin/sx").is_file())
        self.assertTrue((self.pkg / "usr/share/licenses/sx/LICENSE").is_file())
        text = recipe("sx").read_text()
        self.assertIn("go build", text)
        self.assertIn("go test", text)
        self.assertIn("-mod=readonly", text)
        self.assertIn("-X main.version=${pkgver}", text)
        self.assertIn("GOTOOLCHAIN=local", text)
        self.assertIn("sx-search-bin", metadata("sx", "conflicts"))
        self.assertNotIn("releases/download", " ".join(metadata("sx", "source")))

    def test_nbfc_adds_build_dependencies_without_touching_host(self):
        depends = set(metadata("nbfc-linux", "depends"))
        self.assertTrue({"curl", "openssl", "acpica", "acpi_call", "lua54", "libxml2"} <= depends)
        top = self.src / "nbfc-linux-0.5.3"
        top.mkdir()
        fake_bin = self.base / "bin"
        fake_bin.mkdir()
        capture = self.base / "make-args"
        make = fake_bin / "make"
        make.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$MAKE_CAPTURE"\n')
        make.chmod(0o755)
        self.package("nbfc-linux", {"PATH": str(fake_bin) + ":" + os.environ["PATH"],
                                    "MAKE_CAPTURE": str(capture)})
        self.assertEqual(capture.read_text().splitlines(), [f"DESTDIR={self.pkg}", "install"])
        text = recipe("nbfc-linux").read_text()
        self.assertIn("--runstatedir=/run", text)
        self.assertIn("--sysconfdir=/etc", text)
        self.assertNotRegex(text, r"systemctl\s+(enable|start)|modprobe|nbfc\s+(start|config)")
        self.assertFalse((recipe("nbfc-linux").parent / "nbfc-linux.install").exists())

    def test_logo_schema_locale_ownership_and_gnome_bounds(self):
        top = self.src / "Logomenu-25.2_280926"
        top.mkdir()
        self.write_source("Logomenu-25.2_280926/README.md")
        with zipfile.ZipFile(top / "logomenu@aryan_k.shell-extension.zip", "w") as archive:
            archive.writestr("extension.js", "fixture")
            archive.writestr("metadata.json", json.dumps({"uuid": "logomenu@aryan_k", "shell-version": ["49", "50", "51"]}))
            archive.writestr("schemas/org.gnome.shell.extensions.logo-menu.gschema.xml", "fixture")
            archive.writestr("schemas/gschemas.compiled", "must not package")
            archive.writestr("locale/en/LC_MESSAGES/logo-menu.mo", "fixture")
        self.package("gnome-shell-extension-logo-menu")
        self.assertTrue((self.pkg / "usr/share/glib-2.0/schemas/org.gnome.shell.extensions.logo-menu.gschema.xml").is_file())
        self.assertFalse((self.pkg / "usr/share/glib-2.0/schemas/gschemas.compiled").exists())
        self.assertTrue((self.pkg / "usr/share/locale/en/LC_MESSAGES/logo-menu.mo").is_file())
        self.assertEqual(metadata("gnome-shell-extension-logo-menu", "depends"), ["gnome-shell>=1:49", "gnome-shell<1:52"])
        self.assertNotIn("gnome-extensions install", recipe("gnome-shell-extension-logo-menu").read_text())

    @unittest.skipUnless(VERCMP, "set VERCMP or install pacman's vercmp for epoch-aware bounds")
    def test_logo_gnome_bounds_use_arch_epoch(self):
        dependencies = metadata("gnome-shell-extension-logo-menu", "depends")
        lower = next(dep.removeprefix("gnome-shell>=") for dep in dependencies
                     if dep.startswith("gnome-shell>="))
        upper = next(dep.removeprefix("gnome-shell<") for dep in dependencies
                     if dep.startswith("gnome-shell<"))

        def compare(version, bound):
            return int(subprocess.check_output([VERCMP, version, bound], text=True).strip())

        for version, supported in (
            ("1:48.10-1", False),
            ("1:49-1", True),
            ("1:49.5-2", True),
            ("1:50.5-1", True),
            ("1:51.0-1", True),
            ("1:51.99-9", True),
            ("1:52-1", False),
            ("1:52.0-1", False),
        ):
            with self.subTest(version=version):
                self.assertEqual(compare(version, lower) >= 0 and compare(version, upper) < 0,
                                 supported)


@unittest.skipUnless(os.environ.get("DESKTOP_SOURCE_CACHE"), "set DESKTOP_SOURCE_CACHE for downloaded-source validation")
class DownloadedSourceTests(unittest.TestCase):
    def test_source_checksums_and_expected_members(self):
        cache = Path(os.environ["DESKTOP_SOURCE_CACHE"])
        members_by_package = {}
        for package in VERSIONS:
            for source, expected in zip(metadata(package, "source"), metadata(package, "sha256sums")):
                if "://" not in source:
                    continue
                filename = source.split("::")[0]
                path = cache / filename
                self.assertTrue(path.is_file(), f"missing {path}")
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, str(path))
                with tarfile.open(path) as archive:
                    members_by_package.setdefault(package, set()).update(archive.getnames())
                    if package == "gnome-shell-extension-logo-menu":
                        member = archive.extractfile("Logomenu-25.2_280926/metadata.json")
                        metadata_json = json.load(member)
                        self.assertEqual(metadata_json["shell-version"], ["49", "50", "51"])
                        self.assertEqual(metadata_json["version-name"], "25.2")
                    if package == "fpaste":
                        source_text = archive.extractfile("fpaste/fpaste").read().decode()
                        ast.parse(source_text)
                        self.assertIn("VERSION = '0.5.0.0'", source_text)
                    if package == "sx":
                        self.assertIn("go 1.25.0", archive.extractfile("sx-2.5.0/go.mod").read().decode())
                    if package == "nbfc-linux":
                        makefile = archive.extractfile("nbfc-linux-0.5.3/Makefile.in").read().decode()
                        self.assertIn("pkg-config --cflags lua5.4", makefile)
                        self.assertIn("pkg-config --libs libxml-2.0", makefile)
                    if package == "mint-themes":
                        # The official Git archive includes the pre-rendered
                        # assets too; switching from the unavailable Mint pool
                        # archive must not add an undeclared Inkscape dependency.
                        names = set(archive.getnames())
                        checked = 0
                        for member in archive.getmembers():
                            if not re.search(r"/render[^/]*assets\.sh$", member.name):
                                continue
                            script = archive.extractfile(member).read().decode()
                            assets = re.search(r'ASSETS_DIR="([^"]+)"', script)
                            index = re.search(r'INDEX="([^"]+)"', script)
                            if not assets or not index:
                                continue
                            parent = Path(member.name).parent
                            items = archive.extractfile(str(parent / index.group(1))).read().decode().split()
                            for item in items:
                                for suffix in [".png"] + (["@2.png"] if "@2.png" in script else []):
                                    self.assertIn(str(parent / assets.group(1) / (item + suffix)), names)
                                    checked += 1
                        self.assertGreater(checked, 0)
        for package, required in {
            "cursor-byok": ["cursor-byok-desktop", "cursor-byok-1.0.1/LICENSE",
                            "cursor-byok-1.0.1/apps/desktop/src-tauri/icons/linux-128x128.png"],
            "mint-themes": ["mint-themes-2.4.2/Makefile", "mint-themes-2.4.2/generate-themes.py"],
            "mint-y-icons": ["mint-y-icons-1.9.6/usr/share/icons/Mint-Y/index.theme"],
        }.items():
            self.assertTrue(set(required) <= members_by_package[package], package)


if __name__ == "__main__":
    unittest.main()
