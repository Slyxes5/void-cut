"""Create one scoped local transport secret. Never prints it or changes login/Gemini keys."""
import argparse
import csv
import os
from pathlib import Path
import re
import secrets
import subprocess
from backend.clip_credentials import automation_token_path


def _restrict(path):
    if os.name == 'nt':
        system = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'
        identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
                                  capture_output=True, text=True, check=True)
        sid = next(csv.reader([identity.stdout.strip()]))[-1]
        if not re.fullmatch(r'S-1-[0-9-]+', sid):
            raise RuntimeError('Cannot establish the current Windows identity.')
        result = subprocess.run([str(system / 'icacls.exe'), str(path), '/inheritance:r', '/grant:r',
                                 '*' + sid + ':(F)', '*S-1-5-18:(F)'], capture_output=True)
        if result.returncode:
            raise RuntimeError('Cannot restrict the local automation secret to the current user and SYSTEM.')
    else:
        os.chmod(path, 0o600)


def create_secret(path=None):
    path = Path(path) if path is not None else automation_token_path()
    if path.is_symlink():
        raise ValueError('The automation secret must not be a symbolic link.')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.stat().st_size > 128 or not re.fullmatch(r'[0-9a-f]{64}', path.read_text(encoding='ascii').strip()):
            raise ValueError('Existing automation secret is invalid; no automatic replacement was made.')
        _restrict(path)
        return False
    try:
        # Restrict the empty file before writing any secret value.
        _restrict(path)
        with os.fdopen(fd, 'w', encoding='ascii', newline='\n') as out:
            fd = None
            out.write(secrets.token_hex(32) + '\n')
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        if fd is not None:
            os.close(fd)
        path.unlink(missing_ok=True)
        raise
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        from backend.clip_credentials import load_automation_token
        print('clip_automation_secret_configured:', bool(load_automation_token()))
        return
    created = create_secret()
    print('clip_automation_secret_created:', created)
    print('Provider keys, login settings, Telegram credentials and schedules were not changed.')


if __name__ == '__main__':
    main()
