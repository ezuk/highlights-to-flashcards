"""Secrets in the macOS Keychain, through `security`, never on a command line.

Values go in hex over stdin to `security -i`, so they never show in a process list.
`H2F_SECRETS_FILE` swaps the Keychain for a JSON file, for tests.
"""

import json
import re
import subprocess
import threading

from . import paths

SERVICE = paths.APP_NAME
READWISE = "readwise"
ANKIWEB = "ankiweb"
_ACCOUNT = re.compile(r"^[a-z]+$")
_lock = threading.Lock()


class KeychainError(Exception):
    pass


def _check(account):
    if not _ACCOUNT.match(account):
        raise ValueError(f"bad account name {account!r}")


def _security_i(command):
    result = subprocess.run(["security", "-i"], input=command + "\n", capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or "error" in result.stderr.lower():
        raise KeychainError("The Mac's Keychain wouldn't store it.")


def _file_read():
    try:
        return json.loads(paths.secrets_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _file_write(data):
    target = paths.secrets_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")
    target.chmod(0o600)


def get(account):
    """The stored value as a dict, or None."""
    _check(account)
    with _lock:
        if paths.secrets_file():
            return _file_read().get(account)
        result = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                                capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout.strip())
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def put(account, value):
    _check(account)
    data = json.dumps(value, separators=(",", ":"))
    with _lock:
        if paths.secrets_file():
            stored = _file_read()
            stored[account] = value
            _file_write(stored)
            return
        _security_i(f'add-generic-password -U -s "{SERVICE}" -a {account} -X {data.encode("utf-8").hex()}')


def forget(account):
    _check(account)
    with _lock:
        if paths.secrets_file():
            stored = _file_read()
            stored.pop(account, None)
            _file_write(stored)
            return
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                       capture_output=True, timeout=30)
