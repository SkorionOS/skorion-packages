# 2026-10-04 package refresh

This change implements the first four groups from the upstream assessment.
It is a local, unpublished change based on `a1983848d678306b235b42671721842700083b1f`.
It does not change workflow files, triggers, release/database scripts, or the
InputPlumber, PowerStation, Gamescope and SteamOS Manager custom branches.

## Package changes

| Package | Prepared version or change |
| --- | --- |
| mesa / lib32-mesa | `1:26.2.4.sk-1`; all existing 27 split outputs and local build policy retained |
| bootupd | `1:0.2.29.sdboot.r1371.g7db29c7-1`; retain p5/sdboot-support, report its real source version and add OpenSSL |
| bootc | `1.16.14-1`; complete upstream install, SELinux/clang and required runtime/build tools |
| bootc-git | `1.16.14.r70.g66d4e4d-3`; same source/build behavior and prior dependencies, with the matching Arch hook-path correction |
| hhd-git family | `4.1.12+121.r2775.20260930.a87fb308-2`; required Python dependencies, lsof and LGPL metadata |
| xonedo-sk-dkms | `0.5.8.r1.gf97d11e-2`; PID-specific firmware dependency and matching DKMS identity |
| aic8800d80-dkms | New local recipe, `1.0.0.r109.gb72eea9-1`; actual Git revision tracking, same main hardware profile, matching firmware and mode-switch data |
| cursor-byok | `1.0.1-1`; actual upstream executable, GUI/tray dependencies, desktop file, icons and license |
| mint-themes / mint-y-icons | `2.4.2-1` / `1.9.6-1`; checksummed official GitHub archives and preserved symlinks |
| nbfc-linux | Local source recipe `0.5.3-1`, including required Lua 5.4 and libxml2 |
| fpaste | Local recipe `0.5.0.0-1`, using the verified canonical Codeberg source |
| sx | Local source-built recipe `2.5.0-1`; package name and source distribution method retained |
| gnome-shell-extension-logo-menu | Local recipe `25.2_280926-1`, with upstream GNOME Shell 49-51 bounds |
| kmscon | `10.0.4-1`; ported panel-orientation/plane-reset patch, DBus/libtsm requirements and official terminfo package |

The five new local recipes are removed from `aur.conf`, preventing duplicate
AUR/local builds. The resulting input lists contain 95 AUR entries and 38 local
recipe directories. This count is not a count of binary split outputs.

Both bootc channels install their three ostree extension hooks in Arch's
`/usr/lib/libostree/ext`, with symlinks resolving to `/usr/bin/bootc`, rather
than upstream's `/usr/libexec` default. Other libexec contents are retained;
bootupd's independent path and the existing bootc-git build behavior are unchanged.

## LLVM ABI policy

Native LLVM consumers declare bare `libLLVM.so`; makepkg derives the actual
required version from built ELF files. Do not replace this with a guessed ABI.

Arch's current `lib32-llvm-libs` does not publish a 32-bit SONAME virtual provide.
The four LLVM-using 32-bit splits therefore inspect their actual ELF NEEDED
entries and the installed provider, require a matching ELF32/SONAME/owner, and
pin `lib32-llvm-libs` to the installed `epoch:pkgver`. `pkgrel` is deliberately
omitted, so packaging-only rebuilds remain installable.

An upstream LLVM version change, even a patch release, requires a coordinated
rebuild of these four 32-bit splits. The existing version-only update detector
may require an explicit rebuild or package-release bump. This change does not
add CI triggers or silently allow an unvalidated new runtime. The conservative
pin can block an LLVM upgrade until the matching Mesa rebuild is available.

## Official repository and legacy runtime migration

Flatseal and Xviewer are now Arch Extra packages with their original names:
`flatseal` 2.4.1-1 and `xviewer` 3.4.17-1 at assessment time. No renamed provide is
needed. The existing Skorion image manifests request these same package names.
They have been removed only from this repository's AUR build list.

Electron 28 was not required by any of the 191 published packages, using the
actual repository dependency metadata. Current Bilibili requires `electron43`;
`hhd-ui` requires unversioned `electron`, which `electron28-bin` does not provide.
The current SkorionOS image source at
`5b79fc08caa43f53668051c5f40787c778ddb794` also has no Electron 28 request in the
main/branch manifests, AUR setup paths or Dockerfile. This is not a scan of every
user's installed software. New builds no longer request Electron 28, and the
Bilibili bootstrap no longer forces it. The existing Skorion repository remains
available; pikaur resolves Bilibili's actual recipe dependency.

### Publication gate

