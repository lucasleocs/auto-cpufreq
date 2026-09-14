# Source-install ownership detection is kept separate from package-manager
# detection so lifecycle code can decide whether it may safely mutate
# /opt/auto-cpufreq. Package markers alone are insufficient because source and
# packaged installations can coexist on the same host.

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import NamedTuple, Optional
from uuid import uuid4


SOURCE_ROOT = Path("/opt/auto-cpufreq")
SOURCE_RELEASES = SOURCE_ROOT / "releases"
SOURCE_CURRENT = SOURCE_ROOT / "current"
SOURCE_VENV = SOURCE_CURRENT / "venv"
LEGACY_SOURCE_VENV = SOURCE_ROOT / "venv"
SOURCE_COMMAND = Path("/usr/local/bin/auto-cpufreq")
UPDATE_TRANSACTION_NAME = "update-transaction"


class SourceUpdateTransaction(NamedTuple):
    previous_target: str
    candidate_target: str
    daemon_was_installed: bool
    daemon_removed: bool


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _managed_target(target: str, root: Path) -> Path:
    relative = Path(target)
    if relative.is_absolute() or len(relative.parts) != 2:
        raise OSError(f"Current source target is not managed: {target}")
    if relative.parts[0] != "releases" or relative.parts[1] in ("", ".", ".."):
        raise OSError(f"Current source target is not managed: {target}")

    release = root / relative
    if release.is_symlink() or not release.is_dir():
        raise OSError(f"Current source release is unavailable: {release}")
    return release


def current_source_target(*, root: Path = SOURCE_ROOT) -> Optional[str]:
    root = _absolute(Path(root))
    current = root / "current"
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise OSError(f"Current source path is not a symbolic link: {current}")
    target = os.readlink(current)
    _managed_target(target, root)
    return target


