"""Read launcher identity and power state without installing a system service."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess


def process_snapshot(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    try:
        reply = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,lstart=,comm='],
                               capture_output=True, text=True, timeout=2,
                               env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'})
        fields = reply.stdout.strip().split(None, 7)
        if reply.returncode or len(fields) != 8:
            return None
        return {'pid': int(fields[0]), 'parent': int(fields[1]),
                'started': ' '.join(fields[2:7]), 'executable': fields[7]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def launcher_name(executable):
    path = Path(executable)
    if path.name not in {'Tinycast', 'Raycast'} or path.parent.name != 'MacOS':
        return None
    try:
        info = plistlib.loads((path.parent.parent/'Info.plist').read_bytes())
        expected = {'Tinycast': 'com.tinycast.app', 'Raycast': 'com.raycast.macos'}
        return path.name if isinstance(info, dict) and info.get('CFBundleIdentifier') == expected[path.name] else None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def extension_marker(folder):
    path = Path(folder)
    content = (path/'package.json').read_bytes()
    package = json.loads(content)
    if not isinstance(package, dict) or package.get('name') != 'semantic-search':
        raise ValueError('This is not the Semantic Search extension')
    stat = path.stat()
    return f'{stat.st_dev}:{stat.st_ino}:' + hashlib.sha256(content).hexdigest()


def launcher_owner(assets):
    folder = Path(assets).resolve().parent
    marker = extension_marker(folder)
    pid = os.getppid()
    for _ in range(20):
        process = process_snapshot(pid)
        if not process:
            break
        host = launcher_name(process['executable'])
        if host:
            return {key: process[key] for key in ('pid', 'started', 'executable')} | {
                'host': host, 'extension_path': str(folder), 'extension_marker': marker}
        if process['parent'] == pid:
            break
        pid = process['parent']
    raise ValueError('Run this command from Tinycast or Raycast')


def owner_alive(owner):
    try:
        snapshot = process_snapshot(owner['pid'])
        return bool(snapshot and snapshot['started'] == owner['started'] and
                    snapshot['executable'] == owner['executable'] and
                    launcher_name(snapshot['executable']) == owner['host'] and
                    extension_marker(owner['extension_path']) == owner['extension_marker'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def external_power():
    try:
        reply = subprocess.run(['/usr/bin/pmset', '-g', 'batt'], capture_output=True,
                               text=True, timeout=2, env={**os.environ, 'LC_ALL': 'C'})
        if reply.returncode == 0:
            if "'AC Power'" in reply.stdout:
                return True
            if "'Battery Power'" in reply.stdout:
                return False
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(launcher_owner(args.assets)))


if __name__ == '__main__':
    main()
