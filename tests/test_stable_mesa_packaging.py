"""Offline regression checks for the coordinated stable Mesa split families.

The sources were verified against the official 26.2.4 archive and its wrap files.
ELF fixtures are compiled, never executed. No pacman database, system libraries,
network, or full Mesa build is needed. This is not a clean-chroot build test.

Optional integration checks use STABLE_MESA_VERCMP (pacman's vercmp binary)
and STABLE_MESA_LIBDEPENDS (Arch makepkg's find_libdepends function file).
The latter must define functions only, not run the complete makepkg program.
"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECIPES = {name: ROOT / "local" / name / "PKGBUILD"
           for name in ("mesa", "lib32-mesa")}
LLVM_SPLITS = ("mesa", "opencl-mesa", "vulkan-radeon", "vulkan-swrast")
SPLITS = (
    "mesa", "opencl-mesa", "vulkan-asahi", "vulkan-dzn", "vulkan-freedreno",
    "vulkan-gfxstream", "vulkan-intel", "vulkan-nouveau", "vulkan-radeon",
    "vulkan-swrast", "vulkan-virtio", "vulkan-mesa-implicit-layers",
    "vulkan-mesa-layers",
)
GALLIUM = "r300,r600,radeonsi,nouveau,virgl,svga,llvmpipe,softpipe,iris,crocus,i915,zink,d3d12,asahi,freedreno"
VULKAN = "amd,freedreno,intel,intel_hasvk,swrast,virtio,microsoft-experimental,nouveau,asahi,gfxstream"
LAYERS = "device-select,intel-nullhw,overlay,screenshot,anti-lag,vram-report-limit"


def bash(recipe, script, *, env=None, cwd=None, check=True):
    result = subprocess.run(
        ["bash", "-c", 'set -e; source "$1"; ' + script, "bash", str(recipe)],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=30,
    )
    if check and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result


def array(recipe, name):
    return bash(recipe, f'printf "%s\\n" "${{{name}[@]}}"').stdout.splitlines()


class StableMesaRecipeTests(unittest.TestCase):
    def test_bash_syntax_and_coordinated_version(self):
        for recipe in RECIPES.values():
            with self.subTest(recipe=recipe):
                subprocess.run(["bash", "-n", str(recipe)], check=True)
                result = bash(recipe, 'printf "%s\\n" "$_pkgver" "$epoch:$pkgver-$pkgrel"')
                self.assertEqual(result.stdout.splitlines(), ["26.2.4", "1:26.2.4.sk-1"])

    def test_complete_source_manifest_is_verified_and_shared(self):
        manifests = []
        for recipe in RECIPES.values():
            sources = array(recipe, "source")
            sha256 = array(recipe, "sha256sums")
            blake2 = array(recipe, "b2sums")
            self.assertEqual(len(sources), 32)
            self.assertEqual(len(sha256), len(sources))
            self.assertEqual(len(blake2), len(sources))
            self.assertEqual(sources[:2], [
                "https://archive.mesa3d.org/mesa-26.2.4.tar.xz",
                "https://archive.mesa3d.org/mesa-26.2.4.tar.xz.sig",
            ])
            self.assertEqual(sha256[0], "bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9")
            self.assertEqual(blake2[0], "28506a9366e3c031acbb52924e43b0ec098f44a3ac3bb839ec2a86fa0faf83652347704b0e58bdc447aaebae5a2a254e5456e0b893755fd7721e57d6b614ecc4")
            self.assertEqual([i for i, value in enumerate(sha256) if value == "SKIP"], [1])
            self.assertEqual([i for i, value in enumerate(blake2) if value == "SKIP"], [1])
            # Hash canonical (source, SHA256, BLAKE2) tuples so Bash associative
            # array iteration order cannot hide a misaligned crate checksum.
            manifest = "\n".join(sorted("\t".join(row) for row in zip(sources, sha256, blake2))) + "\n"
            self.assertEqual(hashlib.sha256(manifest.encode()).hexdigest(),
                             "513d074436959906ec1680ff74bc5c8ce770c6104fa67350a908856d955d815b")
            manifests.append(manifest)
            self.assertEqual(len(array(recipe, "validpgpkeys")), 6)
        self.assertEqual(*manifests)

    def test_existing_split_families_and_local_driver_policy_are_preserved(self):
        self.assertEqual(array(RECIPES["mesa"], "pkgname"), [*SPLITS, "mesa-docs"])
        self.assertEqual(array(RECIPES["lib32-mesa"], "pkgname"), ["lib32-" + p for p in SPLITS])
        for name, recipe in RECIPES.items():
            text = recipe.read_text()
            self.assertIn("!lto", array(recipe, "options"))
            self.assertIn(f"-D gallium-drivers={GALLIUM}\n", text)
            self.assertIn(f"-D vulkan-drivers={VULKAN}\n", text)
            self.assertIn(f"-D vulkan-layers={LAYERS}\n", text)
            self.assertIn("-D video-codecs=all\n", text)
            self.assertIn('CFLAGS+=" -g1"', text)
            self.assertIn('CXXFLAGS+=" -g1"', text)
            self.assertIn('CFLAGS+=" -include $srcdir/d3d12-infoqueue-fwd.h"', text)
            self.assertIn('CXXFLAGS+=" -include $srcdir/d3d12-infoqueue-fwd.h"', text)
            # Keep per-architecture manifests and the local sysprof policy.
            self.assertNotIn("vulkan-manifest-per-architecture=false", text)
            self.assertIn("_pick vkradeon $icddir/radeon_icd.*.json", text)
            self.assertIn("-D sysprof=" + ("true" if name == "mesa" else "false"), text)
            # pycparser is only needed by etnaviv's hwdb generator in 26.2.4;
            # etnaviv is not among this repository's enabled Gallium drivers.
            self.assertNotIn("etnaviv", GALLIUM.split(","))

    def test_prepare_preserves_cache_version_and_guarded_directx_workaround(self):
        for recipe in RECIPES.values():
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp)
                (src / "mesa-26.2.4").mkdir()
                bash(recipe, 'srcdir=$PWD; prepare', cwd=src)
                self.assertEqual((src / "mesa-26.2.4" / "VERSION").read_text(), "26.2.4-arch1.1\n")
                header = (src / "d3d12-infoqueue-fwd.h").read_text()
                for line in ("#ifndef __ASSEMBLER__", "#ifndef __ID3D12InfoQueue_FWD_DEFINED__",
                             "#ifdef __cplusplus", "struct ID3D12InfoQueue;",
                             "typedef struct ID3D12InfoQueue ID3D12InfoQueue;"):
                    self.assertIn(line, header)
                if shutil.which("gcc"):
                    include = str(src / "d3d12-infoqueue-fwd.h")
                    subprocess.run(["gcc", "-x", "c", "-fsyntax-only", "-include", include, "-"],
                                   input="ID3D12InfoQueue *info;\n", text=True, check=True, capture_output=True)
                    result = subprocess.run(["gcc", "-x", "assembler-with-cpp", "-E", "-P",
                                             "-include", include, "-"], input="nop\n", text=True,
                                            check=True, capture_output=True)
                    self.assertEqual(result.stdout.strip(), "nop")


@unittest.skipUnless(shutil.which("gcc") and shutil.which("readelf"), "gcc and readelf required")
class StableMesaLLVMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = self.root / "provider"
        self.provider.mkdir()
        self.pkgdir = self.root / "pkg"
        (self.pkgdir / "usr/lib32").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.real_readelf = shutil.which("readelf")
        # Only pacman's database query is faked. readelf examines real ELF32/64
        # fixtures; this wrapper redirects /usr/lib32 reads into the sandbox.
        self.write_command("readelf", '''
args=("$@")
last=$((${#args[@]} - 1))
if [[ ${args[last]} == /usr/lib32/* ]]; then
  args[last]="$PROVIDER/${args[last]##*/}"
fi
exec "$REAL_READELF" "${args[@]}"
''')
        self.write_command("pacman", '''
case "$1" in
  -Qqo) printf '%s\\n' "${LLVM_OWNER:-lib32-llvm-libs}" ;;
  -Q) printf 'lib32-llvm-libs %s\\n' "$LLVM_VERSION" ;;
  *) exit 91 ;;
esac
''')
        self.env = {
            **os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PROVIDER": str(self.provider), "REAL_READELF": self.real_readelf,
            "PKGDIR": str(self.pkgdir), "LLVM_VERSION": "1:23.1.1-1",
        }

    def write_command(self, name, text):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -e\n" + text)
        path.chmod(0o755)

    def fixture(self, soname="libLLVM.so.23.1", bits=32, consumer="libfixture.so"):
        library = self.provider / soname
        subprocess.run([
            "gcc", f"-m{bits}", "-shared", "-nostdlib", "-fPIC",
            f"-Wl,-soname,{soname}", "-x", "c", "-o", str(library), "-",
        ], input="int llvm_fixture(void) { return 1; }\n", text=True, check=True, capture_output=True)
        if consumer is not None:
            # Build a matching consumer before optionally substituting a wrong
            # provider; a linker would correctly reject that mismatch itself.
            subprocess.run([
                "gcc", f"-m{bits}", "-shared", "-nostdlib", "-fPIC", "-x", "c", "-",
                "-x", "none", str(library), "-o", str(self.pkgdir / "usr/lib32" / consumer),
            ], input="extern int llvm_fixture(void); int use(void) { return llvm_fixture(); }\n",
                text=True, check=True, capture_output=True)

    def helper(self, *, check=True):
        return bash(RECIPES["lib32-mesa"], '''
pkgdir=$PKGDIR
pkgname=lib32-mesa
depends=(lib32-glibc lib32-llvm-libs mesa)
xdata=(sentinel=preserved)
_llvm32_depends
printf 'depend:%s\\n' "${depends[@]}"
printf 'xdata:%s\\n' "${xdata[@]}"
''', env=self.env, check=check)

    def test_32bit_abi_and_version_are_detected_without_fixed_llvm_number(self):
        for soname, version in (("libLLVM.so.22.1", "1:22.1.8-4"),
                                ("libLLVM.so.23.1", "1:23.1.1-2"),
                                ("libLLVM.so.24", "24.0.3-1")):
            with self.subTest(soname=soname, version=version):
                self.fixture(soname)
                self.env["LLVM_VERSION"] = version
                lines = self.helper().stdout.splitlines()
                self.assertIn("depend:lib32-llvm-libs=" + version.rsplit("-", 1)[0], lines)
                self.assertIn("xdata:sentinel=preserved", lines)
                self.assertIn("depend:lib32-glibc", lines)
                self.assertIn("depend:mesa", lines)
                self.assertNotIn("depend:lib32-llvm-libs", lines)
                self.assertFalse(any(line.startswith("depend:libLLVM") for line in lines))

    def test_missing_stale_mixed_or_wrong_architecture_abi_is_rejected(self):
        self.assertNotEqual(self.helper(check=False).returncode, 0)
        self.fixture()
        (self.provider / "libLLVM.so.23.1").unlink()
        self.assertNotEqual(self.helper(check=False).returncode, 0)
        self.fixture()
        self.fixture("libLLVM.so.22.1", consumer="libother.so")
        self.assertNotEqual(self.helper(check=False).returncode, 0)
        (self.pkgdir / "usr/lib32/libother.so").unlink()
        self.fixture(bits=64)
        self.assertNotEqual(self.helper(check=False).returncode, 0)
        self.fixture()
        self.fixture(bits=64, consumer=None)
        self.assertNotEqual(self.helper(check=False).returncode, 0)

    def test_wrong_owner_invalid_version_and_bad_provider_soname_are_rejected(self):
        self.fixture()
        self.env["LLVM_OWNER"] = "llvm-libs"
        self.assertNotEqual(self.helper(check=False).returncode, 0)
        del self.env["LLVM_OWNER"]
        for version in ("", "23.1.1", "not a package version", "1:23.1.1-1\nother-package 1-1"):
            self.env["LLVM_VERSION"] = version
            self.assertNotEqual(self.helper(check=False).returncode, 0)
        self.env["LLVM_VERSION"] = "1:23.1.1-1"
        self.fixture("libLLVM.so.22.1", consumer=None)
        shutil.copyfile(self.provider / "libLLVM.so.22.1", self.provider / "libLLVM.so.23.1")
        self.assertNotEqual(self.helper(check=False).returncode, 0)

    def test_all_and_only_llvm_split_packages_get_the_correct_dependency_policy(self):
        self.fixture()
        for family, recipe in RECIPES.items():
            for package in array(recipe, "pkgname"):
                with self.subTest(package=package):
                    # Package operations are isolated; the real dependency
                    # helper still runs against the ELF fixture after staging.
                    result = bash(recipe, '''
pkgdir=$PKGDIR
srcdir=$PWD
pkgname=$PACKAGE
xdata=(sentinel=preserved)
meson() { :; }; _pick() { :; }; mv() { :; }; rm() { :; }
ln() { :; }; install() { :; }
"package_$pkgname"
printf 'depend:%s\\n' "${depends[@]}"
printf 'xdata:%s\\n' "${xdata[@]}"
''', env={**self.env, "PACKAGE": package}, cwd=self.root)
                    lines = result.stdout.splitlines()
                    base = package.removeprefix("lib32-")
                    if family == "mesa":
                        self.assertEqual("depend:libLLVM.so" in lines, base in LLVM_SPLITS)
                    else:
                        self.assertEqual("depend:lib32-llvm-libs=1:23.1.1" in lines, base in LLVM_SPLITS)
                        self.assertNotIn("depend:libLLVM.so", lines)
                    self.assertEqual([line for line in lines if line.startswith("xdata:")],
                                     ["xdata:sentinel=preserved"])

    def test_sequential_split_packaging_does_not_leak_shared_metadata(self):
        self.fixture()
        for recipe in RECIPES.values():
            result = bash(recipe, '''
pkgdir=$PKGDIR
srcdir=$PWD
packages=("${pkgname[@]}")
xdata=(sentinel=preserved)
meson() { :; }; _pick() { :; }; mv() { :; }; rm() { :; }
ln() { :; }; install() { :; }
for package in "${packages[@]}"; do
  pkgname=$package
  # makepkg restores package overrides such as depends between split functions.
  # xdata is NOT one of those restored overrides: do not reset it here.
  depends=()
  "package_$pkgname"
  printf 'package:%s\\n' "$pkgname"
  printf 'depend:%s\\n' "${depends[@]}"
  printf 'xdata:%s\\n' "${xdata[@]}"
done
''', env=self.env, cwd=self.root)
            blocks = result.stdout.split("package:")[1:]
            self.assertEqual(len(blocks), len(array(recipe, "pkgname")))
            for block in blocks:
                package, *lines = block.splitlines()
                with self.subTest(package=package):
                    self.assertEqual([line for line in lines if line.startswith("xdata:")],
                                     ["xdata:sentinel=preserved"])
                    base = package.removeprefix("lib32-")
                    llvm_deps = [line for line in lines
                                 if line.startswith(("depend:lib32-llvm-libs=", "depend:libLLVM.so"))]
                    self.assertEqual(len(llvm_deps), int(base in LLVM_SPLITS))

    @unittest.skipUnless(os.environ.get("STABLE_MESA_VERCMP") or shutil.which("vercmp"),
                         "pacman's vercmp not available")
    def test_pacman_allows_pkgrel_but_rejects_upstream_llvm_upgrade(self):
        self.fixture()
        pin = next(line.split("=", 1)[1] for line in self.helper().stdout.splitlines()
                   if line.startswith("depend:lib32-llvm-libs="))
        vercmp = os.environ.get("STABLE_MESA_VERCMP") or shutil.which("vercmp")
        for version, expected in (("1:23.1.1-1", 0), ("1:23.1.1-2", 0),
                                  ("1:23.1.2-1", 1), ("1:24.1.0-1", 1),
                                  ("1:22.1.8-1", -1), ("23.1.1-1", -1)):
            result = subprocess.run([vercmp, version, pin], check=True, capture_output=True, text=True)
            self.assertEqual(int(result.stdout), expected, version)

    @unittest.skipUnless(os.environ.get("STABLE_MESA_LIBDEPENDS"),
                         "Arch makepkg find_libdepends integration file not available")
    def test_native_makepkg_generates_soname_dependency_from_real_elf(self):
        for soname, dependency in (("libLLVM.so.22.1", "libLLVM.so=22.1-64"),
                                   ("libLLVM.so.23.1", "libLLVM.so=23.1-64"),
                                   ("libLLVM.so.24", "libLLVM.so=24-64")):
            self.fixture(soname, bits=64)
            result = bash(RECIPES["mesa"], '''
source "$STABLE_MESA_LIBDEPENDS"
pkgdir=$PKGDIR
depends=(llvm-libs libLLVM.so)
find_libdepends
''', env=self.env, cwd=self.pkgdir)
            self.assertEqual(set(result.stdout.splitlines()), {"llvm-libs", dependency})


if __name__ == "__main__":
    unittest.main()
