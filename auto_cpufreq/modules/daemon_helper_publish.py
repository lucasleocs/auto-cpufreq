"""Conservative publication of root-owned daemon lifecycle helpers.

The filename must be published atomically and an existing target must be
recognized *at the replacement point*, not only during an earlier preflight.
Linux renameat2(RENAME_EXCHANGE) keeps the displaced inode available for an
exact provenance check and permits rollback without deleting its contents.
"""
import ctypes
import hashlib
import os
import stat
import tempfile
from pathlib import Path


_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _root_owned_regular(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0


def _owned_helper(path, template, historical_blob):
    if not _root_owned_regular(path):
        return False
    try:
        data = Path(path).read_bytes()
        current = Path(template).read_bytes()
    except OSError:
        return False
    if data == current:
        return True
    header = ("blob %d\\0" % len(data)).encode("ascii")
    return hashlib.sha1(header + data).hexdigest() == historical_blob


def _exchange(left, right):
    """Exchange two existing directory entries without an overwrite window."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError("Linux renameat2 is required for safe helper replacement")
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD, os.fsencode(left), _AT_FDCWD, os.fsencode(right),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fspath(right))


def publish_daemon_helper(template, target, historical_blob):
    """Install one helper; refuse unrecognized existing paths or failed swaps.

    A newly introduced path is created by a no-clobber hard link. Replacements
    are exchanged atomically, and the displaced file is validated before it
    can be discarded. On a concurrent foreign replacement the old entry is
    exchanged back rather than overwritten or deleted.
    """
    template = Path(template)
    target = Path(target)
    fd, staged_name = tempfile.mkstemp(
        prefix="." + target.name + ".", dir=str(target.parent),
    )
    staged = Path(staged_name)
    cleanup_staged = True
    try:
        with os.fdopen(fd, "wb") as destination:
            with template.open("rb") as source:
                while True:
                    block = source.read(65536)
                    if not block:
                        break
                    destination.write(block)
            destination.flush()
            os.fchmod(destination.fileno(), 0o755)
            os.fsync(destination.fileno())

        if not os.path.lexists(target):
            # No TOCTOU: link(2) refuses to replace an entry created meanwhile.
            os.link(staged, target)
            return

        if not _owned_helper(target, template, historical_blob):
            raise PermissionError("Refusing an unrecognized daemon helper: %s" % target)

        published_inode = os.stat(staged).st_ino
        published_device = os.stat(staged).st_dev
        _exchange(staged, target)
        # The swap can displace a different file than the earlier preflight
        # saw. Never delete that file unless its contents prove ownership.
        if not _owned_helper(staged, template, historical_blob):
            try:
                actual = os.lstat(target)
                if (actual.st_dev, actual.st_ino) != (
                    published_device, published_inode
                ):
                    cleanup_staged = False
                    raise RuntimeError(
                        "Helper changed again during rollback; displaced path "
                        "preserved at %s" % staged
                    )
                _exchange(staged, target)
            except OSError:
                cleanup_staged = False
                raise
            raise PermissionError(
                "Daemon helper changed during publication; original restored"
            )
    finally:
        if cleanup_staged:
            staged.unlink(missing_ok=True)
