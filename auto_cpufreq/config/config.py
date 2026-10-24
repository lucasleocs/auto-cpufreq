"""Configuration selection, safe snapshots, and Linux file-change watches."""
import logging
import os
import pyinotify
import pwd
import sys
from configparser import ConfigParser, Error as ConfigError

from auto_cpufreq.config.config_event_handler import ConfigEventHandler


_LOG = logging.getLogger(__name__)


def find_config_file(args_config_file) -> str:
    """Select explicit, user, then system configuration (unchanged order)."""
    # Match the pre-existing sudo/user precedence without changing daemon
    # policy or implicitly making the GUI user's file authoritative for root.
    username = os.environ.get("SUDO_USER") or os.environ.get("USER")
    try:
        home = (
            pwd.getpwnam(username).pw_dir
            if username else pwd.getpwuid(os.getuid()).pw_dir
        )
    except KeyError:
        home = pwd.getpwuid(os.getuid()).pw_dir
    user_config_dir = os.getenv(
        "XDG_CONFIG_HOME", default=os.path.join(home, ".config")
    )
    user_config_file = os.path.join(
        user_config_dir, "auto-cpufreq/auto-cpufreq.conf"
    )
    system_config_file = "/etc/auto-cpufreq.conf"

    if args_config_file is not None:
        if os.path.isfile(args_config_file):
            return args_config_file
        print(
            f"Config file specified with '--config {args_config_file}' not found."
        )
        sys.exit(1)
    if os.path.isfile(user_config_file):
        return user_config_file
    return system_config_file


class _Config:
    """Publish fully parsed configuration objects, never partial snapshots.

    An intentional deletion resets to defaults, matching the previous
    behavior. Temporary read/parse errors preserve the previous snapshot.
    The notifier worker is created only when a CLI/daemon mode requests it.
    """

    WATCH_MASK = (
        pyinotify.IN_CLOSE_WRITE
        | pyinotify.IN_DELETE
        | pyinotify.IN_MOVED_FROM
        | pyinotify.IN_MOVED_TO
    )

    def __init__(self) -> None:
        self.path: str = ""
        self._config: ConfigParser = ConfigParser()
        self.watch_manager: pyinotify.WatchManager = pyinotify.WatchManager()
        self.config_handler = ConfigEventHandler(self)
        self._notifier = None
        self._watch_dir = None
        self._watch_descriptor = None

    @property
    def notifier(self):
        """Existing .notifier.start() API with no unused polling thread."""
        if self._notifier is None:
            self._notifier = pyinotify.ThreadedNotifier(
                self.watch_manager, self.config_handler
            )
        return self._notifier

    def set_path(self, path: str) -> None:
        selected = os.path.abspath(os.path.expanduser(path))
        directory = os.path.dirname(selected)
        if self._watch_dir != directory:
            # set_path is normally called once, before starting the notifier.
            if self._watch_descriptor is not None:
                try:
                    self.watch_manager.rm_watch(self._watch_descriptor)
                except (OSError, ValueError) as error:
                    _LOG.warning("Cannot release old config watch: %s", error)
            self._watch_dir = None
            self._watch_descriptor = None
            watched = self.watch_manager.add_watch(
                directory, mask=self.WATCH_MASK
            )
            descriptor = watched.get(directory, -1)
            if descriptor is not None and descriptor >= 0:
                self._watch_dir = directory
                self._watch_descriptor = descriptor
            else:
                _LOG.warning(
                    "Configuration directory is not being watched: %s",
                    directory,
                )
        self.path = selected
        self.update_config()

    def has_config(self) -> bool:
        return bool(self.path) and os.path.isfile(self.path)

    def get_config(self) -> ConfigParser:
        # Readers retain their snapshot even if the notifier publishes a new
        # one concurrently; they never observe a parser being filled in place.
        return self._config

    def update_config(self) -> bool:
        if not self.path:
            return False
        replacement = ConfigParser()
        try:
            with open(self.path, "r", encoding="utf-8") as source:
                before = os.fstat(source.fileno())
                replacement.read_file(source)
                after = os.fstat(source.fileno())
            current = os.stat(self.path)
            # Reject a file being edited in place or atomically replaced
            # during a read: a parse without syntax errors is not enough to
            # establish that its bytes came from one complete revision.
            signature = lambda st: (
                st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
            )
            if (
                signature(before) != signature(after)
                or signature(after) != signature(current)
            ):
                _LOG.warning(
                    "Keeping previous configuration: %s changed during read",
                    self.path,
                )
                return False
        except FileNotFoundError:
            # File deleted or moved away: restore default settings, which is
            # what the original IN_DELETE / IN_MOVED_FROM callbacks did.
            if not os.path.lexists(self.path):
                self._config = replacement
                return True
            _LOG.warning(
                "Cannot read configuration %s: missing target", self.path
            )
            return False
        except (OSError, UnicodeError, ConfigError) as error:
            _LOG.warning(
                "Keeping previous configuration after failed reload of %s: %s",
                self.path, error,
            )
            return False
        self._config = replacement
        return True


config = _Config()
