"""Machine-readable systemd queries used by lifecycle operations."""

from subprocess import run


class SystemdQueryError(RuntimeError):
    pass


_UNIT_FILE_STATES = {
    "enabled",
    "enabled-runtime",
    "linked",
    "linked-runtime",
    "alias",
    "masked",
    "masked-runtime",
    "static",
    "indirect",
    "disabled",
    "generated",
    "transient",
    "not-found",
}


def _run_systemctl(args):
    try:
        return run(args, capture_output=True, text=True)
    except OSError as exc:
        raise SystemdQueryError("Unable to execute systemctl.") from exc


def _properties(output: str):
    properties = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def query_unit_file_state(unit: str, *, systemctl: str = "systemctl") -> str:
    """Return systemd's documented unit-file state, including not-found."""
    result = _run_systemctl([systemctl, "is-enabled", unit])
    state = result.stdout.strip()

    # is-enabled intentionally returns non-zero for several valid states. Its
    # documented stdout value, rather than the process status alone, identifies
    # disabled, masked, linked, and missing unit files.
    if "\n" not in state and state in _UNIT_FILE_STATES:
        return state

    raise SystemdQueryError(f"Unable to inspect the unit file state of {unit}.")


def _manager_is_reachable(systemctl: str) -> bool:
    result = _run_systemctl(
        [systemctl, "show", "--no-pager", "--property=Version"]
    )
    properties = _properties(result.stdout)
    return result.returncode == 0 and bool(properties.get("Version"))


def query_unit_properties(
    unit: str,
    names,
    *,
    systemctl: str = "systemctl",
):
    """Return requested properties, or None when the unit is proven absent."""
    names = tuple(names)
    if "LoadState" not in names:
        raise ValueError("LoadState is required to distinguish a missing unit.")

    command = [systemctl, "show", unit, "--no-pager"]
    command.extend(f"--property={name}" for name in names)
    result = _run_systemctl(command)
    properties = _properties(result.stdout)

    # Some systemd versions report a missing unit through properties while
    # still returning non-zero. LoadState is authoritative when it is present.
    if properties.get("LoadState") == "not-found":
        return None

    if result.returncode == 0 and all(name in properties for name in names):
        return {name: properties[name] for name in names}

    # Older systemd versions may omit properties for a missing unit. Accept
    # that compatibility case only after both the unit-file API proves absence
    # and a manager-level query proves that systemctl itself is operational.
    if (
        result.returncode != 0
        and query_unit_file_state(unit, systemctl=systemctl) == "not-found"
        and _manager_is_reachable(systemctl)
    ):
        return None

    raise SystemdQueryError(f"Unable to inspect the runtime state of {unit}.")
