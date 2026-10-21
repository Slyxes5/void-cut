"""Shared local machine secret, separate from handoff JSON and provider credentials."""
import os
import re
from pathlib import Path


def app_data_dir():
    base = Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home() / '.local' / 'share'
    return base / 'CheatClipPro'


def automation_token_path():
    return app_data_dir() / 'automation.key'


def load_automation_token():
    try:
        path = automation_token_path()
        if path.is_symlink() or path.stat().st_size > 128:
            return ''
        value = path.read_text(encoding='ascii').strip()
        return value if re.fullmatch(r'[0-9a-f]{64}', value) else ''
    except (OSError, UnicodeError):
        return ''
