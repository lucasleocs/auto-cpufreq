from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

from typing import Optional, Tuple, Union

from auto_cpufreq.modules.sysfs import (
    ReadResult,
    ReadStatus,
    read_bool01,
    read_int,
    read_text,
)


class ExternalPower(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class BatteryFlow(str, Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    NOT_CHARGING = "not_charging"
    FULL = "full"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PowerSupplySnapshot:
    supply_id: str
    sysfs_path: Path
    type: ReadResult[str]
    scope: ReadResult[str]
    online: ReadResult[bool]
    status: ReadResult[str]
    usb_type: ReadResult[str]
    voltage_now: ReadResult[int]
    current_now: ReadResult[int]
    power_now: ReadResult[int]
    input_current_limit: ReadResult[int]
    input_voltage_limit: ReadResult[int]
    input_power_limit: ReadResult[int]
    capacity: ReadResult[int]
    capacity_level: ReadResult[str]


def _read_online(path: Path) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "0":
        return ReadResult(ReadStatus.AVAILABLE, False)
    if result.value in ("1", "2"):
        return ReadResult(ReadStatus.AVAILABLE, True)
    return ReadResult(ReadStatus.INVALID)


def _active_enum(result: ReadResult[str]) -> ReadResult[str]:
    if result.status is not ReadStatus.AVAILABLE:
        return result
    value = result.value or ""
    if "[" not in value and "]" not in value:
        return result
    if value.count("[") != 1 or value.count("]") != 1:
        return ReadResult(ReadStatus.INVALID)
    start = value.index("[") + 1
    end = value.index("]")
    if end <= start:
        return ReadResult(ReadStatus.INVALID)
    return ReadResult(ReadStatus.AVAILABLE, value[start:end])


def _read_capacity(path: Path) -> ReadResult[int]:
    result = read_int(path)
    if result.status is not ReadStatus.AVAILABLE:
        return result
    if result.value is None or result.value < 0 or result.value > 100:
        return ReadResult(ReadStatus.INVALID)
    return result


def _discover_power_supplies(
    root: Path,
    ignored_supply_substrings: tuple[str, ...] = (),
) -> tuple[ReadStatus, tuple[PowerSupplySnapshot, ...]]:
    root = Path(root)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        return ReadStatus.MISSING, ()
    except OSError:
        return ReadStatus.UNREADABLE, ()

    snapshots = []
    for path in entries:
        if any(token in path.name for token in ignored_supply_substrings):
            continue
        snapshots.append(
            PowerSupplySnapshot(
                supply_id=path.name,
                sysfs_path=path,
                type=read_text(path / "type"),
                scope=read_text(path / "scope"),
                online=_read_online(path / "online"),
                status=read_text(path / "status"),
                usb_type=_active_enum(read_text(path / "usb_type")),
                voltage_now=read_int(path / "voltage_now"),
                current_now=read_int(path / "current_now"),
                power_now=read_int(path / "power_now"),
                input_current_limit=read_int(path / "input_current_limit"),
                input_voltage_limit=read_int(path / "input_voltage_limit"),
                input_power_limit=read_int(path / "input_power_limit"),
                capacity=_read_capacity(path / "capacity"),
                capacity_level=read_text(path / "capacity_level"),
            )
        )
    return ReadStatus.AVAILABLE, tuple(snapshots)


def _is_device_scope(supply: PowerSupplySnapshot) -> bool:
    return (
        supply.scope.status is ReadStatus.AVAILABLE
        and (supply.scope.value or "").strip().casefold() == "device"
    )


def _system_batteries(
    supplies: tuple[PowerSupplySnapshot, ...],
) -> tuple[PowerSupplySnapshot, ...]:
    batteries = []
    for supply in supplies:
        if supply.type.status is not ReadStatus.AVAILABLE:
            continue
        if (supply.type.value or "").strip().casefold() != "battery":
            continue
        if _is_device_scope(supply):
            continue
        batteries.append(supply)
    return tuple(batteries)


def _aggregate_external_power(
    supplies: tuple[PowerSupplySnapshot, ...],
) -> ExternalPower:
    known_source = False
    unresolved = False

    for supply in supplies:
        if _is_device_scope(supply):
            continue

        if supply.type.status is not ReadStatus.AVAILABLE:
            unresolved = True
            continue

        if (supply.type.value or "").strip().casefold() == "battery":
            continue

        known_source = True
        if supply.online.status is not ReadStatus.AVAILABLE:
            unresolved = True
            continue
        if supply.online.value is True:
            return ExternalPower.ONLINE

    if unresolved:
        return ExternalPower.UNKNOWN
    if known_source:
        return ExternalPower.OFFLINE
    return ExternalPower.UNKNOWN


_BATTERY_FLOW_BY_STATUS = {
    "charging": BatteryFlow.CHARGING,
    "discharging": BatteryFlow.DISCHARGING,
    "not charging": BatteryFlow.NOT_CHARGING,
    "full": BatteryFlow.FULL,
}


def _aggregate_battery_flow(
    batteries: tuple[PowerSupplySnapshot, ...],
) -> BatteryFlow:
    if not batteries:
        return BatteryFlow.UNKNOWN

    flows = []
    for battery in batteries:
        if battery.status.status is not ReadStatus.AVAILABLE:
            return BatteryFlow.UNKNOWN
        status = " ".join((battery.status.value or "").split()).casefold()
        flow = _BATTERY_FLOW_BY_STATUS.get(status)
        if flow is None:
            return BatteryFlow.UNKNOWN
        flows.append(flow)

    first = flows[0]
    if all(flow is first for flow in flows[1:]):
        return first
    return BatteryFlow.MIXED


@dataclass(frozen=True)
class TypeCPortSnapshot:
    port_id: str
    sysfs_path: Path
    power_role: ReadResult[str]
    power_operation_mode: ReadResult[str]
    partner_present: ReadResult[bool]
    partner_usb_pd_capable: ReadResult[bool]


def _read_yes_no(path: Path) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "yes":
        return ReadResult(ReadStatus.AVAILABLE, True)
    if result.value == "no":
        return ReadResult(ReadStatus.AVAILABLE, False)
    return ReadResult(ReadStatus.INVALID)


def _read_presence(path: Path) -> ReadResult[bool]:
    try:
        Path(path).stat()
    except FileNotFoundError:
        return ReadResult(ReadStatus.AVAILABLE, False)
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)
    return ReadResult(ReadStatus.AVAILABLE, True)


def _typec_port_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"port([0-9]+)", path.name)
    if match is None:
        return 2**31 - 1, path.name
    return int(match.group(1)), path.name


