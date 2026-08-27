from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import re
from shutil import rmtree
from tempfile import mkdtemp
from subprocess import PIPE, TimeoutExpired, run
from typing import Optional, Union

import requests

from auto_cpufreq.globals import GITHUB


RELEASE_TIMEOUT_SECONDS = 10
SOURCE_VERSION_TIMEOUT_SECONDS = 2
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_VERSION_OUTPUT_PATTERN = re.compile(
    r"^auto-cpufreq version:\s*v?(\d+)\.(\d+)\.(\d+)",
    re.MULTILINE,
)
_PYPROJECT_VERSION_PATTERN = re.compile(
    r'^version\s*=\s*"([^"]+)"',
    re.MULTILINE,
)


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    latest_version: str
    update_available: bool


class UpdateError(RuntimeError):
    pass


def _source_tree_version() -> Optional[str]:
    """Return a VCS version when running directly from a source checkout."""
    source_root = Path(__file__).resolve().parents[1]
    pyproject = source_root / "pyproject.toml"
    git_dir = source_root / ".git"
    if not pyproject.is_file() or not git_dir.exists():
        return None

    try:
        match = _PYPROJECT_VERSION_PATTERN.search(pyproject.read_text())
    except OSError:
        return None
    if match is None:
        return None

    base_version = match.group(1)
    try:
        result = run(
            ["git", "-C", str(source_root), "rev-parse", "--short", "HEAD"],
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            timeout=SOURCE_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutExpired):
        return base_version

    commit = (result.stdout or "").strip()
    if result.returncode == 0 and commit:
        return f"{base_version}+{commit}"
    return base_version


def get_literal_version() -> Optional[str]:
    """Return the active package/source version without changing its VCS suffix."""
    source_version = _source_tree_version()
    if source_version is not None:
        return source_version

    try:
        return package_version("auto-cpufreq")
    except PackageNotFoundError:
        return None


def _release_tuple(version: str) -> Optional[tuple[int, int, int]]:
    match = _VERSION_PATTERN.match(version.strip())
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def release_version_matches(output: str, expected_version: str) -> bool:
    """Return whether ``--version`` output reports the expected release."""
    expected = _release_tuple(expected_version)
    if expected is None:
        return False

    match = _VERSION_OUTPUT_PATTERN.search(output or "")
    if match is None:
        return False
    actual = tuple(int(component) for component in match.groups())
    return actual == expected


def get_update_status(timeout: float = RELEASE_TIMEOUT_SECONDS) -> UpdateStatus:
    current_version = get_literal_version()
    if current_version is None:
        raise UpdateError("Unable to determine the installed auto-cpufreq version")

    latest_release_url = (
        GITHUB.replace("github.com", "api.github.com/repos") + "/releases/latest"
    )
    try:
        response = requests.get(latest_release_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise UpdateError(f"Unable to check the latest GitHub release: {error}") from error
    except ValueError as error:
        raise UpdateError("GitHub release response was not valid JSON") from error

    if not isinstance(payload, dict):
        raise UpdateError("GitHub release response was not an object")

    latest_version = payload.get("tag_name")
    if not isinstance(latest_version, str) or not latest_version.strip():
        raise UpdateError("GitHub release response does not contain a valid tag_name")

    current_release = _release_tuple(current_version)
    latest_release = _release_tuple(latest_version)
    if current_release is None or latest_release is None:
        raise UpdateError(
            "Unable to compare auto-cpufreq versions: "
            f"current={current_version!r}, latest={latest_version!r}"
        )

    return UpdateStatus(
        current_version=current_version,
        latest_version=latest_version,
        update_available=latest_release > current_release,
    )


def _command_failure(prefix: str, result) -> UpdateError:
    stderr = (result.stderr or "").strip()
    detail = stderr or f"command exited with status {result.returncode}"
    return UpdateError(f"{prefix}: {detail}")


def prepare_release_source(custom_dir: Union[str, Path], tag: str) -> Path:
    """Clone the exact release tag before making any installed-system changes."""
    root = Path(custom_dir).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        source_dir = Path(mkdtemp(prefix="auto-cpufreq-update-", dir=root))
    except OSError as error:
        raise UpdateError(f"Unable to prepare update workspace {root}: {error}") from error

    try:
        result = run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                tag,
                f"{GITHUB}.git",
                str(source_dir),
            ],
            stdout=PIPE,
            stderr=PIPE,
            text=True,
        )
    except OSError as error:
        rmtree(source_dir, ignore_errors=True)
        raise UpdateError(f"Unable to start git clone: {error}") from error

    if result.returncode != 0:
        rmtree(source_dir, ignore_errors=True)
        raise _command_failure(f"Unable to download release {tag}", result)

    installer = source_dir / "auto-cpufreq-installer"
    if not installer.is_file():
        rmtree(source_dir, ignore_errors=True)
        raise UpdateError(
            f"Downloaded release {tag} does not contain auto-cpufreq-installer"
        )
    return source_dir


def install_release_source(source_dir: Union[str, Path]) -> None:
    """Install a source tree that was successfully prepared beforehand."""
    source_dir = Path(source_dir).resolve()
    installer = source_dir / "auto-cpufreq-installer"
    if not installer.is_file():
        raise UpdateError(f"Installer not found in {source_dir}")

    try:
        result = run(
            [str(installer)],
            cwd=source_dir,
            input="i\n",
            encoding="utf-8",
            stderr=PIPE,
        )
    except OSError as error:
        raise UpdateError(f"Unable to start release installer: {error}") from error

    if result.returncode != 0:
        raise _command_failure("Release installer failed", result)
