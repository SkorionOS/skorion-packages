"""Exercise the real build entrypoint without downloading or building packages."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-single-package.sh"


class MesaLLVMDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "pkgbuild-patches.conf").write_text(
            (ROOT / "pkgbuild-patches.conf").read_text()
        )
        self.work = self.root / "work"
        self.work.mkdir()
        self.output = self.root / "output"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.captured = self.root / "captured-PKGBUILD"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(self.workspace),
            "WORK_DIR": str(self.work),
            "OUTPUT_DIR": str(self.output),
            "CAPTURED_PKGBUILD": str(self.captured),
            "SRCDEST": "",
        }
        # Git cannot access the network; checkout simulates a pinned recipe.
        self.write_command("git", '''
case "$1" in
    pull) ;;
    checkout) cp PKGBUILD.pinned PKGBUILD ;;
    *) exit 1 ;;
esac
''')
        self.write_command("pikaur", '''
[[ "$*" == "--noconfirm -S -P PKGBUILD" ]] || exit 1
bash -n PKGBUILD
cp PKGBUILD "$CAPTURED_PKGBUILD"
''')

    def write_command(self, name, source):
        command = self.bin / name
        command.write_text("#!/bin/bash\nset -e\n" + source)
        command.chmod(0o755)

    def run_build(self, package, recipe, pinned=None):
        package_dir = self.work / package
        package_dir.mkdir(exist_ok=True)
        (package_dir / "PKGBUILD").write_text(recipe)
        if pinned is not None:
            (package_dir / "PKGBUILD.pinned").write_text(pinned)
            (self.workspace / "aur-pinned.conf").write_text(f"{package}=example-commit\n")
        result = subprocess.run(
            ["bash", str(SCRIPT), package, "aur"], env=self.env,
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.captured.read_text()

    def test_current_and_future_sonames_use_makepkg_detection(self):
        for package in ("mesa-git", "lib32-mesa-git"):
            for abi in ("21.1-64", "22.1-64", "23.1-64", "24-64", "24.2-32"):
                for quote in ("'", '"'):
                    with self.subTest(package=package, abi=abi, quote=quote):
                        result = self.run_build(
                            package, f"depends=({quote}libLLVM.so={abi}{quote})\n"
                        )
                        self.assertEqual(result, f"depends=({quote}libLLVM.so{quote})\n")

    def test_unversioned_and_other_dependencies_are_unchanged(self):
        recipe = "depends=('libLLVM.so' 'lib32-llvm-libs' 'mesa-git' 'libOther.so=22.1-64')\n"
        for package in ("mesa-git", "lib32-mesa-git"):
            with self.subTest(package=package):
                self.assertEqual(self.run_build(package, recipe), recipe)

    def test_other_packages_keep_their_abi_requirement(self):
        recipe = "depends=('libLLVM.so=22.1-64')\n"
        self.assertEqual(self.run_build("unrelated-git", recipe), recipe)

    def test_normalization_happens_after_pinned_checkout(self):
        result = self.run_build(
            "mesa-git", "depends=('libLLVM.so=23.1-64')\n",
            pinned="depends=('libLLVM.so=21.1-64')\n",
        )
        self.assertEqual(result, "depends=('libLLVM.so')\n")

    def test_existing_meson_patch_is_preserved(self):
        recipe = "depends=('libLLVM.so=22.1-64')\nbuild() { meson setup mesa _build; }\n"
        result = self.run_build("mesa-git", recipe)
        self.assertEqual(
            result, "depends=('libLLVM.so')\nbuild() { meson setup mesa mesa/_build; }\n"
        )

    def test_no_fixed_llvm_version_remains_in_patch_rules(self):
        rules = (ROOT / "pkgbuild-patches.conf").read_text()
        self.assertNotIn("libLLVM.so=", rules)


if __name__ == "__main__":
    unittest.main()
