"""Source-list checks; do not execute the system-mutating build bootstrap."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def active_aur_packages():
    return {line.strip() for line in (ROOT / "aur.conf").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


class PackageSourceMigrationTests(unittest.TestCase):
    def test_official_extra_packages_are_no_longer_fetched_from_aur(self):
        active = active_aur_packages()
        for package in ("flatseal", "xviewer"):
            with self.subTest(package=package):
                self.assertNotIn(package, active)
                self.assertFalse((ROOT / "local" / package).exists())

    def test_retired_electron_is_not_an_explicit_build_or_bootstrap_input(self):
        self.assertNotIn("electron28-bin", active_aur_packages())
        bootstrap = (ROOT / "scripts/setup-archlinux-build-env.sh").read_text()
        self.assertNotIn('DEPENDENCIES_PACKAGES+=" electron28-bin"', bootstrap)
        block = re.search(r'if \[ "\$PACKAGE_NAME" == "bilibili-bin" \]; then\n(.*?)\nfi',
                          bootstrap, re.S)
        self.assertIsNotNone(block)
        # Keep the same repository available; pikaur resolves the recipe's
        # current versioned Electron dependency, rather than a hardcoded major.
        self.assertIn("[skorion]", block.group(1))
        self.assertNotIn("DEPENDENCIES_PACKAGES+=", block.group(1))

    def test_locally_maintained_overrides_are_not_built_twice(self):
        active = active_aur_packages()
        local = {path.parent.name for path in (ROOT / "local").glob("*/PKGBUILD")}
        self.assertFalse(active & local, f"Duplicate AUR/local inputs: {active & local}")


if __name__ == "__main__":
    unittest.main()