def _discover_typec_ports(
    root: Path,
) -> tuple[ReadStatus, tuple[TypeCPortSnapshot, ...]]:
    root = Path(root)
    try:
        entries = [
            entry
            for entry in root.iterdir()
            if re.fullmatch(r"port[0-9]+", entry.name)
        ]
    except FileNotFoundError:
        return ReadStatus.MISSING, ()
    except OSError:
        return ReadStatus.UNREADABLE, ()

    snapshots = []
    for port in sorted(entries, key=_typec_port_sort_key):
        partner = root / f"{port.name}-partner"
        partner_present = _read_presence(partner)
        if (
            partner_present.status is ReadStatus.AVAILABLE
            and partner_present.value is True
        ):
            partner_usb_pd_capable = _read_yes_no(
                partner / "supports_usb_power_delivery"
            )
        elif partner_present.status is ReadStatus.AVAILABLE:
            partner_usb_pd_capable = ReadResult(ReadStatus.MISSING)
        else:
            partner_usb_pd_capable = ReadResult(partner_present.status)

        snapshots.append(
            TypeCPortSnapshot(
                port_id=port.name,
                sysfs_path=port,
                power_role=_active_enum(read_text(port / "power_role")),
                power_operation_mode=read_text(port / "power_operation_mode"),
                partner_present=partner_present,
                partner_usb_pd_capable=partner_usb_pd_capable,
            )
        )

    return ReadStatus.AVAILABLE, tuple(snapshots)


class UsbPdCapabilityKind(str, Enum):
    FIXED_SUPPLY = "fixed_supply"
    VARIABLE_SUPPLY = "variable_supply"
    BATTERY = "battery"
    PROGRAMMABLE_SUPPLY = "programmable_supply"
    SPR_ADJUSTABLE_VOLTAGE_SUPPLY = "spr_adjustable_voltage_supply"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UsbPdAttributeSnapshot:
    name: str
    value: ReadResult[Union[int, bool]]
    unit: Optional[str] = None


