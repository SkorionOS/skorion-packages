"""Offline contracts and executable C regressions for the kmscon 10.0.4 port.

Run with KMSCON_SOURCE_TARBALL pointing at the checksum-verified official archive
for strict patch/integration tests. KMSCON_TEST_INCLUDE may point at a directory
containing official libtsm.h and xkbcommon headers for the complete text test.
No downloads, installations, DRM device access, or VT changes are performed.
"""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'local/kmscon'
PATCH = PACKAGE / '0001-auto-detect-panel-orientation.patch'
ARCHIVE_SHA256 = '6918c748c26e9c8cfe81783400be3732792a73e532d6235f40c4b332ca4fde0b'


def function(source, signature):
    """Extract an actual C function body, not a Python reimplementation."""
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def added_source(path):
    part = PATCH.read_text().split(f'diff --git a/{path} b/{path}\n', 1)[1]
    part = part.split('\ndiff --git ', 1)[0]
    return '\n'.join(line[1:] for line in part.splitlines()
                     if line.startswith('+') and not line.startswith('+++'))


class KmsconPackageContract(unittest.TestCase):
    def test_stable_source_and_checksum(self):
        recipe = (PACKAGE / 'PKGBUILD').read_text()
        self.assertIn('pkgver=10.0.4', recipe)
        self.assertIn('$url/archive/refs/tags/v$pkgver.tar.gz', recipe)
        self.assertIn(ARCHIVE_SHA256, recipe)
        self.assertNotIn('SKIP', recipe)
        self.assertIn(hashlib.sha256(PATCH.read_bytes()).hexdigest(), recipe)
        subprocess.run(['bash', '-n', PACKAGE / 'PKGBUILD'], check=True)

    def test_dependencies_and_terminfo_ownership(self):
        recipe = (PACKAGE / 'PKGBUILD').read_text()
        self.assertRegex(recipe, r'\n  dbus\n')
        self.assertIn("'libtsm>=4.8.0'", recipe)
        self.assertIn('"kmscon-terminfo>=$pkgver"', recipe)
        self.assertIn('rm -r "$pkgdir/usr/share/terminfo"', recipe)
        self.assertNotRegex(recipe, r'(?m)^conflicts=.*kmscon-terminfo')
        self.assertIn('--wrap-mode=nofallback', recipe)
        self.assertIn('-Ddbus=enabled', recipe)
        self.assertIn('meson test -C build --print-errorlogs', recipe)
        self.assertIn('patch --batch --forward --fuzz=0 -p1', recipe)

    def test_patch_uses_current_interfaces(self):
        targets = re.findall(r'^\+\+\+ b/(.*)$', PATCH.read_text(), re.M)
        self.assertEqual(set(targets), {
            'src/render/text.c', 'src/render/text.h', 'src/video/video.c',
            'src/video/video.h', 'src/video/video_internal.h',
            'src/video/drm_shared.c', 'tests/test_text.c',
        })
        self.assertNotIn('uterm_display', PATCH.read_text())
        renderer = added_source('src/render/text.c')
        self.assertEqual(renderer.count('display_get_panel_orientation(disp)'), 1)
        self.assertIn('before backend init and font sizing', renderer)
        drm = added_source('src/video/drm_shared.c')
        self.assertIn('disp->panel_orientation = read_panel_orientation(vdrm->fd, conn)', drm)
        self.assertIn('"rotation", DRM_MODE_ROTATE_0', drm)


