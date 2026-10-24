#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from auto_cpufreq.config.config import config, find_config_file
from auto_cpufreq.gui.app import ToolWindow
from auto_cpufreq.gui.diagnostics import diagnostics

def main():
    diagnostics.attach_mainloop(GLib)
    diagnostics.info("GTK application starting")
    try:
        config.set_path(find_config_file(None))
        GLib.set_prgname("auto-cpufreq-gtk")
        win = ToolWindow()
        win.connect("destroy", Gtk.main_quit)
        win.show_all()
        win.handle_update()
        Gtk.main()
    except Exception:
        diagnostics.exception("GTK application failed")
        raise
    finally:
        diagnostics.shutdown()

if __name__ == "__main__": main()