@dataclass(frozen=True)
class UsbPdCapabilitySnapshot:
    position: Optional[int]
    kind: UsbPdCapabilityKind
    sysfs_name: str
    attributes: Tuple[UsbPdAttributeSnapshot, ...]


@dataclass(frozen=True)
class UsbPdSnapshot:
    pd_id: str
    sysfs_path: Path
    revision: ReadResult[str]
    version: ReadResult[str]
    source_capabilities: Tuple[UsbPdCapabilitySnapshot, ...]
    sink_capabilities: Tuple[UsbPdCapabilitySnapshot, ...]


@dataclass(frozen=True)
class PowerContextSnapshot:
    power_supply_status: ReadStatus
    typec_status: ReadStatus
    usb_pd_status: ReadStatus
    external_power: ExternalPower
    battery_flow: BatteryFlow
    power_supplies: Tuple[PowerSupplySnapshot, ...]
    batteries: Tuple[PowerSupplySnapshot, ...]
    typec_ports: Tuple[TypeCPortSnapshot, ...]
    usb_pd_objects: Tuple[UsbPdSnapshot, ...]


_USB_PD_KIND_BY_NAME = {
    kind.value: kind
    for kind in UsbPdCapabilityKind
    if kind is not UsbPdCapabilityKind.UNKNOWN
}


def _read_suffixed_int(path: Path, suffix: str) -> ReadResult[int]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    value = result.value or ""
    if not value.endswith(suffix):
        return ReadResult(ReadStatus.INVALID)
    number = value[:-len(suffix)]
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(number))
    except ValueError:
        return ReadResult(ReadStatus.INVALID)


def _pd_attr(
    path: Path,
    name: str,
    reader,
    unit: Optional[str] = None,
) -> UsbPdAttributeSnapshot:
    return UsbPdAttributeSnapshot(name, reader(path / name), unit)


def _fixed_schema(path: Path, position: int, source: bool):
    attrs = []
    if position == 1:
        common = (
            "dual_role_power",
            "unconstrained_power",
            "usb_communication_capable",
            "dual_role_data",
            "unchunked_extended_messages_supported",
        )
        for name in common:
            attrs.append(_pd_attr(path, name, read_bool01))
        attrs.append(
            _pd_attr(
                path,
                "usb_suspend_supported" if source else "higher_capability",
                read_bool01,
            )
        )
    attrs.append(_pd_attr(path, "voltage", lambda p: _read_suffixed_int(p, "mV"), "mV"))
    if source:
        attrs.append(_pd_attr(path, "peak_current", read_int))
        attrs.append(_pd_attr(path, "maximum_current", lambda p: _read_suffixed_int(p, "mA"), "mA"))
    else:
        attrs.append(_pd_attr(path, "fast_role_swap_current", read_int))
        attrs.append(_pd_attr(path, "operational_current", lambda p: _read_suffixed_int(p, "mA"), "mA"))
    return tuple(attrs)


def _voltage_range_schema(path: Path, current_name: str):
    return (
        _pd_attr(path, "maximum_voltage", lambda p: _read_suffixed_int(p, "mV"), "mV"),
        _pd_attr(path, "minimum_voltage", lambda p: _read_suffixed_int(p, "mV"), "mV"),
        _pd_attr(path, current_name, lambda p: _read_suffixed_int(p, "mA"), "mA"),
    )


def _battery_schema(path: Path, power_name: str):
    return (
        _pd_attr(path, "maximum_voltage", lambda p: _read_suffixed_int(p, "mV"), "mV"),
        _pd_attr(path, "minimum_voltage", lambda p: _read_suffixed_int(p, "mV"), "mV"),
        _pd_attr(path, power_name, lambda p: _read_suffixed_int(p, "mW"), "mW"),
    )


def _programmable_schema(path: Path, source: bool):
    attrs = list(_voltage_range_schema(path, "maximum_current"))
    if source:
        attrs.append(_pd_attr(path, "pps_power_limited", read_bool01))
    return tuple(attrs)


def _spr_adjustable_schema(path: Path, source: bool):
    attrs = [
        _pd_attr(path, "maximum_current_9V_to_15V", lambda p: _read_suffixed_int(p, "mA"), "mA"),
        _pd_attr(path, "maximum_current_15V_to_20V", lambda p: _read_suffixed_int(p, "mA"), "mA"),
    ]
    if source:
        attrs.append(_pd_attr(path, "peak_current", read_int))
    return tuple(attrs)


