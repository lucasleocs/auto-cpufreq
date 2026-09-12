from contextlib import contextmanager
import fcntl
from pathlib import Path


class OperationLockError(RuntimeError):
    pass


@contextmanager
def operation_lock(path: Path, operation: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationLockError(
                f"Another auto-cpufreq {operation} operation is already in progress."
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
