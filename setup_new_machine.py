# -*- coding: utf-8 -*-
"""Set up a new machine, from the source to the running application.

The repository holds the whole product except the built core, which is
five hundred megabytes and is published on the releases page instead.

This script fetches the core, installs the control plane and the
shell's dependencies, freezes the control plane, packages the shell
and says what it did.

    python setup_new_machine.py

It is safe to run twice: what is already there is left alone.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE_VERSION = '160.0.8177.0'
RELEASE_TAG = 'core-win-x64'
ASSET_NAME = 'nova-core-win-x64.zip'
CONTROL_PLANE = ROOT / 'novaprofile' / 'control_plane'
SHELL = ROOT / 'novaprofile' / 'desktop'
CORE_DIR = ROOT / 'core' / 'build' / 'cores' / CORE_VERSION


def say(message: str) -> None:
    print(f'  {message}')


def step(number: int, title: str) -> None:
    print(f'\n{number}. {title}')


def run(args: list[str], cwd: Path | None = None) -> bool:
    """Run a command and say whether it worked."""

    printable = ' '.join(str(a) for a in args)
    print(f'  $ {printable}')
    try:
        result = subprocess.run(args, cwd=str(cwd) if cwd else None)
    except OSError as error:
        say(f'could not run it: {error}')
        return False
    if result.returncode != 0:
        say(f'it failed with the code {result.returncode}')
        return False
    return True


def asset_id() -> int:
    """The identifier of the core's asset on the release.

    The repository is public, so the release that holds the core is
    found without a token.  A token is used when there is one, because
    a token raises the rate at which the interface answers.
    """

    url = (
        'https://api.github.com/repos/mtykp197-blip/NOVAPROFILE'
        f'/releases/tags/{RELEASE_TAG}'
    )
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'novaprofile-setup',
    }
    key = token()
    if key:
        headers['Authorization'] = f'Bearer {key}'

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.loads(response.read().decode())

    for asset in release.get('assets', []):
        if asset.get('name') == ASSET_NAME:
            return int(asset['id'])

    raise LookupError(f'the release has no asset called {ASSET_NAME}')


def token() -> str:
    """The token that can read the repository, if there is one.

    The repository is public, so the core is fetched without it.  The
    token is still used when it is there: a token raises the rate at
    which the interface answers, and a machine that has one has no
    reason to wait.
    """

    for name in ('GITHUB_TOKEN', 'GH_TOKEN'):
        value = os.environ.get(name)
        if value:
            return value

    try:
        result = subprocess.run(
            ['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n',
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ''

    for line in result.stdout.splitlines():
        if line.startswith('password='):
            return line.split('=', 1)[1].strip()
    return ''


def fetch_core() -> bool:
    """Fetch the built core and unpack it where the shell expects it."""

    step(1, 'the core')

    if (CORE_DIR / 'chrome.dll').is_file():
        say('it is already there')
        return True

    try:
        number = asset_id()
    except Exception as error:  # noqa: BLE001 - the setup reports and stops
        say(f'the release could not be read: {error}')
        return False

    CORE_DIR.mkdir(parents=True, exist_ok=True)
    archive = CORE_DIR.parent / 'nova-core.zip'

    headers = {
        'Accept': 'application/octet-stream',
        'User-Agent': 'novaprofile-setup',
    }
    key = token()
    if key:
        headers['Authorization'] = f'Bearer {key}'

    request = urllib.request.Request(
        f'https://api.github.com/repos/mtykp197-blip/NOVAPROFILE'
        f'/releases/assets/{number}',
        headers=headers,
    )

    say('fetching it -- this is two hundred megabytes')
    try:
        with urllib.request.urlopen(request, timeout=1800) as response, archive.open(
            'wb'
        ) as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
    except urllib.error.HTTPError as error:
        say(f'the fetch failed: {error}')
        return False
    except Exception as error:  # noqa: BLE001 - the setup reports and stops
        say(f'the fetch failed: {error}')
        return False

    say(f'the archive is {archive.stat().st_size // (1024 * 1024)} megabytes')
    say('unpacking it')

    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(CORE_DIR)
    except Exception as error:  # noqa: BLE001 - the setup reports and stops
        say(f'the unpacking failed: {error}')
        return False
    finally:
        archive.unlink(missing_ok=True)

    if not (CORE_DIR / 'chrome.dll').is_file():
        say('the core did not come out whole')
        return False

    say('the core is in place')
    return True


def install_control_plane() -> bool:
    """Install the control plane and its dependencies."""

    step(2, 'the control plane')

    if not run([sys.executable, '-m', 'pip', 'install', '-e', '.'], CONTROL_PLANE):
        say('the control plane could not be installed')
        return False
    return True


def install_shell() -> bool:
    """Install the shell's dependencies."""

    step(3, 'the shell')

    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    if not run([npm, 'install'], SHELL):
        say('the shell dependencies could not be installed')
        return False
    return True


def freeze_control_plane() -> bool:
    """Freeze the control plane into a single executable."""

    step(4, 'the frozen control plane')

    target = CONTROL_PLANE / 'dist' / 'nova-backend.exe'
    if target.is_file():
        say('it is already frozen')
        return True

    if not run(
        [sys.executable, '-m', 'PyInstaller', 'nova-backend.spec', '--noconfirm'],
        CONTROL_PLANE,
    ):
        say('the control plane could not be frozen')
        return False
    return True


def package_shell() -> bool:
    """Package the shell into an installer."""

    step(5, 'the application')

    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    if not run([npm, 'run', 'dist'], SHELL):
        say('the application could not be packaged')
        return False

    say('')
    say('the installer is in novaprofile/desktop/dist/')
    return True


def check() -> None:
    """Say what is in place."""

    step(6, 'what is in place')

    items = [
        ('the core', CORE_DIR / 'chrome.dll'),
        ('the control plane', CONTROL_PLANE / 'dist' / 'nova-backend.exe'),
        ('the shell', SHELL / 'electron' / 'main' / 'index.js'),
        (
            'the dataset',
            ROOT / 'novaprofile' / 'core_patch' / 'data' / 'vn_devices.jsonl',
        ),
    ]
    for name, path in items:
        mark = 'yes' if path.exists() else 'NO'
        say(f'{name:20} {mark}  {path.relative_to(ROOT)}')


def main() -> int:
    print('NOVA PROFILE -- setting up this machine')

    if not fetch_core():
        return 1
    if not install_control_plane():
        return 1
    if not install_shell():
        return 1
    if not freeze_control_plane():
        return 1
    if not package_shell():
        return 1

    check()

    print('\nDone.  The installer is in novaprofile/desktop/dist/.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