class CCompilerMixin:
    def compile_run(self, source):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='kmscon-test-') as tmp:
            c_file = Path(tmp) / 'test.c'
            binary = Path(tmp) / 'test'
            c_file.write_text(source)
            subprocess.run([cc, '-std=gnu99', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-fsanitize=undefined',
                            str(c_file), '-o', str(binary)], check=True,
                           capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


class CompiledOrientationHelpers(CCompilerMixin, unittest.TestCase):
    def helpers(self):
        drm = added_source('src/video/drm_shared.c')
        enum = added_source('src/video/video.h')
        enum = enum[enum.index('enum panel_orientation {'):enum.index('};') + 2]
        return enum + '\n' + function(drm, 'static enum panel_orientation read_panel_orientation')

    def test_display_getter_default_and_all_values(self):
        enum = self.helpers().split('static enum panel_orientation', 1)[0]
        getter = function(added_source('src/video/video.c'),
                          'enum panel_orientation display_get_panel_orientation')
        self.compile_run('#include <assert.h>\n#include <stddef.h>\n' + enum +
                         'struct display { enum panel_orientation panel_orientation; };\n' +
                         getter + r'''
int main(void) {
    struct display disp = {0};
    assert(display_get_panel_orientation(NULL) == PANEL_ORIENTATION_NORMAL);
    assert(display_get_panel_orientation(&disp) == PANEL_ORIENTATION_NORMAL);
    for (int i = 0; i < 4; i++) {
        disp.panel_orientation = i;
        assert((int)display_get_panel_orientation(&disp) == i);
    }
    return 0;
}
''')

    def test_kernel_property_names_values_and_fallbacks(self):
        self.compile_run(r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define log_info(...) ((void)0)
struct prop_enum { uint64_t value; char name[32]; };
typedef struct { char name[32]; int count_enums; struct prop_enum enums[5]; } Property;
typedef Property *drmModePropertyPtr;
typedef struct { int count_props; uint32_t *props; uint64_t *prop_values; uint32_t connector_id; } drmModeConnector;
static Property unrelated = {.name = "DPMS"};
static Property panel = {.name = "panel orientation", .count_enums = 5,
    .enums = {{40,"Normal"},{91,"Upside Down"},{17,"Left Side Up"},{3,"Right Side Up"},{100,"Future Value"}}};
static int gets, frees;
static drmModePropertyPtr drmModeGetProperty(int fd, uint32_t id) {
    gets++; return id == 1 ? &unrelated : id == 2 ? &panel : NULL;
}
static void drmModeFreeProperty(drmModePropertyPtr prop) { assert(prop); frees++; }
''' + self.helpers() + r'''
int main(void) {
    uint32_t ids[] = {0, 1, 2};
    uint64_t values[] = {0, 0, 40};
    drmModeConnector conn = {3, ids, values, 12};
    const unsigned int expected[] = {0, 2, 3, 1, 0};
    for (int i = 0; i < 5; i++) {
        values[2] = panel.enums[i].value; gets = frees = 0;
        assert((unsigned int)read_panel_orientation(1, &conn) == expected[i]);
        assert(gets == 3 && frees == 2);
    }
    values[2] = 999; assert(read_panel_orientation(1, &conn) == 0);
    panel.count_enums = 0; assert(read_panel_orientation(1, &conn) == 0);
    conn.count_props = 2; assert(read_panel_orientation(1, &conn) == 0);
    conn.count_props = 0; assert(read_panel_orientation(1, &conn) == 0);
    assert(read_panel_orientation(1, NULL) == 0);
    return 0;
}
''')

    def test_optional_plane_reset_and_failure_propagation(self):
        helper = function(added_source('src/video/drm_shared.c'),
                          'static int set_drm_object_property_optional')
        self.compile_run(r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
typedef struct { int unused; } drmModeAtomicReq;
typedef struct { unsigned int count_props; } drmModeObjectProperties;
typedef struct { uint32_t prop_id; char name[32]; } drmModePropertyRes;
struct drm_object { drmModeObjectProperties *props; drmModePropertyRes **props_info; uint32_t id; };
static int calls, result;
static int drmModeAtomicAddProperty(drmModeAtomicReq *req, uint32_t id, uint32_t prop, uint64_t value) {
    assert(req && id == 7 && prop == 21 && value == 1); calls++; return result;
}
''' + helper + r'''
int main(void) {
    drmModeAtomicReq req = {0};
    drmModePropertyRes rotation = {21, "rotation"}, unrelated = {22, "other"};
    drmModePropertyRes *infos[] = {NULL, &unrelated, &rotation};
    drmModeObjectProperties props = {2};
    struct drm_object obj = {&props, infos, 7};
    assert(set_drm_object_property_optional(&req, &obj, "rotation", 1) == 0 && calls == 0);
    props.count_props = 3;
    for (int i = 0; i < 2; i++)
        assert(set_drm_object_property_optional(&req, &obj, "rotation", 1) == 0);
    assert(calls == 2);
    result = -ENOSPC;
    assert(set_drm_object_property_optional(&req, &obj, "rotation", 1) == -ENOSPC);
    obj.props = NULL;
    assert(set_drm_object_property_optional(&req, &obj, "rotation", 1) == -EINVAL);
    return 0;
}
''')


class PatchedSourceIntegration(CCompilerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = os.environ.get('KMSCON_SOURCE_TARBALL')
        if not archive:
            raise unittest.SkipTest('Set KMSCON_SOURCE_TARBALL for source integration tests')
        if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise AssertionError('Archive SHA256 does not match official v10.0.4 source')
        cls.tmp = tempfile.TemporaryDirectory(prefix='kmscon-source-')
        cls.addClassCleanup(cls.tmp.cleanup)
        with tarfile.open(archive) as tar:
            tar.extractall(cls.tmp.name, filter='data')
        cls.source = Path(cls.tmp.name) / 'kmscon-10.0.4'
        run = subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(PATCH)],
                             cwd=cls.source, capture_output=True, text=True, check=True)
        if re.search(r'offset|fuzz|FAILED|reject', run.stdout + run.stderr, re.I):
            raise AssertionError(run.stdout + run.stderr)

    def test_constructor_applies_once_before_backend_and_font_sizing(self):
        text = (self.source / 'src/render/text.c').read_text()
        constructor = function(text, 'int kmscon_text_new(')
        self.assertLess(constructor.index('display_get_panel_orientation(disp)'),
                        constructor.index('ret = new_text(text, backend)'))
        self.assertNotIn('display_get_panel_orientation', function(text, 'int kmscon_text_set('))
        self.assertNotIn('display_get_panel_orientation', function(text, 'int kmscon_text_rotate('))
        video = (self.source / 'src/video/video.c').read_text()
        self.assertIn('SHL_EXPORT\nint display_get_state(', video)
        self.assertIn('SHL_EXPORT\nenum panel_orientation display_get_panel_orientation(', video)

    def test_atomic_commit_resets_each_time_and_legacy_is_unchanged(self):
        drm = (self.source / 'src/video/drm_shared.c').read_text()
        commit = function(drm, 'int drm_prepare_commit(')
        self.compile_run(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DRM_MODE_ROTATE_0 1
#define log_warn(...) ((void)0)
typedef struct { int unused; } drmModeAtomicReq;
struct drm_object { uint32_t id; };
struct drm_cursor { bool active, visible; uint32_t hot_x, hot_y, fb_id, width, height; int32_t off_x, off_y, x, y; };
struct drm_display { struct drm_object plane, connector, crtc, cursor_plane; uint32_t mode_blob_id, fb_id, damage_blob_id; struct drm_cursor cursor; };
static int resets, failure;
static int set_drm_object_property(drmModeAtomicReq *req, struct drm_object *obj, const char *name, uint64_t value) { return 0; }
static int set_drm_object_property_optional(drmModeAtomicReq *req, struct drm_object *obj, const char *name, uint64_t value) {
    assert(req && obj->id == 7 && !strcmp(name,"rotation") && value == DRM_MODE_ROTATE_0);
    resets++; return failure;
}
''' + commit + r'''
int main(void) {
    drmModeAtomicReq req = {0}; struct drm_display disp = {.plane.id=7};
    assert(drm_prepare_commit(1,&disp,&req,99,800,480,false)==0);
    assert(drm_prepare_commit(1,&disp,&req,99,800,480,false)==0);
    assert(resets==2);
    failure=-1; assert(drm_prepare_commit(1,&disp,&req,99,800,480,false)==-1);
    assert(resets==3);
    assert(drm_prepare_commit(1,&disp,NULL,101,800,480,false)==0);
    assert(resets==3 && disp.fb_id==101);
    return 0;
}
''')

    def test_upstream_renderer_c_regressions(self):
        include = Path(os.environ.get('KMSCON_TEST_INCLUDE', '/usr/include'))
        if not (include / 'libtsm.h').exists() or not (include / 'xkbcommon/xkbcommon.h').exists():
            self.skipTest('Official libtsm and xkbcommon development headers needed')
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('C compiler unavailable')
        support = Path(self.tmp.name) / 'test-support.c'
        support.write_text('''#include "shl/log.h"
#include "shl/module.h"
void log_format(enum log_severity level, const char *subs, const char *format, ...) {}
void shl_module_ref(struct shl_module *module) {}
void shl_module_unref(struct shl_module *module) {}
''')
        binary = Path(self.tmp.name) / 'test_text'
        run = subprocess.run([cc, '-std=gnu99', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror',
                              '-Wno-unused-parameter', '-ffunction-sections', '-fdata-sections',
                              '-fsanitize=undefined', '-I', str(include), '-I', str(self.source / 'src'),
                              str(self.source / 'tests/test_text.c'), str(support),
                              '-Wl,--gc-sections', '-pthread', '-o', str(binary)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
