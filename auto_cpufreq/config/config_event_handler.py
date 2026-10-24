"""Targeted events for the selected config file (no backup-file reloads)."""
import os
from pyinotify import Event, ProcessEvent


class ConfigEventHandler(ProcessEvent):
    def __init__(self, config) -> None:
        self.config = config

    def _process_update(self, event: Event):
        # rstrip("~") used to reload backups as if they were the real file.
        # Exact, normalized matching also supports --config relative paths.
        if os.path.abspath(event.pathname) == self.config.path:
            self.config.update_config()

    def process_IN_CLOSE_WRITE(self, event: Event) -> None:
        # IN_MODIFY can fire while the application is still writing.
        self._process_update(event)

    def process_IN_DELETE(self, event: Event) -> None:
        self._process_update(event)

    def process_IN_MOVED_FROM(self, event: Event) -> None:
        self._process_update(event)

    def process_IN_MOVED_TO(self, event: Event) -> None:
        self._process_update(event)

    def process_IN_Q_OVERFLOW(self, event: Event) -> None:
        # A lost event stream cannot be trusted to describe current state.
        # Reload directly from disk rather than trusting the event payload.
        self.config.update_config()
