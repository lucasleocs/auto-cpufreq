# Serialize source installation, daemon lifecycle and source-update operations.
#
# The updater intentionally keeps one flock-backed open file description alive
# while it invokes the staged installer and, when needed, the newly installed
# command. Nested processes inherit that same lock descriptor instead of trying
# to acquire an independent lock and being rejected as a competing operation.

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat


OPERATION_LOCK_PATH = Path("/run/lock/auto-cpufreq.lock")
INHERITED_LOCK_FD_ENV = "AUTO_CPUFREQ_OPERATION_LOCK_FD"


class OperationLockError(RuntimeError):
    pass


def _open_lock_handle(path: Path, *, create: bool):
    parent_descriptor = None
    descriptor = None
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        parent = path.parent.resolve(strict=True)
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        parent_metadata = os.fstat(parent_descriptor)
        parent_mode = stat.S_IMODE(parent_metadata.st_mode)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != 0
            or (parent_mode & 0o022 and not parent_metadata.st_mode & stat.S_ISVTX)
        ):
            raise OSError("the lock directory is not a root-owned directory")

        flags = os.O_RDWR | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT
        descriptor = os.open(
            path.name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise OSError("the lock is not a root-owned regular file")
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "a+")
        descriptor = None
        return handle
    except OSError as exc:
        raise OperationLockError(
            f"Unable to open the auto-cpufreq operation lock at {path}."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _inherited_lock_handle(path: Path):
    inherited_fd = os.environ.get(INHERITED_LOCK_FD_ENV)
    if inherited_fd is None:
        return None

    try:
        fd = int(inherited_fd)
        inherited_metadata = os.fstat(fd)
        expected_handle = _open_lock_handle(path, create=False)
        try:
            expected_metadata = os.fstat(expected_handle.fileno())
        finally:
            expected_handle.close()
    except (OSError, ValueError, OperationLockError) as exc:
        raise OperationLockError(
            "The inherited auto-cpufreq operation lock is invalid."
        ) from exc

    if (
        not stat.S_ISREG(inherited_metadata.st_mode)
        or inherited_metadata.st_uid != 0
        or stat.S_IMODE(inherited_metadata.st_mode) != 0o600
        or (inherited_metadata.st_dev, inherited_metadata.st_ino)
        != (expected_metadata.st_dev, expected_metadata.st_ino)
    ):
        raise OperationLockError(
            "The inherited auto-cpufreq operation lock does not match "
            f"{path}."
        )

    # dup() references the same open file description, so this process can own
    # a Python file object without changing the lock lifetime of its parent.
    handle = None
    try:
        handle = os.fdopen(os.dup(fd), "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        if handle is not None:
            handle.close()
        raise OperationLockError(
            "The inherited auto-cpufreq operation lock is not held."
        ) from exc

    return handle


@contextmanager
def operation_lock(
    path: Path = OPERATION_LOCK_PATH,
    operation: str = "operation",
):
    path = Path(path)
    inherited_handle = _inherited_lock_handle(path)
    if inherited_handle is not None:
        try:
            yield inherited_handle
        finally:
            # Closing this duplicate must not explicitly unlock the inherited
            # lock; the shared open file description remains held through the
            # outer lifecycle process's descriptor until that process finishes.
            inherited_handle.close()
        return

    handle = _open_lock_handle(path, create=True)
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OperationLockError(
                f"Another auto-cpufreq {operation} operation is already in progress."
            ) from exc
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
