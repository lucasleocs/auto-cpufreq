"""GTK update check integration without network calls, dialogs or privilege."""
import threading
import time
import unittest
from unittest.mock import patch

from gi.repository import Gtk

from gtk_refresh_checks import APP, pump_until


class FakeWindow:
    handle_update = APP.ToolWindow.handle_update
    _check_update_async = APP.ToolWindow._check_update_async
    _finish_update_check = APP.ToolWindow._finish_update_check

    def __init__(self):
        self.destroyed = False
        self._update_check_pending = False
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = value


class UpdateCheckTests(unittest.TestCase):
    def test_update_check_does_not_block_gui_and_does_not_duplicate(self):
        window = FakeWindow()
        started, release = threading.Event(), threading.Event()
        calls = []
        dialogs = []

        def wait_for_network():
            calls.append(threading.get_ident())
            started.set()
            release.wait(timeout=3)
            return "v9.8.7"

        class FakeDialog:
            def __init__(self, _parent, current, latest):
                dialogs.append((current, latest))

            def run(self):
                return Gtk.ResponseType.NO

            def destroy(self):
                pass

        with patch.object(APP, "check_for_update", side_effect=wait_for_network), \
             patch.object(APP, "UpdateDialog", FakeDialog):
            started_at = time.monotonic()
            self.assertFalse(window.handle_update())
            self.assertLess(time.monotonic() - started_at, 0.5)
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(window._update_check_pending)
            window.handle_update()  # No second network thread.
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0], threading.get_ident())
            release.set()
            self.assertTrue(pump_until(lambda: bool(dialogs)))
            self.assertEqual(dialogs[0][1], "Latest version: v9.8.7")
            self.assertFalse(window._update_check_pending)

    def test_close_during_update_check_never_shows_dialog(self):
        window = FakeWindow()
        dialogs = []
        with patch.object(APP, "check_for_update", return_value="v9.8.7"), \
             patch.object(APP, "UpdateDialog", side_effect=lambda *a: dialogs.append(a)):
            window.handle_update()
            window.destroyed = True
            self.assertTrue(pump_until(lambda: not window._update_check_pending))
            self.assertFalse(dialogs)

    def test_network_exception_is_consumed_on_ui_thread(self):
        window = FakeWindow()
        dialogs = []
        with patch.object(APP, "check_for_update",
                          side_effect=OSError("synthetic timeout")), \
             patch.object(APP, "UpdateDialog", side_effect=lambda *a: dialogs.append(a)):
            window.handle_update()
            self.assertTrue(pump_until(lambda: not window._update_check_pending))
            self.assertFalse(dialogs)
            self.assertFalse(window.destroyed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
