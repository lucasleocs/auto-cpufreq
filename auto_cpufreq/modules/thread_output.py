"""Keep background Live-mode output quiet without muting the Urwid UI.

Unlike contextlib.redirect_stdout(), this proxy does not suppress writes
from other threads. It is installed only during a Live-mode session.
"""
from contextlib import contextmanager
import threading


class ThreadSelectiveStdout:
    def __init__(self, stream):
        self._stream = stream
        self._local = threading.local()

    @contextmanager
    def silence_current_thread(self):
        previous = getattr(self._local, "silent", False)
        self._local.silent = True
        try:
            yield
        finally:
            self._local.silent = previous

    def write(self, text):
        if getattr(self._local, "silent", False):
            return len(text)
        return self._stream.write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if not getattr(self._local, "silent", False):
            return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)