Removing build-list entries does **not** remove existing release assets or
pacman database records. None were deleted or republished during this work.
Do not call the online migration complete after merely applying this patch.

After explicit publication approval and a successful build/upgrade check:

1. Back up the current `latest` database and preserve a recovery snapshot
2. Remove the old `flatseal` and `xviewer` assets and database records together,
   so the higher-priority Skorion repository no longer masks Arch Extra
3. Reconfirm downstream Electron 28 consumers, then retire its asset/database
   record if still unused; do not remove unrelated Electron versions
4. Regenerate and verify both repository databases before publishing them;
   verify installed systems can resolve the original Flatseal/Xviewer names
   from Extra, and Bilibili resolves Electron 43

The currently identified old assets are:

- `flatseal-2.4.0-1-any.pkg.tar.zst`
- `xviewer-3.4.15-1-x86_64.pkg.tar.zst`
- `electron28-bin-28.3.3-3-x86_64.pkg.tar.zst`

No filtering framework or database-workflow rewrite was introduced. This
cleanup remains a separate, explicitly approved release operation.

## Verification and remaining release gates

The final source-integrated run passed **83 tests with zero skips**, plus the
existing standalone bootc dependency check and shell syntax for every local
recipe. Full package builds remain unrun as detailed below.

Permanent regression tests cover the package metadata, real source checksums,
version ordering, source-driven Git versions, staging layouts, ELF32/64 ABI
fixtures, makepkg SONAME behavior, sequential split isolation, migration lists,
and the actual patched kmscon renderer's rotation combinations.

The source-integrated test run needs previously verified upstream inputs; the
normal offline runner explicitly skips unavailable source integrations instead
of downloading or claiming to execute them. See the environment inputs in
`tests/test_core_recipe_updates.py`, `tests/test_desktop_recipe_updates.py`,
`tests/test_stable_mesa_packaging.py` and `tests/test_kmscon_orientation_patch.py`.

The Mesa tarball and all 30 crates were checksummed, and Mesa's source signature
was verified against the existing allowlisted release signer. Cursor's payload
was inspected with `readelf`, not executed. AIC, Cursor, fpaste and Mint-Y icons
were staged without installing them on the host. Mocked/placeholder build
outputs used in other layout tests are identified in those tests.

The kmscon patch applies with zero fuzz/offsets/rejects. Its changed translation
units compile with official headers, and its renderer/DRM unit harnesses cover
all 16 panel/user rotation combinations and repeated font/atomic setup. See
`local/kmscon/VALIDATION.md` for the detailed limits and device test matrix.

Still required before publishing binaries:

- Full clean Arch builds, dependency resolution, `.PKGINFO`/ELF checks, and real
  pacman upgrade transactions for the changed recipes and all split outputs
- Boot/install/update/rollback tests for bootc and the unchanged bootupd fork
- Explicit SELinux-enabled image checks: bootc uses `chcon` in that path, while
  ordinary Arch coreutils does not supply it; this patch does not switch
  coreutils providers or enable SELinux
- Portrait/landscape DRM hardware, hotplug and repeated VT testing for kmscon
- AIC target USB IDs, matching firmware and suspend/resume tests; upstream
  MCU1 hardware needs its separate legacy-mcu1 profile and must not receive a
  mixed firmware set
- xonedo dongle/firmware and HHD service/plugin testing on a clean image
- NBFC fan/EC safety and fallback checks on supported hardware
- Cursor GUI/tray and GNOME extension tests in the intended desktop session

The current cloud validation environment lacks makepkg/pacman, an Arch chroot
or container runner, and several full-build toolchains. Source/fixture checks do
not establish successful package builds or hardware operation.

## References

- [Mesa 26.2.4](https://docs.mesa3d.org/relnotes/26.2.4.html)
- [Retained bootupd source](https://github.com/p5/coreos-bootupd/commit/7db29c74bbb8a494ea142b008307a957061af17d)
- [bootc 1.16.14](https://github.com/bootc-dev/bootc/tree/v1.16.14)
- [AIC main hardware profile](https://github.com/shenmintao/aic8800d80/tree/b72eea956451d6a351292cd6cd46b44b48e65b8d)
- [Flatseal Extra](https://archlinux.org/packages/extra/any/flatseal/)
- [Xviewer Extra](https://archlinux.org/packages/extra/x86_64/xviewer/)
- [Electron support schedule](https://releases.electronjs.org/schedule)
- [Skorion image source inspected](https://github.com/SkorionOS/skorionos/tree/5b79fc08caa43f53668051c5f40787c778ddb794)
