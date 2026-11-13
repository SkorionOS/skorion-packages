import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).parents[1] / 'scripts/verify-lib32-mesa-archive.py'
spec = importlib.util.spec_from_file_location('archive_verify', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ArchiveTests(unittest.TestCase):
    def records(self):
        return [{'pkgname': [name], 'pkgbase': ['lib32-mesa'],
                 'pkgver': ['1:26.2.4.sk-3'], 'arch': ['x86_64'],
                 'depend': ['lib32-llvm-libs=1:23.1.4'] if name in mod.LLVM else []}
                for name in sorted(mod.EXPECTED)]
    def test_complete_family(self):
        mod.validate(self.records())
    def test_missing_split(self):
        with self.assertRaises(AssertionError): mod.validate(self.records()[:-1])
    def test_old_pin(self):
        records = self.records()
        next(r for r in records if r['pkgname'] == ['lib32-mesa'])['depend'] = ['lib32-llvm-libs=1:23.1.3']
        with self.assertRaises(AssertionError): mod.validate(records)
    def test_duplicate(self):
        records = self.records()
        with self.assertRaises(AssertionError): mod.validate(records + records[:1])
    def test_wrong_version(self):
        records = self.records()
        records[0]['pkgver'] = ['1:26.2.4.sk-2']
        with self.assertRaises(AssertionError): mod.validate(records)