def _replace_current(target: str, root: Path) -> None:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temporary_name = f".current.{uuid4().hex}.tmp"
    try:
        os.symlink(target, temporary_name, dir_fd=root_fd)
        os.replace(
            temporary_name,
            "current",
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        # Persist the rename itself, not only the files inside the release.
        os.fsync(root_fd)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.close(root_fd)


def activate_source_release(release: Path, *, root: Path = SOURCE_ROOT) -> Optional[str]:
    root = _absolute(Path(root))
    releases = root / "releases"
    release = _absolute(Path(release))
    current = root / "current"

    if release.parent != releases or release.name in ("", ".", ".."):
        raise OSError(f"Release is outside the managed directory: {release}")
    if release.is_symlink() or not release.is_dir():
        raise OSError(f"Release is not a managed directory: {release}")
    previous = current_source_target(root=root)
    target = str(Path("releases") / release.name)
    _replace_current(target, root)

    return previous


def restore_source_release(
    expected_target: str,
    previous_target: Optional[str],
    *,
    root: Path = SOURCE_ROOT,
) -> None:
    root = _absolute(Path(root))
    if current_source_target(root=root) != expected_target:
        raise OSError("Current source release changed before rollback")

    if previous_target is None:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.unlink("current", dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return

    _managed_target(previous_target, root)
    _replace_current(previous_target, root)


def ensure_legacy_source_current(
    *,
    root: Path = SOURCE_ROOT,
    legacy_share: Optional[Path] = None,
) -> Optional[str]:
    root = _absolute(Path(root))
    existing = current_source_target(root=root)
    if existing is not None:
        return existing

    legacy_venv = root / "venv"
    if legacy_venv.is_symlink() or not legacy_venv.is_dir():
        return None

    releases = root / "releases"
    releases.mkdir(mode=0o755, parents=True, exist_ok=True)
    shim = releases / f"legacy-{uuid4().hex}"
    shim.mkdir(mode=0o755)
    try:
        (shim / "venv").symlink_to(legacy_venv, target_is_directory=True)
        if legacy_share is not None:
            legacy_share = _absolute(Path(legacy_share))
            if legacy_share.is_dir() and not legacy_share.is_symlink():
                (shim / "share").symlink_to(
                    legacy_share,
                    target_is_directory=True,
                )
        activate_source_release(shim, root=root)
    except OSError:
        shutil.rmtree(shim, ignore_errors=True)
        raise

    return str(Path("releases") / shim.name)


def _transaction_link_target(directory: int, name: str, root: Path) -> str:
    raw_target = os.readlink(name, dir_fd=directory)
    parts = Path(raw_target).parts
    if len(parts) != 3 or parts[:2] != ("..", "releases"):
        raise OSError(f"Invalid source update {name} target")
    target = str(Path("releases") / parts[2])
    _managed_target(target, root)
    return target


def _transaction_marker(directory: int, name: str) -> bool:
    try:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise OSError(f"Invalid source update marker: {name}")
    return True


def load_source_update(
    *, root: Path = SOURCE_ROOT
) -> Optional[SourceUpdateTransaction]:
    root = _absolute(Path(root))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        directory = os.open(root / UPDATE_TRANSACTION_NAME, flags)
    except FileNotFoundError:
        return None

    try:
        metadata = os.fstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise OSError("Invalid source update transaction directory")
        return SourceUpdateTransaction(
            previous_target=_transaction_link_target(
                directory, "previous", root
            ),
            candidate_target=_transaction_link_target(
                directory, "candidate", root
            ),
            daemon_was_installed=_transaction_marker(
                directory, "daemon-was-installed"
            ),
            daemon_removed=_transaction_marker(directory, "daemon-removed"),
        )
    finally:
        os.close(directory)


def begin_source_update(
    candidate: Path,
    *,
    daemon_was_installed: bool,
    root: Path = SOURCE_ROOT,
) -> SourceUpdateTransaction:
    root = _absolute(Path(root))
    candidate = _absolute(Path(candidate))
    if candidate.parent != root / "releases":
        raise OSError("Source update candidate is outside the releases directory")
    candidate_target = str(Path("releases") / candidate.name)
    _managed_target(candidate_target, root)
    previous_target = current_source_target(root=root)
    if previous_target is None:
        raise OSError("A current source release is required before update")
    if previous_target == candidate_target:
        raise OSError("The update candidate is already active")

    transaction = root / UPDATE_TRANSACTION_NAME
    if transaction.exists() or transaction.is_symlink():
        raise OSError("A source update transaction is already pending")

    temporary = root / f".{UPDATE_TRANSACTION_NAME}.{uuid4().hex}.tmp"
    temporary.mkdir(mode=0o700)
    try:
        (temporary / "previous").symlink_to(Path("..") / previous_target)
        (temporary / "candidate").symlink_to(Path("..") / candidate_target)
        if daemon_was_installed:
            marker = os.open(
                temporary / "daemon-was-installed",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
            os.close(marker)
        temporary_fd = os.open(
            temporary,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        if transaction.exists() or transaction.is_symlink():
            raise OSError("A source update transaction is already pending")
        os.rename(temporary, transaction)
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)

    loaded = load_source_update(root=root)
    if loaded is None:
        raise OSError("Unable to verify the source update transaction")
    return loaded


def mark_source_update_daemon_removed(*, root: Path = SOURCE_ROOT) -> None:
    root = _absolute(Path(root))
    transaction = load_source_update(root=root)
    if transaction is None:
        raise OSError("No source update transaction is pending")
    directory = os.open(
        root / UPDATE_TRANSACTION_NAME,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    descriptor = None
    try:
        if transaction.daemon_removed:
            return
        descriptor = os.open(
            "daemon-removed",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def is_source_install(
    *,
    prefix: Optional[Path] = None,
    root: Path = SOURCE_ROOT,
) -> bool:
    # sys.prefix identifies the venv that owns this process. Keep recognizing
    # the legacy path until the first versioned installation has committed.
    runtime_prefix = Path(sys.prefix if prefix is None else prefix)
    root = Path(root)
    candidates = (root / "current/venv", root / "venv")
    for candidate in candidates:
        try:
            if runtime_prefix.resolve() == candidate.resolve():
                return True
        except OSError:
            if runtime_prefix == candidate:
                return True
    return False
