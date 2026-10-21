"""Local scoped secret setup; generated fixtures only, no provider/bot calls."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ClipSecretTests(unittest.TestCase):
    def test_setup_creates_and_preserves_one_secret_without_changing_provider_env(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.setup_clip_automation'), 'Scoped local secret setup exists')
        from backend.setup_clip_automation import create_secret
        from backend.clip_credentials import load_automation_token
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            path = Path(root) / 'automation.key'
            self.assertTrue(create_secret(path))
            first = path.read_text(encoding='ascii')
            self.assertRegex(first.strip(), r'^[0-9a-f]{64}$')
            self.assertFalse(create_secret(path))
            self.assertEqual(first, path.read_text(encoding='ascii'))
            with patch('backend.clip_credentials.automation_token_path', return_value=path):
                self.assertEqual(load_automation_token(), first.strip())
                path.write_text('invalid-file-content')
                self.assertEqual(load_automation_token(), '')


if __name__ == '__main__':
    unittest.main()
