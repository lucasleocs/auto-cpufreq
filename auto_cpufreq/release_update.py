# Helpers for stable-release selection and isolated update staging.
#
# Functions in this module deliberately avoid mutating the installed
# auto-cpufreq environment. The lifecycle layer decides when it is safe to
# remove/reinstall the daemon; this module only reasons about revisions and
# manages a private staging workspace that it created itself.

from pathlib import Path
from re import fullmatch
from shutil import rmtree
from subprocess import DEVNULL, run
from tempfile import mkdtemp
from typing import NamedTuple, Optional


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


def version_matches_exact_commit(
    installed_version: str, expected_commit: str
) -> bool:
    installed_commit = extract_git_commit(installed_version)
    expected_commit = expected_commit.strip().lower()

    return (
        installed_commit is not None
        and fullmatch(r"[0-9a-f]{40}", installed_commit) is not None
        and fullmatch(r"[0-9a-f]{40}", expected_commit) is not None
        and installed_commit == expected_commit
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
    parent.mkdir(parents=True, exist_ok=True)
    return Path(
        mkdtemp(prefix="auto-cpufreq-update-", dir=str(parent))
    )


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


def cleanup_staging_workspace(staged_source: Path) -> bool:
    staged_source = Path(staged_source)
    workspace = (
        staged_source.parent
        if staged_source.name == "source"
        else staged_source
    )
    # Never recursively remove an arbitrary caller-supplied path. Only
    # mkdtemp() workspaces with our private prefix are eligible for cleanup.
    if not workspace.name.startswith("auto-cpufreq-update-"):
        return False
    return _try_remove_destination(workspace)


def stage_release(
    repository: str,
    release_tag: str,
    destination: Path,
) -> Optional[Path]:
    workspace = Path(destination)

    # new_staging_destination() creates a private workspace atomically. Clone
    # into a child path so cleanup only ever targets that owned workspace.
    if not workspace.is_dir() or any(workspace.iterdir()):
        return None

    source_dir = workspace / "source"
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
                str(source_dir),
            ],
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    except OSError:
        cleanup_staging_workspace(workspace)
        return None

    if result.returncode != 0:
        cleanup_staging_workspace(workspace)
        return None

    return source_dir
