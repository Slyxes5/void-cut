"""Create only the local encryption key; never print OAuth credentials or tokens."""
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet


def ensure_encryption_key(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing to edit an environment symlink.')
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    expression = r'^[ \t]*SOCIAL_TOKEN_ENCRYPTION_KEY[ \t]*=[ \t]*([^\r\n]*)$'
    existing = re.search(expression,text,re.MULTILINE)
    if existing and existing[1].strip('"\' '):
        Fernet(existing[1].strip('"\' ').encode('ascii'))
        return False
    value = Fernet.generate_key().decode('ascii')
    line = 'SOCIAL_TOKEN_ENCRYPTION_KEY='+value
    if existing:
        text = re.sub(expression,line,text,flags=re.MULTILINE)
    else:
        text = text.rstrip()+'\n\n# Social upload token encryption\n'+line+'\n'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(path.name+'.social.tmp')
    temporary.write_text(text,encoding='utf-8')
    try:
        os.chmod(temporary,0o600)
    except OSError:
        pass
    os.replace(temporary,path)
    return True


if __name__ == '__main__':
    try:
        changed = ensure_encryption_key(Path(__file__).resolve().parent / '.env')
        print('Kunci enkripsi dibuat di backend/.env.' if changed else 'Kunci enkripsi sudah ada; tidak diubah.')
        print('Selanjutnya isi kredensial developer sesuai docs/social-upload-setup.md, lalu restart backend.')
    except (OSError,ValueError,UnicodeError):
        raise SystemExit('Kunci tidak dibuat. Periksa izin atau nilai kunci yang sudah ada di backend/.env.') from None
