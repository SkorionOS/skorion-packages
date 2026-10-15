"""Exercise MangoHud's AUR override through the real build entrypoint."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-single-package.sh"
# Relevant fields from AUR mangohud-git at 61e3e93; no source code is built here.
RECIPE = """\
_pkgname=MangoHud
pkgname=mangohud-git
url='https://github.com/flightlessmango/MangoHud'
makedepends=('vulkan-headers' 'wayland-protocols')
depends=('glfw' 'libx11' 'libxkbcommon')
source=("$_pkgname::git+$url")
build() { arch-meson "$_pkgname" build -Dmangoapp=true -Dmangohudctl=true; }
"""


class MangoHudDependenciesTests(unittest.TestCase):
    def run_recipe(self, package, recipe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_dir = root / "work" / package
            package_dir.mkdir(parents=True)
            (package_dir / "PKGBUILD").write_text(recipe)
            commands = root / "bin"
            commands.mkdir()
            captured = root / "captured-PKGBUILD"
            for name, body in {
                "git": '[[ "$*" == "pull" ]]',
                "pikaur": '''
[[ "$*" == "--noconfirm -S -P PKGBUILD" ]]
bash -n PKGBUILD
cp PKGBUILD "$CAPTURED_PKGBUILD"
''',
            }.items():
                command = commands / name
                command.write_text("#!/bin/bash\nset -e\n" + body + "\n")
                command.chmod(0o755)
            result = subprocess.run(
                ["bash", str(SCRIPT), package, "aur"],
                env={
                    **os.environ,
                    "PATH": f"{commands}:{os.environ['PATH']}",
                    "GITHUB_WORKSPACE": str(ROOT),
                    "WORK_DIR": str(root / "work"),
                    "OUTPUT_DIR": str(root / "output"),
                    "CAPTURED_PKGBUILD": str(captured),
                    "SRCDEST": "",
                },
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return captured.read_text()

    def test_libxi_is_a_runtime_dependency(self):
        patched = self.run_recipe("mangohud-git", RECIPE)
        self.assertIn("depends=('glfw' 'libx11' 'libxi' 'libxkbcommon')", patched)
        self.assertNotIn("libxi", patched.split("makedepends=(", 1)[1].split(")", 1)[0])

    def test_source_and_enabled_features_are_preserved(self):
        patched = self.run_recipe("mangohud-git", RECIPE)
        expected = RECIPE.replace(
            "'vulkan-headers'", "'vulkan-headers' 'wayland-protocols'"
        ).replace("'libx11'", "'libx11' 'libxi'")
        self.assertEqual(patched, expected)

    def test_override_is_scoped_to_the_aur_package(self):
        self.assertEqual(self.run_recipe("unrelated-git", RECIPE), RECIPE)

    def test_custom_fork_pins_reviewed_candidate(self):
        recipe = (ROOT / "local/mangohud-sk-git/PKGBUILD").read_text()
        self.assertIn("url='https://github.com/honjow/MangoHud'", recipe)
        self.assertIn('"$_pkgname::git+$url#commit=305a576c7a63a546a0727f0b82fd08c1bdbf2eef"', recipe)


if __name__ == "__main__":
    unittest.main()