def _parse_pd_capability(path: Path, source: bool) -> UsbPdCapabilitySnapshot:
    match = re.fullmatch(r"([0-9]+):(.+)", path.name)
    if match is None:
        return UsbPdCapabilitySnapshot(
            position=None,
            kind=UsbPdCapabilityKind.UNKNOWN,
            sysfs_name=path.name,
            attributes=(),
        )

    position = int(match.group(1))
    kind = _USB_PD_KIND_BY_NAME.get(
        match.group(2),
        UsbPdCapabilityKind.UNKNOWN,
    )
    if kind is UsbPdCapabilityKind.UNKNOWN:
        attrs = ()
    elif kind is UsbPdCapabilityKind.FIXED_SUPPLY:
        attrs = _fixed_schema(path, position, source)
    elif kind is UsbPdCapabilityKind.VARIABLE_SUPPLY:
        attrs = _voltage_range_schema(
            path,
            "maximum_current" if source else "operational_current",
        )
    elif kind is UsbPdCapabilityKind.BATTERY:
        attrs = _battery_schema(
            path,
            "maximum_power" if source else "operational_power",
        )
    elif kind is UsbPdCapabilityKind.PROGRAMMABLE_SUPPLY:
        attrs = _programmable_schema(path, source)
    else:
        attrs = _spr_adjustable_schema(path, source)

    return UsbPdCapabilitySnapshot(
        position=position,
        kind=kind,
        sysfs_name=path.name,
        attributes=attrs,
    )


def _pd_capability_sort_key(path: Path):
    match = re.fullmatch(r"([0-9]+):(.+)", path.name)
    if match is None:
        return 1, 0, path.name
    return 0, int(match.group(1)), path.name


def _discover_pd_capabilities(
    pd_path: Path,
    group: str,
    source: bool,
) -> Tuple[UsbPdCapabilitySnapshot, ...]:
    group_path = pd_path / group
    try:
        entries = sorted(group_path.iterdir(), key=_pd_capability_sort_key)
    except (FileNotFoundError, OSError):
        return ()
    return tuple(_parse_pd_capability(path, source) for path in entries)


def _discover_usb_pd_objects(
    root: Path,
) -> tuple[ReadStatus, Tuple[UsbPdSnapshot, ...]]:
    root = Path(root)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        return ReadStatus.MISSING, ()
    except OSError:
        return ReadStatus.UNREADABLE, ()

    snapshots = []
    for path in entries:
        snapshots.append(
            UsbPdSnapshot(
                pd_id=path.name,
                sysfs_path=path,
                revision=read_text(path / "revision"),
                version=read_text(path / "version"),
                source_capabilities=_discover_pd_capabilities(
                    path,
                    "source-capabilities",
                    True,
                ),
                sink_capabilities=_discover_pd_capabilities(
                    path,
                    "sink-capabilities",
                    False,
                ),
            )
        )
    return ReadStatus.AVAILABLE, tuple(snapshots)


class PowerContextDiscovery:
    def __init__(
        self,
        power_supply_root=Path("/sys/class/power_supply"),
        typec_root=Path("/sys/class/typec"),
        usb_pd_root=Path("/sys/class/usb_power_delivery"),
        ignored_supply_substrings=(),
    ):
        self.power_supply_root = Path(power_supply_root)
        self.typec_root = Path(typec_root)
        self.usb_pd_root = Path(usb_pd_root)
        self.ignored_supply_substrings = tuple(ignored_supply_substrings)

    def discover(self) -> PowerContextSnapshot:
        power_status, supplies = _discover_power_supplies(
            self.power_supply_root,
            self.ignored_supply_substrings,
        )
        typec_status, ports = _discover_typec_ports(self.typec_root)
        usb_pd_status, pd_objects = _discover_usb_pd_objects(self.usb_pd_root)
        batteries = _system_batteries(supplies)
        return PowerContextSnapshot(
            power_supply_status=power_status,
            typec_status=typec_status,
            usb_pd_status=usb_pd_status,
            external_power=_aggregate_external_power(supplies),
            battery_flow=_aggregate_battery_flow(batteries),
            power_supplies=supplies,
            batteries=batteries,
            typec_ports=ports,
            usb_pd_objects=pd_objects,
        )
