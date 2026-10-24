"""Testable lifecycle for --live, without power-policy implementation.

The caller provides service actions. Preparing marks each service for
restoration *before* attempting its stop, including partially failed stops.
No restoration occurs while a policy worker might still write CPU state.
"""
import logging
import sys
from threading import Event, Lock, Thread

from auto_cpufreq.modules.thread_output import ThreadSelectiveStdout


_LOG = logging.getLogger(__name__)


class LiveModeSession:
    def __init__(self, policy, service_steps, watch_start, watch_stop):
        self._policy = policy
        self._service_steps = list(service_steps)
        self._watch_start = watch_start
        self._watch_stop = watch_stop
        self._restorations = []
        self._stop = Event()
        self._lock = Lock()
        self._worker = None
        self._watch_started = False
        self._stdout_original = None
        self._stdout_proxy = None
        self._prepared = False
        self._closed = False
        self._policy_failure = None

    @property
    def policy_failure(self):
        with self._lock:
            return self._policy_failure

    @property
    def status(self):
        with self._lock:
            failure = self._policy_failure
            worker = self._worker
            closed = self._closed
        if failure is not None:
            return "failed: %s" % str(failure)[:120]
        if closed:
            return "stopped"
        if self._stop.is_set():
            return "stopping"
        if worker is not None and worker.is_alive():
            return "running"
        if worker is not None:
            return "stopped unexpectedly"
        return "not started"

    def prepare(self):
        if self._prepared or self._closed or self._restorations:
            raise RuntimeError("Live session already prepared")
        for _label, stop_action, restore_action in self._service_steps:
            # Even if stop_action partially succeeds then raises, try to undo.
            self._restorations.append((restore_action, _label))
            stop_action()
        self._prepared = True

    def _run_policy(self):
        with self._stdout_proxy.silence_current_thread():
            try:
                while not self._stop.wait(1):
                    self._policy()
            except Exception as error:
                with self._lock:
                    self._policy_failure = error
                _LOG.exception("Live policy worker terminated unexpectedly")

    def start(self):
        if not self._prepared or self._worker is not None or self._closed:
            raise RuntimeError("Live session must be prepared exactly once")
        # Mark watch startup before trying: even a partly initialized watcher
        # must be asked to stop if its start method raises.
        self._watch_started = True
        self._watch_start()
        self._stdout_original = sys.stdout
        self._stdout_proxy = ThreadSelectiveStdout(self._stdout_original)
        worker = Thread(
            target=self._run_policy, daemon=True,
            name="auto-cpufreq-live-policy",
        )
        self._worker = worker
        sys.stdout = self._stdout_proxy
        try:
            worker.start()
        except BaseException:
            if sys.stdout is self._stdout_proxy:
                sys.stdout = self._stdout_original
            self._worker = None
            raise

    def close(self, timeout=5):
        if self._closed:
            return
        self._stop.set()
        errors = []
        if self._worker is not None and self._worker.ident is not None:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                # The state stays retryable. Do not restore competing power
                # services while set_autofreq could still be running.
                raise RuntimeError(
                    "Live policy worker did not stop; restoration deferred. "
                    "Retry session.close() after the policy worker exits."
                )
        if self._watch_started:
            try:
                self._watch_stop()
            except Exception as error:
                errors.append(("configuration watcher", error))
            else:
                self._watch_started = False
        if self._stdout_proxy is not None and sys.stdout is self._stdout_proxy:
            sys.stdout = self._stdout_original
        # Complete successful steps only; failed steps remain retryable.
        remaining = []
        for action, label in reversed(self._restorations):
            try:
                action()
            except Exception as error:
                errors.append((label, error))
                remaining.append((action, label))
        self._restorations = list(reversed(remaining))
        self._closed = not self._restorations and not self._watch_started
        if errors:
            details = ", ".join("%s: %s" % item for item in errors)
            raise RuntimeError("Live cleanup incomplete: " + details)
