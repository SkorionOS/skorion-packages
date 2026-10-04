"""Offline AIC recipe tests; no network, kernel build, install or module load."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "local/aic8800d80-dkms/PKGBUILD"


class AIC8800RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aic recipe ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src" / "aic8800d80"
        self.source.mkdir(parents=True)
        self.pkgdir = self.root / "pkg"
        self.pkgdir.mkdir()
        self.write("dkms.conf", 'PACKAGE_NAME="aic8800"\nPACKAGE_VERSION="1.0.0"\n'
                   'BUILT_MODULE_NAME[2]="aic_zlp_quirk"\n')
        self.write("drivers/aic8800/Makefile", "# Offline fixture only\n")
        self.write("drivers/aic8800/aic_zlp_quirk/aic_zlp_quirk.c", "/* fixture */\n")
        self.write("fw/aic8800D80/main.bin", "main firmware\n")
        self.write("fw/aic8800D80N/revision.bin", "revision firmware\n")
        self.write("aic.rules", "# udev fixture\n")
        self.write("usb_modeswitch/1111_1111", "DefaultVendor=0x1111\n")
        self.write("README.md", "main profile; MCU1 uses legacy-mcu1\n")
        self.write("install.sh", "exit 99\n")
        self.git("init", "-q")
        self.commit()
        self.env = {
            **os.environ,
            "RECIPE": str(RECIPE),
            "srcdir": str(self.source.parent),
            "pkgdir": str(self.pkgdir),
        }

    def write(self, name, text):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.source, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "offline fixture")

    def shell(self, command):
        return subprocess.run(["bash", "-c", 'set -e; source "$RECIPE"; ' + command],
                              env=self.env, text=True, capture_output=True, timeout=10)

    def version(self):
        result = self.shell("pkgver")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_actual_git_revision_changes_the_version(self):
        first = self.version()
        self.assertRegex(first, r"^1\.0\.0\.r1\.g[0-9a-f]{7}$")
        self.write("revision.txt", "new upstream commit\n")
        self.commit()
        second = self.version()
        self.assertRegex(second, r"^1\.0\.0\.r2\.g[0-9a-f]{7}$")
        self.assertNotEqual(first, second)
        self.assertEqual(second, self.version())

    def test_invalid_driver_version_fails_closed(self):
        self.write("dkms.conf", 'PACKAGE_NAME="aic8800"\nPACKAGE_VERSION="unknown"\n')
        self.assertNotEqual(self.shell("pkgver").returncode, 0)

    def test_package_layout_keeps_all_same_revision_firmware_and_dkms_inputs(self):
        version = self.version()
        original = (self.source / "dkms.conf").read_text()
        result = self.shell('pkgver="$(pkgver)"; package')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = self.pkgdir / "usr/src" / f"aic8800-{version}"
        self.assertIn(f'PACKAGE_VERSION="{version}"', (installed / "dkms.conf").read_text())
        self.assertIn('PACKAGE_NAME="aic8800"', (installed / "dkms.conf").read_text())
        self.assertIn('BUILT_MODULE_NAME[2]="aic_zlp_quirk"',
                      (installed / "dkms.conf").read_text())
        self.assertTrue((installed / "drivers/aic8800/aic_zlp_quirk/aic_zlp_quirk.c").is_file())
        self.assertTrue((self.pkgdir / "usr/lib/firmware/aic8800D80/main.bin").is_file())
        self.assertTrue((self.pkgdir / "usr/lib/firmware/aic8800D80N/revision.bin").is_file())
        self.assertTrue((self.pkgdir / "etc/usb_modeswitch.d/1111:1111").is_file())
        self.assertTrue((self.pkgdir / "usr/lib/udev/rules.d/aic.rules").is_file())
        self.assertFalse(any(self.pkgdir.rglob(".git")))
        self.assertFalse(any(self.pkgdir.rglob("install.sh")))
        self.assertEqual((self.source / "dkms.conf").read_text(), original)
        self.assertEqual(self.version(), version)

    def test_existing_update_checker_recognizes_vcs_function(self):
        self.assertRegex(RECIPE.read_text(), r"(?m)^pkgver\(\)")
        result = self.shell('printf "%s\\n" "${source[@]}" "${depends[@]}" "${makedepends[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#branch=main", result.stdout)
        for dependency in ("dkms", "usb_modeswitch", "util-linux", "git"):
            self.assertIn(dependency, result.stdout.splitlines())
        active_aur = {line.strip() for line in (ROOT / "aur.conf").read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#")}
        self.assertNotIn("aic8800d80-dkms", active_aur)


if __name__ == "__main__":
    unittest.main()
