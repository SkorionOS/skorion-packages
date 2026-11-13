#!/usr/bin/env python3
"""Verify every stable 32-bit Mesa split before isolated archive publication."""
import pathlib
import subprocess
import sys

SUFFIXES = ('mesa', 'opencl-mesa', 'vulkan-asahi', 'vulkan-dzn',
            'vulkan-freedreno', 'vulkan-gfxstream', 'vulkan-intel',
            'vulkan-nouveau', 'vulkan-radeon', 'vulkan-swrast',
            'vulkan-virtio', 'vulkan-mesa-implicit-layers', 'vulkan-mesa-layers')
EXPECTED = {'lib32-' + name for name in SUFFIXES}
LLVM = {'lib32-mesa', 'lib32-opencl-mesa', 'lib32-vulkan-radeon', 'lib32-vulkan-swrast'}

def fields(text):
    result = {}
    for line in text.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            result.setdefault(key, []).append(value)
    return result

def validate(records):
    seen = set()
    for info in records:
        name, = info['pkgname']
        assert name in EXPECTED and name not in seen, f'Unexpected/duplicate split: {name}'
        seen.add(name)
        assert info['pkgbase'] == ['lib32-mesa']
        assert info['pkgver'] == ['1:26.2.4.sk-3']
        assert info['arch'] == ['x86_64']
        pins = [v for v in info.get('depend', []) if v.startswith('lib32-llvm-libs')]
        if name in LLVM:
            assert pins == ['lib32-llvm-libs=1:23.1.4'], (name, pins)
    assert seen == EXPECTED, f'Missing splits: {EXPECTED - seen}'

if __name__ == '__main__':
    files = sorted(pathlib.Path(sys.argv[1]).glob('*.pkg.tar.zst'))
    records = [fields(subprocess.check_output(['bsdtar', '-xOf', str(p), '.PKGINFO'], text=True)) for p in files]
    validate(records)
    print('Verified all 13 splits and four LLVM pins against archive 2026/11/10')
