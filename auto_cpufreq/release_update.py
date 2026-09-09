from pathlib import Path
from re import fullmatch
from shutil import rmtree
from subprocess import DEVNULL, run
from typing import NamedTuple, Optional
from uuid import uuid4


class ReleaseUpdateDecision(NamedTuple):
    available: bool
    tag: Optional[str]
    installed_commit: Optional[str]
    reason: str


def extract_git_commit(version: str) -> Optional[str]:
    _, separator, local = version.partition("+")
    if not separator or not local:
        return None

    candidate = local.split(".", 1)[0]
    if candidate.startswith(("g", "G")):
        candidate = candidate[1:]

    if not fullmatch(r"[0-9a-fA-F]{7,40}", candidate):
        return None

    return candidate.lower()


def _release_base(version: str) -> str:
    base = version.strip().split("+", 1)[0]
    if base.startswith(("v", "V")):
        base = base[1:]
    return base


def version_matches_release(installed_version: str, release_tag: str) -> bool:
    return _release_base(installed_version) == _release_base(release_tag)


def version_matches_commit(installed_version: str, expected_commit: str) -> bool:
    installed_commit = extract_git_commit(installed_version)
    expected_commit = expected_commit.strip().lower()

    if installed_commit is None:
        return False
    if not fullmatch(r"[0-9a-fA-F]{7,40}", expected_commit):
        return False

    return (
        expected_commit.startswith(installed_commit)
        or installed_commit.startswith(expected_commit)
    )


def staged_release_commit(source_dir: Path) -> Optional[str]:
    try:
        result = run(
            ["git", "-C", str(Path(source_dir)), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    commit = result.stdout.strip().lower()
    if not fullmatch(r"[0-9a-f]{40}", commit):
        return None

    return commit


def decide_release_update(
    installed_version: str,
    latest_tag: str,
    compare_status: Optional[str],
) -> ReleaseUpdateDecision:
    installed_commit = extract_git_commit(installed_version)
    if installed_commit is None:
        return ReleaseUpdateDecision(
            False,
            None,
            None,
            "revision-unknown",
        )

    if compare_status == "ahead":
        return ReleaseUpdateDecision(
            True,
            latest_tag,
            installed_commit,
            "release-ahead",
        )

    reasons = {
        "identical": "up-to-date",
        "behind": "installed-ahead",
        "diverged": "diverged",
    }
    return ReleaseUpdateDecision(
        False,
        None,
        installed_commit,
        reasons.get(compare_status, "comparison-unknown"),
    )


def new_staging_destination(parent: Path) -> Path:
    parent = Path(parent)

    while True:
        destination = parent / f"auto-cpufreq-update-{uuid4().hex}"
        if not destination.exists() and not destination.is_symlink():
            return destination


def _remove_destination(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        rmtree(destination)


def _try_remove_destination(destination: Path) -> bool:
    try:
        _remove_destination(destination)
    except OSError:
        return False
    return True


def stage_release(
    repository: str,
    release_tag: str,
    destination: Path,
) -> Optional[Path]:
    destination = Path(destination)

    # Never remove or replace a path that existed before this staging attempt.
    # Callers should allocate a fresh destination for each update.
    if destination.exists() or destination.is_symlink():
        return None

    try:
        result = run(
            [
                "git",
                "clone",
                "--branch",
                release_tag,
                "--single-branch",
                "--depth",
                "1",
                repository,
                str(destination),
            ],
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    except OSError:
        _try_remove_destination(destination)
        return None

    if result.returncode != 0:
        _try_remove_destination(destination)
        return None

    return destination
