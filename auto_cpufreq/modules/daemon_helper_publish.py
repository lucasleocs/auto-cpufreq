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
    header = ("blob %d\0" % len(data)).encode("ascii")
    known_blobs = (historical_blob,) if isinstance(historical_blob, str) else historical_blob
    return hashlib.sha1(header + data).hexdigest() in known_blobs


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
        if error in (22, 38, 95):  # EINVAL, ENOSYS, EOPNOTSUPP
            raise OSError(
                error,
                "Atomic helper exchange is unsupported on this filesystem; "
                "the existing executable was preserved",
                os.fspath(right),
            )
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

        # A reinstall of an identical official helper does not need a
        # kernel-specific rename exchange. This matters on filesystems
        # where RENAME_EXCHANGE is unsupported.
        if (target.read_bytes() == staged.read_bytes()
                and os.access(target, os.X_OK)):
            return

        _exchange(staged, target)
        # The swap can displace a different file than the earlier preflight
        # saw. Never delete that file unless its contents prove ownership.
        if not _owned_helper(staged, template, historical_blob):
            # A second exchange after checking paths is another race. Keep
            # the displaced inode, including unexpected administrator content.
            # The newly published helper is still at target and the failed
            # deployment must be reported for manual recovery.
            cleanup_staged = False
            raise PermissionError(
                "Unexpected helper displaced during publication; preserved "
                "at %s; installed path is %s" % (staged, target)
            )
    finally:
        if cleanup_staged:
            staged.unlink(missing_ok=True)

def publish_daemon_helpers(specifications):
    """Publish a matched helper set, restoring prior known versions on error.

    Each element is (template, target, historical_blobs). The first phase
    validates *all* existing helpers, before touching either destination.
    Existing versions are hard-linked to private recovery names on the same
    filesystem. A failed later publication attempts a guarded rollback; if
    another actor changed a target, keep its backup and report the location.
    """
    specs = [(Path(src), Path(dst), historical)
             for src, dst, historical in specifications]
    snapshots = []
    attempted = []
    preserve_snapshots = False
    try:
        for template, target, historical in specs:
            if os.path.lexists(target):
                if not _owned_helper(target, template, historical):
                    raise PermissionError(
                        "Refusing an unrecognized daemon helper: %s" % target
                    )
                fd, name = tempfile.mkstemp(
                    prefix="." + target.name + ".recovery.", dir=str(target.parent)
                )
                os.close(fd)
                os.unlink(name)
                # Hard link retains the old inode without removing target.
                os.link(target, name, follow_symlinks=False)
                backup = Path(name)
                snapshots.append((template, target, backup, historical))
                if not _owned_helper(backup, template, historical):
                    preserve_snapshots = True
                    raise PermissionError(
                        "Helper changed while snapshotting; recovery at %s" % backup
                    )
            else:
                snapshots.append((template, target, None, historical))

        for entry in snapshots:
            template, target, _, historical = entry
            attempted.append(entry)
            publish_daemon_helper(template, target, historical)
    except (OSError, RuntimeError) as primary:
        failures = []
        for template, target, backup, historical in reversed(attempted):
            try:
                if backup is None and not os.path.lexists(target):
                    continue
                if not _owned_helper(target, template, historical):
                    raise PermissionError(
                        "Helper changed during rollback: %s" % target
                    )
                if backup is None:
                    # New path created by us. Do not delete an unknown file.
                    if os.path.lexists(target):
                        os.unlink(target)
                elif not os.path.samefile(target, backup):
                    # Restore the official previous inode through the same
                    # guarded atomic publication, not an unchecked overwrite.
                    updated = template.read_bytes()
                    updated_hash = hashlib.sha1(
                        ("blob %d\0" % len(updated)).encode("ascii") + updated
                    ).hexdigest()
                    publish_daemon_helper(backup, target, (updated_hash,))
            except (OSError, RuntimeError) as error:
                failures.append("%s: %s" % (target, error))
        if failures:
            preserve_snapshots = True
            raise RuntimeError(
                "Helper update failed (%s). Recovery snapshots preserved: %s. "
                "Rollback errors: %s" %
                (primary, [str(item[2]) for item in snapshots if item[2]],
                 "; ".join(failures))
            ) from primary
        raise
    finally:
        # Preserve every snapshot if an automatic rollback was incomplete.
        if not preserve_snapshots:
            for _, _, backup, _ in snapshots:
                if backup is not None:
                    try:
                        backup.unlink()
                    except FileNotFoundError:
                        pass
