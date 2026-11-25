"""Filesystem deployment checks on a synthetic installation; never touches real SeaDAS."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('patch_tool',ROOT/'patch.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        sandbox_root=ROOT/'work/test-sandboxes';sandbox_root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='synthetic-',dir=sandbox_root)
        self.root=Path(self.tmp.name).resolve()
        self.assertTrue(self.root.is_relative_to(sandbox_root.resolve()))
        self.home=self.root/'SeaDAS';self.target=self.home/tool.MODULE
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'synthetic original module')
        self.original=tool.sha(self.target)
        self.package=self.root/'package';self.package.mkdir()
        self.payload=self.package/'patched.jar';self.payload.write_bytes(b'synthetic patched module')
        self.manifest={'schema':1,'patch_version':2,'module':tool.MODULE.as_posix(),
                       'original_sha256':self.original,'patched_sha256':tool.sha(self.payload),'candidate_only':False}
        tool.write_json(self.package/'manifest.json',self.manifest)
        self.cache=self.root/'profile/var/cache';self.cache.mkdir(parents=True)
        (self.cache/'all-resources.dat').write_bytes(b'synthetic stale class cache')
        self.settings=self.root/'profile/config/user-rgb-profile.txt'
        self.settings.parent.mkdir();self.settings.write_text('keep this profile')
        self.closed=mock.object(tool,'assert_closed',return_value=None);self.closed.start()
        self.output=contextlib.redirect_stdout(io.StringIO());self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None,None,None);self.closed.stop()
        # Recursive cleanup is restricted to the previously checked, task-created sandbox.
        self.assertTrue(self.root.is_relative_to((ROOT/'work/test-sandboxes').resolve()))
        self.tmp.cleanup()

    def install(self):
        return tool.install(self.home,self.package,self.cache)

    def test_install_then_rollback_preserves_settings_and_cache_backups(self):
        receipt=self.install();backup=Path(receipt['backup'])
        self.assertEqual(tool.sha(self.target),self.manifest['patched_sha256'])
        self.assertEqual(tool.sha(backup/'original.jar'),self.original)
        self.assertFalse((self.cache/'all-resources.dat').exists())
        self.assertEqual(Path(receipt['cache_backup']).read_bytes(),b'synthetic stale class cache')
        (self.cache/'all-resources.dat').write_bytes(b'synthetic rebuilt cache')
        restored=tool.rollback(self.home,backup)
        self.assertEqual(tool.sha(self.target),self.original)
        self.assertEqual(Path(restored['cache_backup']).read_bytes(),b'synthetic rebuilt cache')
        self.assertEqual(self.settings.read_text(),'keep this profile')

    def test_wrong_installed_version_is_not_overwritten(self):
        self.target.write_bytes(b'another upgraded module')
        with self.assertRaisesRegex(ValueError,'ORIGINAL hash'):self.install()
        self.assertEqual(self.target.read_bytes(),b'another upgraded module')
        self.assertTrue((self.cache/'all-resources.dat').exists())

    def test_tampered_payload_is_not_installed(self):
        self.payload.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'Patch checksum'):self.install()
        self.assertEqual(tool.sha(self.target),self.original)

    def test_rollback_does_not_replace_a_later_upgrade(self):
        receipt=self.install();self.target.write_bytes(b'future upgrade')
        with self.assertRaisesRegex(ValueError,'different upgrade'):tool.rollback(self.home,receipt['backup'])
        self.assertEqual(self.target.read_bytes(),b'future upgrade')

    def test_original_backup_cannot_be_overwritten(self):
        backup=self.home/'local_fixes/color-manager'/self.original[:16]
        backup.mkdir(parents=True);(backup/'original.jar').write_bytes(b'wrong backup')
        with self.assertRaisesRegex(ValueError,'backup checksum'):self.install()
        self.assertEqual((backup/'original.jar').read_bytes(),b'wrong backup')
        self.assertEqual(tool.sha(self.target),self.original)

    def test_candidate_requires_explicit_review_flag(self):
        self.manifest['candidate_only']=True;tool.write_json(self.package/'manifest.json',self.manifest)
        with self.assertRaisesRegex(ValueError,'Unverified-version'):self.install()
        self.assertEqual(tool.sha(self.target),self.original)

    def test_running_application_prevents_replacement(self):
        with mock.object(tool,'assert_closed',side_effect=ValueError('still running')):
            with self.assertRaisesRegex(ValueError,'still running'):self.install()
        self.assertEqual(tool.sha(self.target),self.original)
        self.assertTrue((self.cache/'all-resources.dat').exists())

    def test_unknown_staging_file_is_not_overwritten(self):
        staged=self.target.with_name(self.target.name+'.color-manager-fix.tmp')
        staged.write_bytes(b'previous interrupted operation')
        with self.assertRaisesRegex(ValueError,'Staging file already exists'):self.install()
        self.assertEqual(staged.read_bytes(),b'previous interrupted operation')
        self.assertEqual(tool.sha(self.target),self.original)


if __name__=='__main__':
    unittest.main()