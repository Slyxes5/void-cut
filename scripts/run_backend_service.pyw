"""Hidden, single-process backend launcher used by Windows Task Scheduler."""
import argparse
import importlib.util
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / 'logs' / 'backend-service.log'


def configuration():
    return {
        'project': str(ROOT),
        'python': sys.executable,
        'environment_file_present': (ROOT / 'backend' / '.env').is_file(),
        'dependencies_available': all(importlib.util.find_spec(name) is not None
                                      for name in ('uvicorn', 'fastapi', 'dotenv', 'cryptography')),
        'host': '127.0.0.1',
        'port': 8000,
        'log': str(LOG),
    }


class LogStream(io.TextIOBase):
    """Give pythonw a real stream and route output to a rotating file."""
    def __init__(self, logger):
        self.logger = logger

    @property
    def encoding(self):
        return 'utf-8'

    def isatty(self):
        return False

    def writable(self):
        return True

    def write(self, text):
        for line in str(text).splitlines():
            if line.strip():
                self.logger.info(line)
        return len(text)

    def flush(self):
        for handler in self.logger.handlers:
            handler.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check paths and imports without starting the server.')
    args = parser.parse_args()
    if args.check:
        result = configuration()
        print(json.dumps(result, indent=2))
        return 0 if result['dependencies_available'] and result['environment_file_present'] else 1

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    # Keep this logger independent of Uvicorn's stream handlers to avoid recursion.
    output = logging.getLogger('void-cut.service-output')
    output.handlers = [handler]
    output.setLevel(logging.INFO)
    output.propagate = False
    logging.raiseExceptions = False
    sys.stdout = sys.stderr = LogStream(output)

    try:
        import uvicorn
        uvicorn.run('backend.main:app', host='127.0.0.1', port=8000,
                    workers=1, reload=False, use_colors=False)
    except KeyboardInterrupt:
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception:
        logging.getLogger('void-cut.service-output').exception('Backend could not start.')
        return 1
    finally:
        sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
