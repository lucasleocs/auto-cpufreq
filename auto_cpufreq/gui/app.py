import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from importlib.metadata import PackageNotFoundError, version as package_version
from os.path import isdir
from subprocess import PIPE, run
from threading import Thread
import time

from auto_cpufreq.gui.diagnostics import diagnostics
from auto_cpufreq.modules.report_refresh import ReportRefreshCoordinator
from auto_cpufreq.modules.report_presentation import (
    present_report, interpret_turbo, interpret_battery,
)

from auto_cpufreq.config.config import config
from auto_cpufreq.core import check_for_update, daemon_is_running
from auto_cpufreq.globals import GITHUB, IS_INSTALLED_WITH_SNAP
from auto_cpufreq.gui.objects import BluetoothBootControl, DaemonNotRunningView, DropDownMenu, MonitorModeView, RadioButtonView, CPUTurboOverride, UpdateDialog
from auto_cpufreq.modules.system_info import (
    battery_power_label,
    format_platform_profile_choices,
    system_info,
)
from auto_cpufreq.power_helper import bluetoothctl_exists

if IS_INSTALLED_WITH_SNAP:
    CSS_FILE = "/snap/auto-cpufreq/current/style.css"
    ICON_FILE = "/snap/auto-cpufreq/current/icon.png"
elif isdir("/opt/auto-cpufreq/current/share"):
    CSS_FILE = "/opt/auto-cpufreq/current/share/scripts/style.css"
    ICON_FILE = "/opt/auto-cpufreq/current/share/images/icon.png"
else:
    CSS_FILE = "/usr/local/share/auto-cpufreq/scripts/style.css"
    ICON_FILE = "/usr/local/share/auto-cpufreq/images/icon.png"

HBOX_PADDING = 20
CONTROL_OPTION_MARGIN = 8
SECTION_SPACING = 12

DEFAULT_REFRESH_INTERVAL = 2
REFRESH_INTERVALS = (1, 2, 5)

# The daemon normally applies overrides on its next ~2 second cycle.
# Keep the affected state hidden until a later, fresh report is collected.
CONTROL_APPLY_DELAY_SECONDS = 3


def _new_section(title):
    frame = Gtk.Frame()
    heading = Gtk.Label(label=title, name="bold")
    heading.set_halign(Gtk.Align.START)
    frame.set_label_widget(heading)

    grid = Gtk.Grid()
    grid.set_column_spacing(18)
    grid.set_row_spacing(4)
    grid.set_margin_top(8)
    grid.set_margin_bottom(8)
    grid.set_margin_start(10)
    grid.set_margin_end(10)
    frame.add(grid)
    return frame, grid


def _add_row(grid, row, name):
    name_label = Gtk.Label(label=name)
    name_label.set_halign(Gtk.Align.START)
    name_label.set_xalign(0)

    value_label = Gtk.Label(label="—")
    value_label.set_halign(Gtk.Align.START)
    value_label.set_xalign(0)
    value_label.set_hexpand(True)

    grid.attach(name_label, 0, row, 1, 1)
    grid.attach(value_label, 1, row, 1, 1)
    return name_label, value_label


def _set_row_visible(grid, row, visible):
    for column in (0, 1):
        child = grid.get_child_at(column, row)
        if child is None:
            continue
        child.set_no_show_all(True)
        if visible:
            child.show()
        else:
            child.hide()


def _battery_status(battery):
    return interpret_battery(battery).status.text


def _governor_status(report):
    return present_report(report).governor.text


def _turbo_status(report):
    return interpret_turbo(report.is_turbo_on).text


def _configuration_label_text():
    prefix = (
        "Using configuration file"
        if config.has_config()
        else "Configuration file"
    )
    return f"{prefix}: {config.path}"


