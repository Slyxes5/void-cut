r"""Run locally: venv\Scripts\python.exe -m backend.setup_login --origin https://YOUR-PRIVATE-HOST
Passwords/API keys are read with getpass, never command-line arguments or output.
Does not restart services. Existing .env entries are preserved; writes use atomic replacement.
"""
import argparse
import getpass
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from dotenv import dotenv_values
from backend.security import hash_password, valid_password_hash


def configure_login_file(path, password, origins, gemini_key=None):
    encoded = hash_password(password)  # Validate before touching any existing file.
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Allowed remote origins must be HTTPS origins with no path or credentials.')
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig') if path.exists() else '# Private local configuration. Never commit this file.\n'
    changes = {'CHEAT_CLIP_PASSWORD_HASH': encoded}
    if origins:
        existing = dotenv_values(path).get('CHEAT_CLIP_ALLOWED_ORIGINS', '') if path.exists() else ''
        changes['CHEAT_CLIP_ALLOWED_ORIGINS'] = ','.join(dict.fromkeys([s.strip() for s in (existing or '').split(',') if s.strip()] + list(origins)))
    if gemini_key and gemini_key.strip():
        changes['GEMINI_API_KEY'] = gemini_key.strip()
    for key, value in changes.items():
        if any(c in value for c in "'\r\n"):
            raise ValueError('Invalid configuration value.')
        line = f"{key}='{value}'"
        pattern = rf'(?m)^(?:export\s+)?{key}\s*=.*$'
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _: line, text)
        else:
            text = text.rstrip('\r\n') + '\n' + line + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scratch is on the same local volume as the project; os.replace is atomic.
    fd, temporary = tempfile.mkstemp(prefix='clip-login-', suffix='.env', dir=os.environ.get('TMPDIR'))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description='Set the private app password locally; never pass secrets as arguments.')
    parser.add_argument('--origin', action='append', default=[], help='Exact private HTTPS origin (not a secret).')
    parser.add_argument('--check', action='store_true', help='Print configuration booleans only; no secrets.')
    args = parser.parse_args()
    path = Path(__file__).resolve().parent / '.env'
    values = dotenv_values(path) if path.exists() else {}
    if args.check:
        print('backend_env_exists:', path.exists())
        print('gemini_key_configured:', bool((values.get('GEMINI_API_KEY') or '').strip()))
        print('login_configured:', valid_password_hash(values.get('CHEAT_CLIP_PASSWORD_HASH') or ''))
        return
    password = getpass.getpass('New app password (14–256 characters, hidden): ')
    confirmation = getpass.getpass('Confirm app password (hidden): ')
    if password != confirmation:
        raise SystemExit('Passwords did not match; no files changed.')
    gemini_key = None
    if not (values.get('GEMINI_API_KEY') or '').strip():
        gemini_key = getpass.getpass('Gemini API key (hidden; Enter to configure later): ')
    configure_login_file(path, password, args.origin, gemini_key)
    print('Login hash saved. No plaintext password stored. No services restarted.')
    print('Restart the backend only after verifying that no analysis, download or render jobs are active.')


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
