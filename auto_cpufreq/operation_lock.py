from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


OPERATION_LOCK_PATH = Path("/run/lock/auto-cpufreq.lock")
INHERITED_LOCK_FD_ENV = "AUTO_CPUFREQ_OPERATION_LOCK_FD"


class OperationLockError(RuntimeError):
    pass


def _inherited_lock_handle(path: Path):
    inherited_fd = os.environ.get(INHERITED_LOCK_FD_ENV)
    if inherited_fd is None:
        return None

    try:
        fd = int(inherited_fd)
        target = Path(f"/proc/self/fd/{fd}").resolve()
        expected = path.resolve()
    except (OSError, ValueError) as exc:
        raise OperationLockError(
            "The inherited auto-cpufreq operation lock is invalid."
        ) from exc

    if target != expected:
        raise OperationLockError(
            "The inherited auto-cpufreq operation lock does not match "
            f"{expected}."
        )

    try:
        handle = os.fdopen(os.dup(fd), "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
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
            inherited_handle.close()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        path.chmod(0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationLockError(
                f"Another auto-cpufreq {operation} operation is already in progress."
            ) from exc
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
