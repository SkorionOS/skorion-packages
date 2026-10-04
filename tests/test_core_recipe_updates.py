"""Offline core-recipe checks; no package installation, network or boot actions.

Set CORE_RECIPE_UPSTREAM_DIR to a directory containing the verified upstream
bootc-1.16.14.tar.gz, bootc-1.16.14/, bootc/, coreos-bootupd/, hhd/, and xonedo-sk/
to additionally check actual source provenance and staged installation layouts.
Set VERCMP to a pacman-compatible comparator to check upgrade ordering.
Staging tests use placeholder build outputs, not compiled or tested binaries.
"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = os.environ.get("CORE_RECIPE_UPSTREAM_DIR")
VERCMP = os.environ.get("VERCMP") or shutil.which("vercmp")
PACKAGES = ("bootupd", "bootc", "bootc-git", "hhd-git", "xonedo-sk-dkms")


class RecipeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}

    def run_recipe(self, package, commands, *, env=None, check=True):
        result = subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; ' + commands,
             "recipe-test", str(ROOT / "local" / package / "PKGBUILD")],
            cwd=self.work, env={**self.env, **(env or {})},
            text=True, capture_output=True, timeout=30,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def values(self, package, name, setup=""):
        result = self.run_recipe(package, setup + f'printf "%s\\n" "${{{name}[@]}}"')
        return result.stdout.splitlines()

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -eu\n" + body)
        path.chmod(0o755)

class CoreRecipeTests(RecipeTestCase):
    def test_shell_syntax(self):
        for package in PACKAGES:
            with self.subTest(package=package):
                subprocess.run(
                    ["bash", "-n", str(ROOT / "local" / package / "PKGBUILD")],
                    check=True, capture_output=True,
                )

    def test_bootupd_retains_fork_feature_and_declares_openssl(self):
        self.assertEqual(self.values("bootupd", "source"), [
            "git+https://github.com/p5/coreos-bootupd#branch=sdboot-support"
        ])
        self.assertIn("openssl", self.values("bootupd", "depends"))
        self.assertIn("pkgconf", self.values("bootupd", "makedepends"))
        self.assertEqual(self.values("bootupd", "epoch"), ["1"])
        self.assertEqual(self.values("bootupd", "provides"), [
            "bootupd=1:" + self.values("bootupd", "pkgver")[0]
        ])
        (self.work / "coreos-bootupd").mkdir()
        self.command("cargo", 'printf "%s\\n" "$*"')
        self.assertEqual(self.run_recipe("bootupd", "build").stdout.strip(),
                         "build --release --frozen --features systemd-boot")

    def test_bootupd_version_uses_only_the_source_checkout(self):
        source = self.work / "coreos-bootupd"
        source.mkdir()
        (source / "Cargo.toml").write_text(
            '[package]\nname = "bootupd"\nversion = "0.2.29"\n'
            '[dependencies.example]\nversion = "99.0"\n'
        )
        # Any network command (clone/fetch/ls-remote) or different cwd fails.
        self.command("git", '''
[[ "$PWD" == "$EXPECTED_SOURCE" ]]
case "$*" in
  'rev-list --count HEAD') printf '%s\\n' "$REVISION" ;;
  'rev-parse --short=7 HEAD') printf '%s\\n' "$COMMIT" ;;
  *) exit 97 ;;
esac
''')
        for revision, commit in ((1371, "7db29c7"), (1372, "0123456")):
            result = self.run_recipe("bootupd", "pkgver", env={
                "EXPECTED_SOURCE": str(source), "REVISION": str(revision),
                "COMMIT": commit,
            })
            self.assertEqual(result.stdout.strip(),
                             f"0.2.29.sdboot.r{revision}.g{commit}")

    def test_bootupd_rejects_missing_package_version(self):
        source = self.work / "coreos-bootupd"
        source.mkdir()
        (source / "Cargo.toml").write_text(
            '[package]\nname = "bootupd"\n[dependencies.other]\nversion = "9"\n'
        )
        self.assertNotEqual(self.run_recipe("bootupd", "pkgver", check=False).returncode, 0)

    def test_bootc_stable_source_is_checksummed(self):
        self.assertEqual(self.values("bootc", "pkgver"), ["1.16.14"])
        self.assertEqual(self.values("bootc", "source"), [
            "https://github.com/bootc-dev/bootc/archive/refs/tags/v1.16.14.tar.gz"
        ])
        self.assertEqual(self.values("bootc", "sha256sums"), [
            "be63615959d74d23d098c79027e1759734b9e77bb1bc2e0ad27a2deccef5fda0"
        ])

    def test_bootc_stable_runtime_and_build_dependencies(self):
        self.assertTrue({"libselinux", "openssl", "ostree", "podman", "skopeo",
                         "systemd", "util-linux", "zstd"}.issubset(
                             self.values("bootc", "depends")))
        self.assertTrue({"cargo", "clang", "git", "go-md2man", "make", "pkgconf"}
                        .issubset(self.values("bootc", "makedepends")))
        # The existing VCS repair must remain effective as well.
        self.assertIn("libselinux", self.values("bootc-git", "depends"))
        self.assertIn("clang", self.values("bootc-git", "makedepends"))

    def test_bootc_build_uses_offline_upstream_completion_target(self):
        (self.work / "bootc-1.16.14").mkdir()
        self.command("make", '''
[[ "$CARGO_NET_OFFLINE" == true ]]
[[ "$CARGO_TARGET_DIR" == target ]]
[[ "$RUSTUP_TOOLCHAIN" == stable ]]
printf '%s\\n' "$*"
''')
        self.assertEqual(self.run_recipe("bootc", "build").stdout.strip(), "completion")

    def test_bootc_git_keeps_existing_source_and_build_behavior(self):
        self.assertEqual(self.values("bootc-git", "source"), [
            "git+https://www.github.com/bootc-dev/bootc.git"
        ])
        self.assertEqual(self.values("bootc-git", "pkgrel"), ["3"])
        (self.work / "bootc").mkdir()
        self.command("make", 'printf "%s\\n" "$*"')
        self.assertEqual(self.run_recipe("bootc-git", "build").stdout.strip(),
                         "bin manpages")

    def test_hhd_runtime_dependencies_and_split_licenses(self):
        # Stub the installer only; the real split function declares metadata.
        (self.work / "hhd").mkdir()
        deps = self.values("hhd-git", "depends", setup=(
            f'srcdir="{self.work}"; pkgdir="{self.work}/pkg"; '
            "python() { :; }; package_hhd-git; "
        ))
        self.assertTrue({"python-pyserial", "python-pyroute2", "python-gobject",
                         "python-dbus", "lsof"}.issubset(deps))
        self.assertEqual(self.values("hhd-git", "license"), ["LGPL-2.1-or-later"])
        self.assertEqual(self.values("hhd-git", "pkgrel"), ["2"])
        self.assertEqual(self.values("hhd-git", "pkgname"), [
            "hhd-git", "hhd-systemd-git", "hhd-license-git", "hhd-doc-git"
        ])
        self.assertEqual(self.values("hhd-git", "source"), [
            "hhd::git+https://github.com/hhd-dev/hhd.git"
        ])

    def test_xonedo_retains_fork_and_requires_matching_firmware(self):
        self.assertEqual(self.values("xonedo-sk-dkms", "source"), [
            "git+https://github.com/honjow/xonedo.git"
        ])
        self.assertIn("xone-dongle-firmware>=2.0.0",
                      self.values("xonedo-sk-dkms", "depends"))
        self.assertIn("xonedo-dkms", self.values("xonedo-sk-dkms", "conflicts"))
        self.assertIn("xone-dkms", self.values("xonedo-sk-dkms", "provides"))
        self.assertEqual(self.values("xonedo-sk-dkms", "pkgrel"), ["2"])

    @unittest.skipUnless(VERCMP, "set VERCMP or install pacman's vercmp for ordering checks")
    def test_all_repairs_upgrade_published_versions(self):
        published = {
            "bootupd": "0.3.2.r13.gc5de0ad-1",
            "bootc": "1.16.9-1",
            "bootc-git": "1.16.14.r70.g66d4e4d-1",
            "hhd-git": "4.1.12+121.r2775.20260930.a87fb308-1",
            "xonedo-sk-dkms": "0.5.8.r1.gf97d11e-1",
        }
        for package, old in published.items():
            with self.subTest(package=package):
                new = self.run_recipe(package,
                    'printf "%s:%s-%s" "${epoch:-0}" "$pkgver" "$pkgrel"').stdout
                result = subprocess.check_output([VERCMP, new, old], text=True).strip()
                self.assertEqual(result, "1", f"{new} must upgrade {old}")
        # A newer fork commit wins independently of the hash's lexical order.
        self.assertEqual(subprocess.check_output([
            VERCMP, "1:0.2.29.sdboot.r1372.g0000000-1",
            "1:0.2.29.sdboot.r1371.gfffffff-1",
        ], text=True).strip(), "1")


@unittest.skipUnless(UPSTREAM, "set CORE_RECIPE_UPSTREAM_DIR for real-source checks")
class UpstreamSourceTests(RecipeTestCase):
    """Opt-in provenance and install-layout checks on already-downloaded sources."""

    def copy_source(self, name, destination=None):
        return Path(shutil.copytree(
            Path(UPSTREAM) / name, self.work / (destination or name),
            symlinks=True, ignore=shutil.ignore_patterns(".git"),
        ))

    def test_bootc_archive_checksum_and_source_version(self):
        source = Path(UPSTREAM)
        digest = hashlib.sha256((source / "bootc-1.16.14.tar.gz").read_bytes()).hexdigest()
        self.assertEqual(digest, self.values("bootc", "sha256sums")[0])
        manifest = tomllib.loads((source / "bootc-1.16.14/crates/lib/Cargo.toml").read_text())
        self.assertEqual(manifest["package"]["version"], "1.16.14")

    def test_vcs_versions_match_actual_full_checkouts(self):
        for package in ("bootupd", "hhd-git", "bootc-git"):
            with self.subTest(package=package):
                # Real Git reads only; no changes or fetches to these checkouts.
                actual = self.run_recipe(package, 'cd "$srcdir"; pkgver',
                                         env={"srcdir": UPSTREAM}).stdout.strip()
                self.assertEqual(actual, self.values(package, "pkgver")[0])

    def test_hhd_metadata_covers_current_source_requirements(self):
        source = Path(UPSTREAM) / "hhd"
        manifest = tomllib.loads((source / "pyproject.toml").read_text())["project"]
        self.assertEqual(self.values("hhd-git", "license"), [manifest["license"]["text"]])
        (self.work / "hhd").mkdir()
        deps = self.values("hhd-git", "depends", setup=(
            f'srcdir="{self.work}"; pkgdir="{self.work}/pkg"; '
            "python() { :; }; package_hhd-git; "
        ))
        arch_names = {
            "evdev": "python-evdev", "PyYAML": "python-yaml",
            "rich": "python-rich", "python-xlib": "python-xlib",
            "pyserial": "python-pyserial", "pyroute2": "python-pyroute2",
            "PyGObject": "python-gobject", "dbus-python": "python-dbus",
        }
        for requirement in manifest["dependencies"]:
            self.assertIn(arch_names[requirement.split(">=")[0]], deps)

    def test_bootupd_staged_layout_with_placeholder_binary(self):
        source = self.copy_source("coreos-bootupd")
        output = source / "target/release/bootupd"
        output.parent.mkdir(parents=True)
        output.write_text("NOT A BUILT BINARY\n")
        self.command("cargo", "exit 97\n")
        pkg = self.work / "pkg"
        self.run_recipe("bootupd", "package", env={"pkgdir": str(pkg)})
        binary = pkg / "usr/libexec/bootupd"
        self.assertEqual(binary.read_bytes(), output.read_bytes())
        self.assertEqual(binary.stat().st_ino, (pkg / "usr/bin/bootupctl").stat().st_ino)
        self.assertTrue((pkg / "usr/lib/systemd/system/bootloader-update.service").is_file())
        for cfg in (source / "src/grub2").rglob("*.cfg"):
            self.assertEqual(
                (pkg / "usr/lib/bootupd/grub2-static" / cfg.relative_to(source / "src/grub2"))
                .read_bytes(), cfg.read_bytes(),
            )

    def test_bootc_staged_layout_with_placeholder_build_outputs(self):
        source = self.copy_source("bootc-1.16.14")
        for name in ("bootc", "system-reinstall-bootc", "bootc-initramfs-setup"):
            output = source / "target/release" / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("NOT A BUILT BINARY\n")
        for man in (source / "docs/src").rglob("*.[578].md"):
            output = source / "target/man" / man.stem
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("placeholder man page\n")
        for shell in ("bash", "elvish", "fish", "powershell", "zsh"):
            output = source / f"target/completion/bootc.{shell}"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("placeholder completion\n")
        self.command("cargo", "exit 97\n")
        pkg = self.work / "pkg"
        self.run_recipe("bootc", "package", env={
            "pkgdir": str(pkg), "MAKEFLAGS": "SHELL=/bin/bash",
        })
        expected = [
            "usr/bin/bootc", "usr/bin/system-reinstall-bootc",
            "usr/lib/bootc/initramfs-setup",
            "usr/lib/systemd/system-generators/bootc-systemd-generator",
            "usr/lib/dracut/modules.d/51bootc/module-setup.sh",
            "usr/share/man/man8/bootc.8", "usr/share/licenses/bootc/LICENSE-MIT",
            "usr/share/licenses/bootc/LICENSE-APACHE",
            "usr/share/bash-completion/completions/bootc",
            "usr/share/fish/vendor_completions.d/bootc.fish",
            "usr/share/zsh/site-functions/_bootc",
        ]
        for relative in expected:
            self.assertTrue((pkg / relative).is_file(), relative)
        for unit in (source / "systemd").iterdir():
            if unit.suffix in (".service", ".timer", ".path", ".target"):
                self.assertTrue((pkg / "usr/lib/systemd/system" / unit.name).is_file())
        self.assertTrue((pkg / "usr/lib/bootc/storage").is_symlink())
        for hook in ("ostree-container", "ostree-ima-sign", "ostree-provisional-repair"):
            installed = pkg / "usr/lib/libostree/ext" / hook
            self.assertTrue(installed.is_symlink(), hook)
            self.assertEqual(installed.resolve(strict=True), pkg / "usr/bin/bootc")
        self.assertFalse((pkg / "usr/libexec/libostree").exists())

    def test_bootc_git_arch_hooks_with_placeholder_build_outputs(self):
        # Use the actual VCS Makefile, not the stable release's installation rules.
        source = self.copy_source("bootc")
        for name in ("bootc", "system-reinstall-bootc", "bootc-initramfs-setup"):
            output = source / "target/release" / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("NOT A BUILT BINARY\n")
        for man in (source / "docs/src").rglob("*.[578].md"):
            output = source / "target/man" / man.stem
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("placeholder man page\n")
        # The unchanged VCS recipe invokes make install's build prerequisites.
        # Stub Cargo and bootc completion generation: this checks layout only.
        self.command("cargo", ":\n")
        fake_bootc = source / "target/release/bootc"
        fake_bootc.write_text(
            '#!/bin/bash\nset -eu\n[[ "$1" == completion ]]\n'
            'printf "placeholder %s completion\\n" "$2"\n'
        )
        fake_bootc.chmod(0o755)
        pkg = self.work / "pkg"
        unrelated = pkg / "usr/libexec/libostree/unrelated-helper"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("preserve unrelated upstream libexec content\n")
        self.run_recipe("bootc-git", "package", env={
            "pkgdir": str(pkg), "MAKEFLAGS": "SHELL=/bin/bash", "CARGO_FEATURES": "",
        })
        self.assertEqual((pkg / "usr/bin/bootc").read_bytes(), fake_bootc.read_bytes())
        for hook in ("ostree-container", "ostree-ima-sign", "ostree-provisional-repair"):
            installed = pkg / "usr/lib/libostree/ext" / hook
            self.assertTrue(installed.is_symlink(), hook)
            self.assertEqual(installed.resolve(strict=True), pkg / "usr/bin/bootc")
        self.assertFalse((pkg / "usr/libexec/libostree/ext").exists())
        self.assertEqual(unrelated.read_text(), "preserve unrelated upstream libexec content\n")

    def test_xonedo_staged_dkms_identity_and_wired_removal(self):
        original = Path(UPSTREAM) / "xonedo-sk"
        source = self.copy_source("xonedo-sk", "xonedo")
        pkg = self.work / "pkg"
        self.run_recipe("xonedo-sk-dkms", "package", env={
            "srcdir": str(self.work), "pkgdir": str(pkg),
        })
        version = self.values("xonedo-sk-dkms", "pkgver")[0]
        staged = pkg / f"usr/src/xonedo-{version}"
        dkms = (staged / "dkms.conf").read_text()
        self.assertIn('PACKAGE_NAME="xonedo"', dkms)
        self.assertIn(f'PACKAGE_VERSION="{version}"', dkms)
        self.assertIn('BUILT_MODULE_NAME[1]="xone_dongle"', dkms)
        self.assertNotIn("#VERSION#", dkms)
        self.assertNotIn("xone_wired", (staged / "Kbuild").read_text())
        self.assertFalse((staged / "transport/wired.c").exists())
        self.assertFalse((staged / ".git").exists())
        self.assertEqual((pkg / "usr/lib/modprobe.d/xonedo-blacklist.conf").read_bytes(),
                         (original / "install/modprobe.conf").read_bytes())
        self.assertEqual((source / "dkms.conf").read_text().splitlines()[0],
                         'PACKAGE_NAME="xone"')


if __name__ == "__main__":
    unittest.main()
