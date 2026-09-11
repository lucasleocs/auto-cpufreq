from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus, read_int, read_text


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
