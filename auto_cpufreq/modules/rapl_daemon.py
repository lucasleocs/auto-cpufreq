"""Optional daemon-only Intel RAPL policy with fail-closed lifecycle checks.

Normal governor/EPP/boost management does not depend on this module.
No code path outside the --daemon loop calls tick(). When no explicit
[intel_power] opt-in is configured, the policy never touches Powercap.
"""
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from auto_cpufreq.modules.intel_rapl import (
    IntelRaplController,
    RaplEnvelopeTargets,
    RaplStateError,
)
from auto_cpufreq.modules.rapl_capabilities import inspect_rapl
from auto_cpufreq.modules.rapl_policy import (
    RaplPolicyError,
    parse_rapl_configuration,
    resolve_rapl_selection,
)


SOURCE_ROOT = Path("/opt/auto-cpufreq")
SERVICE_NAME = "auto-cpufreq.service"
INSTALLED_UNIT = Path("/etc/systemd/system") / SERVICE_NAME
PACKAGE_UNIT = SOURCE_ROOT / "current/share/scripts" / SERVICE_NAME
HELPER = SOURCE_ROOT / "current/venv/bin/auto-cpufreq-restore-rapl"
EXPECTED_STOP = "ExecStopPost=-" + str(HELPER)


def _managed_systemd_lifecycle() -> bool:
    """Require a running, root-owned source service with its restore hook.

    Environment variables are not considered sufficient authorization.
    Non-systemd / standalone --daemon / third-party packaging stays inert.
    Verify the actual service's main PID; no manual CLI process can borrow
    an active daemon's recovery readiness.
    """
    if os.geteuid() != 0 or not HELPER.is_file() or not os.access(HELPER, os.X_OK):
        return False
    try:
        installed = INSTALLED_UNIT.read_bytes()
        expected = PACKAGE_UNIT.read_bytes()
        if installed != expected or EXPECTED_STOP.encode() not in expected:
            return False
        result = subprocess.run(
            [
                "systemctl", "show", SERVICE_NAME,
                "--property=MainPID", "--property=DropInPaths",
                "--property=ActiveState",
            ],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode:
        return False
    props = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    return (
        props.get("MainPID") == str(os.getpid())
        and props.get("ActiveState") == "active"
        and props.get("DropInPaths") == ""
    )


@dataclass(frozen=True)
class RaplDaemonEvent:
    state: str
    detail: str


class RaplDaemonPolicy:
    """Daemon-scoped policy; only this object may acquire RAPL state.

    A pre-existing journal at startup is an unresolved predecessor and
    prohibits new writes. Service cleanup can still restore that journal.
    A state error or ambiguous writer result latches RAPL off for this process.
    """

    def __init__(
        self,
        *,
        controller: Optional[IntelRaplController] = None,
        ready: Callable[[], bool] = _managed_systemd_lifecycle,
    ) -> None:
        self.controller = controller or IntelRaplController()
        self.ready = ready
        self._startup_inspected = False
        self._blocked = False
        self._ever_owned = False
        self._scope = None
        self._last_event = None

    def _event(self, state: str, message: str) -> RaplDaemonEvent:
        event = RaplDaemonEvent(state, message)
        if event != self._last_event:
            print(f"Intel RAPL: {state}: {message}", file=sys.stderr)
            self._last_event = event
        return event

    def _disable_on_fault(self, exc: Exception) -> RaplDaemonEvent:
        self._blocked = True
        return self._event("blocked", f"RAPL writes disabled for this daemon: {exc}")

    def _fail_and_reconcile(self, exc: Exception) -> RaplDaemonEvent:
        """Disable new writes and recover all recoverable in-flight entries.

        A constraint can be changed before apply() returns a result, so
        _ever_owned alone is insufficient. The startup journal check gates
        this method from accidentally restoring a predecessor's state.
        """
        details = str(exc)
        if self._startup_inspected:
            try:
                results = self.controller.restore_owned()
                with self.controller.state_store.locked():
                    remaining = self.controller.state_store.load()
                if remaining:
                    details += (
                        f"; {len(remaining)} RAPL recovery entries remain pending"
                    )
                elif results:
                    self._ever_owned = False
                    self._scope = None
            except (OSError, RaplStateError) as recovery_error:
                details += f"; immediate RAPL recovery failed: {recovery_error}"
        return self._disable_on_fault(RaplStateError(details))

    def handle_unexpected_failure(self, exc: Exception) -> RaplDaemonEvent:
        """Used by the --daemon loop before abandoning a failed RAPL cycle."""
        return self._fail_and_reconcile(exc)

    def _check_initial_state(self) -> bool:
        if self._startup_inspected:
            return not self._blocked
        try:
            with self.controller.state_store.locked():
                preexisting = self.controller.state_store.load()
        except (RaplStateError, OSError) as exc:
            self._disable_on_fault(exc)
            return False
        self._startup_inspected = True
        if preexisting:
            self._disable_on_fault(
                RaplStateError("Unresolved RAPL ownership from a previous process")
            )
        return not self._blocked

    def _restore_ours(self) -> RaplDaemonEvent:
        if not self._ever_owned:
            return self._event("disabled", "No daemon-owned constraints to restore")
        try:
            outcomes = self.controller.restore_owned()
            with self.controller.state_store.locked():
                remaining = self.controller.state_store.load()
        except (RaplStateError, OSError) as exc:
            return self._disable_on_fault(exc)
        if remaining:
            return self._disable_on_fault(
                RaplStateError("RAPL restoration left unresolved ownership")
            )
        if not outcomes:
            # We had owned state but there is no longer any journal entry.
            # An empty journal is not proof that restoration occurred.
            return self._disable_on_fault(
                RaplStateError("Owned RAPL journal disappeared without restoration")
            )
        # If the firmware took control, yield instead of reacquiring.
        if any(item.action == "retired-drift" for item in outcomes):
            return self._disable_on_fault(
                RaplStateError("External RAPL policy changed an owned limit")
            )
        self._ever_owned = False
        self._scope = None
        return self._event("restored", "Previous RAPL envelope reconciled")

    def tick(self, conf, profile: str) -> RaplDaemonEvent:
        """Advance optional RAPL policy after the regular CPU policy cycle.

        Only profile='charger'/'battery' is accepted. The caller supplies the
        exact profile selected by set_autofreq(), not a second power probe.
        """
        if profile not in ("charger", "battery"):
            return self._disable_on_fault(
                RaplStateError(f"Unknown CPU power profile: {profile!r}")
            )
        if self._blocked:
            return self._event("blocked", "No further RAPL writes in this daemon")

        try:
            cfg = parse_rapl_configuration(conf, profile)
        except RaplPolicyError as exc:
            if self._ever_owned:
                return self._restore_ours()
            return self._event("invalid-config", str(exc))

        if not cfg.enabled or not cfg.targets.requested_names():
            if self._ever_owned:
                return self._restore_ours()
            return self._event("disabled", "RAPL opt-in inactive for this profile")

        if not self.ready():
            # If the service was valid earlier and we have already changed
            # Powercap, relinquish our proven ownership before disabling
            # RAPL. This is cleanup, never a new limit acquisition.
            if self._ever_owned:
                self._restore_ours()
                return self._disable_on_fault(
                    RaplStateError("Managed service recovery lifecycle changed")
                )
            return self._event("read-only", "Service recovery lifecycle not verified")

        if not self._check_initial_state():
            return self._event("blocked", "Previous RAPL journal needs recovery")

        try:
            capabilities = inspect_rapl(self.controller.discovery.powercap_root)
            selected = resolve_rapl_selection(cfg, capabilities)
            if selected.state != "observed":
                # A partially readable envelope is not safe to apply piecemeal.
                if self._ever_owned:
                    return self._restore_ours()
                return self._event(selected.state, selected.message)

            scope = (selected.control_type, selected.package)
            # One canonical matching physical limit per requested name.
            writer = self.controller.discover_package_constraints()
            for item in selected.observed_limits:
                matches = (
                    ref for ref in writer
                    if ref.control_type == item.control_type
                    and ref.zone_name == item.package
                    and ref.constraint_index == item.constraint_index
                    and ref.constraint_name == item.constraint_name
                )
                if sum(1 for _ in matches) != 1:
                    return self._disable_on_fault(
                        RaplStateError("Writer and read-only Powercap discovery disagree")
                    )

            if self._scope is not None and self._scope != scope:
                previous = self._restore_ours()
                if self._blocked or previous.state != "restored":
                    return previous

            target = RaplEnvelopeTargets(
                long_term_uw=cfg.targets.long_term_uw,
                short_term_uw=cfg.targets.short_term_uw,
            )
            results = self.controller.apply(
                target, control_type=scope[0], package=scope[1],
            )
            # Refuse an envelope if any requested constraint failed.
            # Even an exception mid-apply may have modified PL1 before PL2;
            # read and reconcile the durable ownership journal immediately.
            if any(
                r.action in {"acquired", "updated", "unchanged-owned"}
                for r in results
            ):
                self._ever_owned = True
                self._scope = scope
            quantized = any(
                r.action in {"acquired", "updated"}
                and r.effective_uw != r.requested_uw
                for r in results
            )
            bad_actions = {
                "readback-rejected", "write-failed", "readback-failed",
                "unsafe-readback-unresolved", "restore-failed",
                "pending-unresolved", "unavailable", "skipped-zone-disabled",
                "rejected-min", "rejected-max", "skipped-above-original",
                "skipped-external-limit",
            }
            if quantized or any(r.action in bad_actions for r in results):
                return self._fail_and_reconcile(RaplStateError(
                    "RAPL envelope rejected or only partially verified: "
                    + ", ".join(r.action for r in results)
                ))
            if any(
                r.action in {"drift", "retired-drift", "yielded-external"}
                for r in results
            ):
                return self._fail_and_reconcile(RaplStateError(
                    "Another controller changed the RAPL envelope"
                ))
            if self._ever_owned and any(
                r.action in {"restored", "already-restored"} for r in results
            ):
                # A config reload may withdraw the final owned constraint
                # while still requesting some externally matched (unowned)
                # PL2 value. Reconcile the verified journal after restoration.
                with self.controller.state_store.locked():
                    remaining = self.controller.state_store.load()
                if not remaining:
                    self._ever_owned = False
                    self._scope = None
            return self._event(
                "active" if self._ever_owned else "no-change",
                "Explicit RAPL envelope evaluated: "
                + ", ".join(r.action for r in results),
            )
        except (RaplStateError, RaplPolicyError, OSError) as exc:
            return self._fail_and_reconcile(exc)
