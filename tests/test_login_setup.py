"""Tests for local-only login setup; never touch real credentials."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

class SetupTests(unittest.TestCase):
    def test_atomic_setup_preserves_existing_key_and_other_settings(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.setup_login'), 'Local setup helper exists')
        from backend.setup_login import configure_login_file
        from backend.security import verify_password
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            path = Path(root) / '.env'
            path.write_text('GEMINI_API_KEY=test-existing-gemini-key\nOTHER_SETTING=keep\nCHEAT_CLIP_PASSWORD_HASH=old\n')
            configure_login_file(path, 'test-only-strong-password', ['https://private-test.ts.net'])
            values = dotenv_values(path)
            self.assertEqual(values['GEMINI_API_KEY'], 'test-existing-gemini-key')
            self.assertEqual(values['OTHER_SETTING'], 'keep')
            self.assertTrue(verify_password('test-only-strong-password', values['CHEAT_CLIP_PASSWORD_HASH']))
            self.assertEqual(values['CHEAT_CLIP_ALLOWED_ORIGINS'], 'https://private-test.ts.net')
            self.assertNotIn('test-only-strong-password', path.read_text())

    def test_rejects_short_password_before_any_file_changes(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.setup_login'), 'Local setup helper exists')
        from backend.setup_login import configure_login_file
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            path = Path(root) / '.env'
            with self.assertRaises(ValueError):
                configure_login_file(path, 'short', [])
            self.assertFalse(path.exists())

if __name__ == '__main__':
    unittest.main()