class SystemReportView(Gtk.Box):
    """Shared read-only presentation of SystemReport."""

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
        )

        self.columns = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HBOX_PADDING,
        )
        self.left_column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SECTION_SPACING,
        )
        self.left_column.set_valign(Gtk.Align.START)
        self.columns.pack_start(self.left_column, True, True, 0)

        system_frame, self.system_grid = _new_section("System Information")
        self.system_values = {}
        for row, (key, name) in enumerate(
            (
                ("distro", "Linux distro"),
                ("kernel", "Linux kernel"),
                ("processor", "Processor"),
                ("cores", "Logical CPUs"),
                ("architecture", "Architecture"),
                ("driver", "CPU driver"),
            )
        ):
            _, self.system_values[key] = _add_row(
                self.system_grid, row, name
            )
        self.left_column.pack_start(system_frame, False, False, 0)

        cpu_frame = Gtk.Frame()
        cpu_heading = Gtk.Label(label="CPU Statistics", name="bold")
        cpu_heading.set_halign(Gtk.Align.START)
        cpu_frame.set_label_widget(cpu_heading)

        self.cpu_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        self.cpu_box.set_margin_top(8)
        self.cpu_box.set_margin_bottom(8)
        self.cpu_box.set_margin_start(10)
        self.cpu_box.set_margin_end(10)
        cpu_frame.add(self.cpu_box)

        limits_grid = Gtk.Grid()
        limits_grid.set_column_spacing(18)
        limits_grid.set_row_spacing(4)
        _, self.max_freq_value = _add_row(
            limits_grid, 0, "Maximum frequency"
        )
        _, self.min_freq_value = _add_row(
            limits_grid, 1, "Minimum frequency"
        )
        self.cpu_box.pack_start(limits_grid, False, False, 0)

        self.cores_grid = Gtk.Grid()
        self.cores_grid.set_column_spacing(18)
        self.cores_grid.set_row_spacing(3)
        self.cpu_box.pack_start(self.cores_grid, False, False, 0)

        self.fan_label = Gtk.Label(label="")
        self.fan_label.set_halign(Gtk.Align.START)
        self.fan_label.set_xalign(0)
        self.fan_label.set_no_show_all(True)
        self.cpu_box.pack_start(self.fan_label, False, False, 0)

        self.left_column.pack_start(cpu_frame, False, False, 0)

        self.right_column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=SECTION_SPACING,
        )
        self.right_column.set_valign(Gtk.Align.START)
        self.columns.pack_start(self.right_column, True, True, 0)

        platform_frame, self.platform_grid = _new_section("Platform Profile")
        self.platform_names = {}
        self.platform_values = {}
        for row, (key, name) in enumerate(
            (
                ("interface", "Interface"),
                ("current", "Current profile"),
                ("available", "Available profiles (0)"),
                ("provider", "Provider"),
            )
        ):
            name_label, value_label = _add_row(
                self.platform_grid, row, name
            )
            self.platform_names[key] = name_label
            self.platform_values[key] = value_label

        # Keep long kernel-provided lists readable without increasing the
        # window's minimum width. The complete values stay visible by wrapping.
        for key in ("available", "provider"):
            value_label = self.platform_values[key]
            value_label.set_max_width_chars(44)
            value_label.set_line_wrap(True)
            value_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)

        self.right_column.pack_start(platform_frame, False, False, 0)

        power_frame, self.power_grid = _new_section("CPU Power State")
        self.power_values = {}
        for row, (key, name) in enumerate(
            (
                ("governor", "Governor"),
                ("epp", "EPP"),
                ("epb", "EPB"),
                ("hwp", "HWP Dynamic Boost"),
                ("turbo", "Turbo Boost"),
            )
        ):
            _, self.power_values[key] = _add_row(
                self.power_grid, row, name
            )
        _set_row_visible(self.power_grid, 3, False)
        self.right_column.pack_start(power_frame, False, False, 0)

        battery_frame, self.battery_grid = _new_section("Battery")
        self.battery_names = {}
        self.battery_values = {}
        for row, (key, name) in enumerate(
            (
                ("status", "Status"),
                ("charge", "Charge"),
                ("ac", "AC power"),
                ("start", "Start threshold"),
                ("stop", "Stop threshold"),
                ("power", "Battery power"),
            )
        ):
            name_label, value_label = _add_row(
                self.battery_grid, row, name
            )
            self.battery_names[key] = name_label
            self.battery_values[key] = value_label
        self.right_column.pack_start(battery_frame, False, False, 0)

        stats_frame, stats_grid = _new_section("System Statistics")
        self.stats_values = {}
        for row, (key, name) in enumerate(
            (
                ("usage", "CPU usage"),
                ("load", "System load"),
                ("load_avg", "Load average (1 / 5 / 15 min)"),
                ("temp", "Average CPU temperature"),
            )
        ):
            _, self.stats_values[key] = _add_row(stats_grid, row, name)
        self.left_column.pack_start(stats_frame, False, False, 0)

        self.pack_start(self.columns, True, True, 0)

    def prepend_right(self, widget):
        self.right_column.pack_start(
            widget, False, False, 0
        )
        self.right_column.reorder_child(widget, 0)

    def _refresh_core_rows(self, cores):
        for child in self.cores_grid.get_children():
            self.cores_grid.remove(child)

        for column, text in enumerate(
            ("CPU", "Usage", "Temperature", "Frequency")
        ):
            label = Gtk.Label(label=text, name="bold")
            label.set_halign(Gtk.Align.START)
            label.set_xalign(0)
            self.cores_grid.attach(label, column, 0, 1, 1)

        for row, core in enumerate(cores, start=1):
            values = (
                f"CPU{core.id}",
                f"{core.usage:.1f}%",
                (
                    f"{core.temperature:.0f} °C"
                    if core.temperature > 0
                    else "—"
                ),
                (
                    f"{core.frequency:.0f} MHz"
                    if core.frequency > 0
                    else "—"
                ),
            )
            for column, text in enumerate(values):
                label = Gtk.Label(label=text)
                label.set_halign(Gtk.Align.START)
                label.set_xalign(0)
                if column == 1:
                    # Keep later columns stable when usage reaches 100.0%.
                    label.set_width_chars(len("100.0%"))
                self.cores_grid.attach(label, column, row, 1, 1)

        self.cores_grid.show_all()

    def apply_report(self, report, pending_power_updates=()):
        presentation = present_report(report)
        self.system_values["distro"].set_text(
            f"{report.distro_name} {report.distro_ver}".strip()
        )
        self.system_values["kernel"].set_text(
            report.kernel_version or "Unknown"
        )
        self.system_values["processor"].set_text(
            report.processor_model or "Unknown"
        )
        self.system_values["cores"].set_text(
            str(report.total_core)
            if report.total_core is not None
            else "Unknown"
        )
        self.system_values["architecture"].set_text(
            report.arch or "Unknown"
        )
        self.system_values["driver"].set_text(
            report.cpu_driver or "Unknown"
        )

        platform_state = report.platform_profile
        if platform_state.interface == "none":
            self.platform_values["interface"].set_text("Not detected")
            for row in (1, 2, 3):
                _set_row_visible(self.platform_grid, row, False)
        else:
            interface_text = {
                "modern": "Modern",
                "legacy": "Legacy",
            }.get(platform_state.interface, "Unknown")
            control_text = (
                "Available"
                if platform_state.control_available
                else "Unavailable"
            )
            self.platform_values["interface"].set_text(
                f"{interface_text} · Control: {control_text}"
            )

            _set_row_visible(self.platform_grid, 1, True)
            _set_row_visible(self.platform_grid, 2, True)
            self.platform_values["current"].set_text(
                platform_state.current or "Unknown"
            )

            available_label, available_text = format_platform_profile_choices(
                platform_state
            )
            self.platform_names["available"].set_text(available_label)
            self.platform_values["available"].set_text(available_text)

            provider_states = platform_state.provider_states
            provider_visible = bool(provider_states)
            _set_row_visible(self.platform_grid, 3, provider_visible)
            if provider_visible:
                self.platform_names["provider"].set_text(
                    "Providers" if len(provider_states) > 1 else "Provider"
                )
                if len(provider_states) > 1:
                    provider_text = "; ".join(
                        f"{provider}: {profile or 'Unknown'}"
                        for provider, profile in provider_states
                    )
                else:
                    provider_text = provider_states[0][0]
                self.platform_values["provider"].set_text(provider_text)

        self.max_freq_value.set_text(
            f"{report.cpu_max_freq:.0f} MHz"
            if report.cpu_max_freq is not None
            else "Unavailable"
        )
        self.min_freq_value.set_text(
            f"{report.cpu_min_freq:.0f} MHz"
            if report.cpu_min_freq is not None
            else "Unavailable"
        )
        self._refresh_core_rows(report.cores_info)

        if report.cpu_fan_speed is not None:
            self.fan_label.set_text(
                f"CPU fan speed: {report.cpu_fan_speed} RPM"
            )
            self.fan_label.show()
        else:
            self.fan_label.hide()

        if "governor" not in pending_power_updates:
            self.power_values["governor"].set_text(
                presentation.governor.text
            )
        self.power_values["epp"].set_text(
            presentation.epp.text
        )
        self.power_values["epb"].set_text(
            presentation.epb.text
        )

        hwp_available = report.current_hwp_dynamic_boost is not None
        _set_row_visible(self.power_grid, 3, hwp_available)
        if hwp_available:
            self.power_values["hwp"].set_text(
                presentation.hwp_dynamic_boost.text
            )

        if "turbo" not in pending_power_updates:
            self.power_values["turbo"].set_text(
                presentation.turbo.text
            )

        battery = report.battery_info
        self.battery_values["status"].set_text(
            presentation.battery.status.text
        )
        if battery is None:
            for key in ("charge", "ac", "start", "stop", "power"):
                self.battery_values[key].set_text("Unavailable")
        else:
            self.battery_values["charge"].set_text(
                presentation.battery.level.text
            )
            self.battery_values["ac"].set_text(
                presentation.battery.ac.text
            )
            self.battery_values["start"].set_text(
                presentation.battery.charge_start.text
            )
            self.battery_values["stop"].set_text(
                presentation.battery.charge_stop.text
            )
            self.battery_names["power"].set_text(
                battery_power_label(battery)
            )
            self.battery_values["power"].set_text(
                presentation.battery.power_watts.text
            )

        self.stats_values["usage"].set_text(
            f"{report.cpu_usage:.1f}%"
        )
        self.stats_values["load"].set_text(f"{report.load:.2f}")
        if report.avg_load:
            self.stats_values["load_avg"].set_text(
                f"{report.avg_load[0]:.2f} / "
                f"{report.avg_load[1]:.2f} / "
                f"{report.avg_load[2]:.2f}"
            )
        else:
            self.stats_values["load_avg"].set_text("Unavailable")

        self.stats_values["temp"].set_text(
            presentation.average_temperature.text
        )


class ToolWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="auto-cpufreq")
        self.set_default_size(920, 680)
        self.set_border_width(10)
        self.set_resizable(True)
        self.refresh_in_progress = False
        self.refresh_pending = False
        self.refresh_interval = DEFAULT_REFRESH_INTERVAL
        self.refresh_id = None
        self.pending_power_updates = set()
        self.pending_power_release = set()
        self.power_apply_sources = {}
        self.destroyed = False
        self._update_check_pending = False
        self._refresh_coordinator = ReportRefreshCoordinator(
            self._collect_report,
            self._dispatch_refresh_result,
            thread_name="gtk-dashboard-refresh",
            on_dispatch_error=lambda error: diagnostics.warning(
                "GTK dashboard report delivery failed: %s", error
            ),
        )
        self.connect("destroy", self._cleanup_refresh)
        diagnostics.info("GTK window created")
        self.load_css()
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            filename=ICON_FILE,
            width=500,
            height=500,
            preserve_aspect_ratio=True,
        )
        self.set_icon(pixbuf)
        self.build()

    def main(self):
        self.report_view = SystemReportView()

        controls_frame = Gtk.Frame()
        controls_heading = Gtk.Label(label="Controls", name="bold")
        controls_heading.set_halign(Gtk.Align.START)
        controls_frame.set_label_widget(controls_heading)

        controls_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
        )
        controls_box.set_margin_top(8)
        controls_box.set_margin_bottom(8)
        controls_box.set_margin_start(10)
        controls_box.set_margin_end(10)
        controls_frame.add(controls_box)

        controls_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        controls_actions.set_halign(Gtk.Align.END)
        controls_box.pack_start(controls_actions, False, False, 0)

        self.menu = DropDownMenu(self)
        self._add_refresh_interval_menu()
        controls_actions.pack_end(self.menu, False, False, 0)

        self.governor_control = RadioButtonView()
        self.governor_control.label.set_xalign(0.0)
        self.governor_control.default.set_margin_start(CONTROL_OPTION_MARGIN)
        controls_box.pack_start(self.governor_control, False, False, 0)

        self.control_label_group = Gtk.SizeGroup(
            mode=Gtk.SizeGroupMode.HORIZONTAL
        )
        self.control_label_group.add_widget(self.governor_control.label)
        self.control_option_groups = [
            Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
            for _ in range(3)
        ]
        for group, button in zip(
            self.control_option_groups,
            (
                self.governor_control.default,
                self.governor_control.powersave,
                self.governor_control.performance,
            ),
        ):
            group.add_widget(button)
            button.set_halign(Gtk.Align.START)

        turbo_state, _ = system_info.turbo_on()
        if turbo_state is not None:
            self.turbo_control = CPUTurboOverride()
            self.turbo_control.label.set_xalign(0.0)
            self.turbo_control.auto.set_margin_start(CONTROL_OPTION_MARGIN)
            controls_box.pack_start(self.turbo_control, False, False, 0)
            self.control_label_group.add_widget(self.turbo_control.label)
            for group, button in zip(
                self.control_option_groups,
                (
                    self.turbo_control.auto,
                    self.turbo_control.never,
                    self.turbo_control.always,
                ),
            ):
                group.add_widget(button)
                button.set_halign(Gtk.Align.START)

        if bluetoothctl_exists:
            self.bluetooth_control = BluetoothBootControl(
                show_advanced_button=False
            )
            controls_actions.pack_end(
                self.bluetooth_control.advanced_btn,
                False,
                False,
                0,
            )
            self.bluetooth_control.label.set_xalign(0.0)
            self.bluetooth_control.on_btn.set_margin_start(
                CONTROL_OPTION_MARGIN
            )
            self.control_label_group.add_widget(
                self.bluetooth_control.label
            )
            self.control_option_groups[0].add_widget(
                self.bluetooth_control.on_btn
            )
            self.control_option_groups[1].add_widget(
                self.bluetooth_control.off_btn
            )
            self.bluetooth_control.on_btn.set_halign(Gtk.Align.START)
            self.bluetooth_control.off_btn.set_halign(Gtk.Align.START)
            spacer = Gtk.Label()
            self.control_option_groups[2].add_widget(spacer)
            self.bluetooth_control.inner_box.pack_start(
                spacer, True, True, 0
            )
            controls_box.pack_start(
                self.bluetooth_control, False, False, 0
            )

        self.config_label = Gtk.Label(
            label=_configuration_label_text()
        )
        self.config_label.set_halign(Gtk.Align.START)
        self.config_label.set_xalign(0)
        self.config_label.set_line_wrap(True)
        controls_box.pack_start(
            self.config_label, False, False, 0
        )

        self.report_view.prepend_right(controls_frame)

        # Applying… continues to address the shared
        # observed governor/Turbo rows directly.
        self.power_values = self.report_view.power_values

        self.refresh_error_label = Gtk.Label(label="")
        self.refresh_error_label.set_halign(Gtk.Align.START)
        self.refresh_error_label.set_xalign(0)
        self.refresh_error_label.set_no_show_all(True)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        self.scrolled.set_can_focus(True)
        self.scrolled.add(self.report_view)

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        main_box.pack_start(
            self.refresh_error_label, False, False, 0
        )
        main_box.pack_start(self.scrolled, True, True, 0)
        self.add(main_box)

        self.refresh_in_thread()
        self._schedule_refresh()

    def begin_power_state_apply(self, key):
        if (
            self.destroyed
            or key not in getattr(self, "power_values", {})
        ):
            return False

        source_id = self.power_apply_sources.pop(key, None)
        if source_id is not None:
            GLib.source_remove(source_id)

        # A newer action for this field supersedes any older
        # release that was waiting behind an in-flight refresh.
        self.pending_power_release.discard(key)
        self.pending_power_updates.add(key)
        self.power_values[key].set_text("Applying…")
        return True

    def finish_power_state_apply(self, key, success):
        if (
            self.destroyed
            or key not in self.pending_power_updates
        ):
            return False

        if not success:
            self.pending_power_updates.discard(key)
            self.pending_power_release.discard(key)
            self.request_refresh()
            return True

        source_id = GLib.timeout_add_seconds(
            CONTROL_APPLY_DELAY_SECONDS,
            self._release_power_state_apply,
            key,
        )
        self.power_apply_sources[key] = source_id
        return True

    def _release_power_state_apply(self, key):
        self.power_apply_sources.pop(key, None)

        if (
            self.destroyed
            or key not in self.pending_power_updates
        ):
            return False

        # Never let a report that started before this point replace
        # "Applying…". If one is already running, queue a fresh one.
        if self.refresh_in_progress:
            self.pending_power_release.add(key)
            self.refresh_pending = True
            return False

        self.pending_power_updates.discard(key)
        self.refresh_in_thread()
        return False

    def _add_refresh_interval_menu(self):
        refresh_item = Gtk.MenuItem(
            label="Refresh interval"
        )
        refresh_menu = Gtk.Menu()
        group = None

        for seconds in REFRESH_INTERVALS:
            label = (
                f"{seconds} second"
                if seconds == 1
                else f"{seconds} seconds"
            )

            item = Gtk.RadioMenuItem.new_with_label(
                group,
                label,
            )

            if group is None:
                group = item.get_group()

            item.set_active(
                seconds == self.refresh_interval
            )

            item.connect(
                "toggled",
                self._on_refresh_interval_selected,
                seconds,
            )

            refresh_menu.append(item)

        refresh_item.set_submenu(refresh_menu)

        self.menu.menu.prepend(
            Gtk.SeparatorMenuItem()
        )
        self.menu.menu.prepend(refresh_item)
        self.menu.menu.show_all()

    def _on_refresh_interval_selected(
        self,
        item,
        seconds,
    ):
        if item.get_active():
            self.set_refresh_interval(seconds)

    def set_refresh_interval(self, seconds):
        if seconds not in REFRESH_INTERVALS:
            return

        if seconds == self.refresh_interval:
            return

        self.refresh_interval = seconds
        diagnostics.info("GTK refresh interval set to %ds", seconds)
        self._schedule_refresh()
        self.request_refresh()

    def _schedule_refresh(self):
        if self.refresh_id is not None:
            GLib.source_remove(self.refresh_id)

        self.refresh_id = GLib.timeout_add_seconds(
            self.refresh_interval,
            self.refresh_in_thread,
        )

    def request_refresh(self):
        if self.destroyed:
            return False

        if self.refresh_in_progress:
            self.refresh_pending = True
            diagnostics.debug("GTK refresh requested during active collection")
            return True

        return self.refresh_in_thread()

    def _run_pending_refresh(self):
        if (
            not self.refresh_pending
            or self.destroyed
        ):
            return

        self.refresh_pending = False

        # These fields waited for an older report to finish.
        # The refresh started below is therefore the first report
        # collected after their apply delay elapsed.
        for key in self.pending_power_release:
            self.pending_power_updates.discard(key)
        self.pending_power_release.clear()

        self._refresh_coordinator.request()

    def _cleanup_refresh(self, *_args):
        diagnostics.set_error_callback(None)
        diagnostics.info("GTK window closing")
        self.destroyed = True
        self._refresh_coordinator.close()
        self.refresh_in_progress = False
        self.refresh_pending = False

        if self.refresh_id is not None:
            GLib.source_remove(self.refresh_id)
            self.refresh_id = None

        for source_id in self.power_apply_sources.values():
            GLib.source_remove(source_id)
        self.power_apply_sources.clear()
        self.pending_power_updates.clear()
        self.pending_power_release.clear()

    def show_debug_log_error(self, message):
        if self.destroyed:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CLOSE,
            text="GUI debug logging error",
        )
        dialog.format_secondary_text(message)
        dialog.connect("response", lambda widget, response: widget.destroy())
        dialog.show_all()

    def snap(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        # reference: https://forum.snapcraft.io/t/pkexec-not-found-python-gtk-gnome-app/36579
        label = Gtk.Label(label="GUI not available due to Snap package confinement limitations.\nPlease install auto-cpufreq using auto-cpufreq-installer\nVisit the GitHub repo for more info")
        label.set_justify(Gtk.Justification.CENTER)
        button = Gtk.LinkButton.new_with_label(
            uri=GITHUB,
            label="GitHub Repo"
        )
        
        box.pack_start(label, False, False, 0)
        box.pack_start(button, False, False, 0)
        self.add(box)

    def handle_update(self):
        """Check for updates without blocking the GTK event loop."""
        if self.destroyed or self._update_check_pending:
            return False
        self._update_check_pending = True
        try:
            Thread(
                target=self._check_update_async,
                daemon=True, name="gtk-update-check",
            ).start()
        except RuntimeError:
            self._update_check_pending = False
            raise
        return False

    def _check_update_async(self):
        # No redirect_stdout here: stdout is process-global and redirecting
        # it from a thread could interfere with concurrent debug output.
        try:
            available_tag = check_for_update()
            error = None
        except Exception as exc:
            available_tag, error = None, exc
        try:
            GLib.idle_add(self._finish_update_check, available_tag, error)
        except Exception as exc:
            diagnostics.warning("Cannot deliver GTK update result: %s", exc)

    def _finish_update_check(self, available_tag, error):
        self._update_check_pending = False
        if self.destroyed:
            return False
        if error is not None:
            diagnostics.warning("Update check failed: %s", error)
            return False
        if not available_tag:
            return False

        try:
            installed_version = package_version("auto-cpufreq")
        except (PackageNotFoundError, ValueError):
            installed_version = "unknown"

        dialog = UpdateDialog(
            self,
            f"Current version: {installed_version}",
            f"Latest version: {available_tag}",
        )
        response = dialog.run()
        dialog.destroy()
        if self.destroyed or response != Gtk.ResponseType.YES:
            return False
        self.set_sensitive(False)
        Thread(target=self._run_update, daemon=True).start()
        return False

    def _run_update(self):
        try:
            updater = run(
                ["pkexec", "auto-cpufreq", "--update"],
                input="y\n",
                text=True,
                stderr=PIPE,
            )
            error = None
        except OSError as exc:
            updater = None
            error = exc

        if not self.destroyed:
            GLib.idle_add(
                self._finish_update,
                updater,
                error,
            )

    def _finish_update(self, updater, error):
        self.set_sensitive(True)

        if error is not None:
            message = str(error)
        elif updater is None:
            message = "Update command failed to start"
        elif updater.returncode == 0:
            message = None
        elif updater.returncode == 126:
            message = "Authorization was cancelled"
        elif updater.returncode == 127:
            message = "Authorization failed"
        else:
            message = (updater.stderr or "").strip() or (
                "Update failed with exit status "
                f"{updater.returncode}"
            )

        if message is not None:
            dialog = Gtk.MessageDialog(
                self,
                0,
                Gtk.MessageType.ERROR,
                Gtk.ButtonsType.OK,
                "Error updating",
            )
            dialog.format_secondary_text(message)
            dialog.run()
            dialog.destroy()
            return False

        success = Gtk.MessageDialog(
            self,
            0,
            Gtk.MessageType.INFO,
            Gtk.ButtonsType.OK,
            "Update successful",
        )
        success.format_secondary_text(
            "The app will now close. Please reopen to apply changes"
        )
        success.run()
        success.destroy()
        self.destroy()
        return False

    def daemon_not_running(self):
        self.box = DaemonNotRunningView(self)
        self.add(self.box)

    def monitor_mode(self):
        self.monitor_view = MonitorModeView(
            self,
            SystemReportView(),
            refresh_interval=self.refresh_interval,
        )
        self.add(self.monitor_view)

    def build(self):
        if IS_INSTALLED_WITH_SNAP: self.snap()
        elif daemon_is_running(): self.main()
        else: self.daemon_not_running()

    def load_css(self):
        screen = Gdk.Screen.get_default()
        self.gtk_provider = Gtk.CssProvider()
        self.gtk_context = Gtk.StyleContext()
        self.gtk_context.add_provider_for_screen(screen, self.gtk_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.gtk_provider.load_from_file(Gio.File.new_for_path(CSS_FILE))

    def refresh_in_thread(self):
        if self.destroyed:
            return False
        if self.refresh_in_progress:
            self.refresh_pending = True
            return True
        self.refresh_in_progress = True
        try:
            self._refresh_coordinator.request()
        except RuntimeError:
            self.refresh_in_progress = False
            raise
        return True

    def _collect_report(self):
        return system_info.generate_system_report()

    def _dispatch_refresh_result(self, result):
        # Worker thread: the actual UI update is exclusively on GLib.
        GLib.idle_add(self._deliver_refresh_result, result)

    def _deliver_refresh_result(self, result):
        if (
            self.destroyed
            or not self._refresh_coordinator.is_current(result.generation)
        ):
            return False
        try:
            diagnostics.record_duration(
                "GTK dashboard queue", result.queue_seconds,
                slow_after=max(1.0, self.refresh_interval),
            )
            diagnostics.record_duration(
                "GTK dashboard collection", result.duration,
                slow_after=max(2.0, self.refresh_interval * 2),
            )
            if result.completed_at:
                diagnostics.record_duration(
                    "GTK dashboard delivery latency",
                    max(0.0, time.monotonic() - result.completed_at),
                    slow_after=max(1.0, self.refresh_interval),
                )
            if result.error is not None:
                diagnostics.warning(
                    "GTK dashboard collection failed: %s", result.error
                )
                self._finish_refresh_error(str(result.error))
            else:
                self._apply_refresh(result.report)
        finally:
            try:
                self._run_pending_refresh()
            except Exception:
                diagnostics.exception("GTK pending refresh scheduling failed")
            finally:
                # Always acknowledge the completed generation, even if
                # queuing a successor failed.
                self._refresh_coordinator.acknowledge(result.generation)
                self.refresh_in_progress = self._refresh_coordinator.in_progress
        return False

    def _apply_refresh(self, report):
        if self.destroyed:
            self.refresh_in_progress = False
            return False

        started = time.monotonic()
        try:
            self.report_view.apply_report(
                report,
                self.pending_power_updates,
            )
            self.config_label.set_text(
                _configuration_label_text()
            )
            self.refresh_error_label.hide()
        except Exception as error:
            diagnostics.exception("GTK dashboard render failed")
            self.refresh_error_label.set_text(
                "Unable to refresh system information: "
                f"{error}"
            )
            self.refresh_error_label.show()
        finally:
            diagnostics.record_duration(
                "GTK dashboard render",
                time.monotonic() - started,
                slow_after=1.0,
            )
        return False

    def _finish_refresh_error(self, message):
        if self.destroyed:
            self.refresh_in_progress = False
            return False

        diagnostics.warning("GTK dashboard collection error: %s", message)
        self.refresh_error_label.set_text(
            "Unable to refresh system information: "
            f"{message}"
        )
        self.refresh_error_label.show()
        return False
