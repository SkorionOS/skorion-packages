# kmscon 10.0.4 orientation port

Validated locally on 2026-10-04. This is a recipe and patch update, not a published
binary or a claim of successful hardware/VT testing.

## Source and packaging

- Official source: <https://github.com/kmscon/kmscon/archive/refs/tags/v10.0.4.tar.gz>
- Release: <https://github.com/kmscon/kmscon/releases/tag/v10.0.4>
- SHA256: `6918c748c26e9c8cfe81783400be3732792a73e532d6235f40c4b332ca4fde0b`
  The earlier assessment copy and a fresh download through the GitHub archive
  URL (also independently through codeload) matched byte-for-byte. This is archive
  integrity verification, not verification of a detached upstream signature.
- The recipe requires `libtsm>=4.8.0`, enables DBus explicitly, disables Meson
  dependency fallbacks, and runs the upstream Meson tests in `check()`.
- Arch owns terminfo in its separate `kmscon-terminfo` package. This recipe
  depends on `kmscon-terminfo>=10.0.4` and removes only the duplicate staged
  `/usr/share/terminfo` tree, preserving `TERM=kmscon` on local installation.
  It does not conflict with or replace the official terminfo package.
  Reference: <https://gitlab.archlinux.org/archlinux/packaging/packages/kmscon/-/blob/main/PKGBUILD>
- The existing configuration backup declaration and optional accelerated/font
  backends remain. No handheld fork or other package is changed by this port.

## Port decisions

The old patch's `src/text.c` and `src/uterm_*` interfaces are now
`src/render/text.c` and `src/video/*`. More importantly, a renderer owns its
single display from construction onward in 10.0.4, while `kmscon_text_set()` now
only changes its font. Apply the panel correction in the constructor, before
backend initialization and terminal font sizing. Reapplying the correction in
`set()` would both miss early geometry and reset/double-apply user rotation on
font changes.

The effective orientation remains `(user + panel) % 4`. Kernel property enum
names, rather than numeric enum IDs, map to the same corrections as before:
Normal=0, Right Side Up=1, Upside Down=2, Left Side Up=3 clockwise quarter turns.
Missing/unreadable/unrecognized properties and zero-initialized non-DRM displays
retain normal orientation. Grab-key callers already supply effective rotations;
those rotations survive subsequent font resets without a second correction.

The primary-plane `rotation` property is set to `DRM_MODE_ROTATE_0` for every
atomic commit. Drivers without that property are skipped; atomic-add errors
propagate. The new upstream legacy-modesetting early return is preserved. This
port does not claim to reset a plane on that non-atomic path. Both the new getter
and existing display-state getter retain `SHL_EXPORT` visibility.

## Passed checks

From the repository root:

```sh
KMSCON_SOURCE_TARBALL=/path/to/kmscon-10.0.4.tar.gz \
KMSCON_TEST_INCLUDE=/path/to/development-headers \
PYTHONDONTWRITEBYTECODE=1 \
python -m unittest discover -s tests -p test_kmscon_orientation_patch.py -v
```

All **9 tests passed**, with no skips in the recorded validation run:

- Package version, archive/patch checksums, dependency and terminfo contracts,
  strict patch flags, and shell syntax
- Patch application to a clean verified archive using `--fuzz=0`, with no offsets,
  rejects, or fuzz
- Compiled actual C helper bodies using mocked DRM property APIs: all named
  orientations with deliberately non-sequential enum values, missing/unreadable
  and unknown properties, getter defaults, optional rotation reset, repeat calls,
  and atomic failure propagation
- Compiled actual atomic-commit function using mocked property writes: reset on
  consecutive commits, error propagation, and unchanged legacy behavior
- Actual patched upstream `tests/test_text.c`, compiled with official libtsm and
  xkbcommon headers and stubbed logging/module services, exercising all 16
  panel/user combinations, backend fallback, pre-font geometry, default rotation,
  repeated font setup, and four grab-key turns across font resets

The C harnesses use GCC's undefined-behavior sanitizer. These are deterministic
unit tests, not DRM ioctl or framebuffer integration tests. Without the archive
and development headers, the Python runner reports the source-integration tests
as skipped instead of downloading or pretending to run them.

Additional checks passed:

- Compiled all changed translation units (`src/video/drm_shared.c`,
  `src/video/video.c`, `src/render/text.c`) with `-Wall -Werror`, GNU99, hidden
  default visibility, and official libtsm 4.8.0 / libdrm 2.4.124 / xkbcommon 1.8.1
  headers; ELF inspection confirmed both display getters are globally exported
- Existing upstream `test_bbulk` compiled and passed with UBSan
- `tic -x` compiled the upstream terminfo; `infocmp -A` read the resulting `kmscon`
  entry successfully
- Shell syntax and recipe diff whitespace checks passed. Standard Git whitespace
  warnings on the patch file itself describe required unified-diff context
  prefixes (space followed by tab / blank context lines), not C source errors

Development-source references:
<https://github.com/kmscon/libtsm/tree/v4.8.0>,
<https://dri.freedesktop.org/libdrm/libdrm-2.4.124.tar.xz>,
<https://github.com/xkbcommon/libxkbcommon/tree/xkbcommon-1.8.1>.

## Required before publishing

The Debian validation container has no `makepkg`, `arch-meson`, Meson/Ninja, or
pkg-config development packages for libdrm/libtsm/libudev/xkbcommon/DBus. A full
Arch clean-chroot package build, full Meson test suite, dynamic-link/install
validation, and pacman upgrade transaction were **not run**. Compile-only checks
do not replace these gates.

After a clean Arch build, validate on a portrait-mounted handheld and a normal
external display, using both drm2d and drm3d/gltex:

1. Fresh boot with no `--rotate`, then each explicit rotation; check text,
   dimensions, font auto-sizing, and cursor alignment
2. Repeated VT switches from gamescope and a graphical desktop; verify no
   double rotation on subsequent activations
3. Grab-key rotations followed by font changes and refreshes
4. Disconnect/reconnect, suspend/resume, and mixed-orientation multi-monitor use
5. Upgrade from the previous local kmscon while installing official
   kmscon-terminfo; verify no file ownership conflict, preserved local config,
   login, and `infocmp kmscon`

Do not treat this patch port as hardware-qualified until those checks pass.
