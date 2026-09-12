# Debug observability Stage 3 implementation plan

## Baseline

Stage 3 starts from the verified Stage 2 candidate:

`39a4de81baa8a9b2ade09770b5d5515a089f18ee`

Preserve all existing boundaries:

- `SystemReport` remains the fast shared telemetry snapshot;
- `DiagnosticsReport` remains one-shot and read-only;
- GTK and `--stats` do not gain D-Bus polling;
- lifecycle, installer, updater, daemon policy, and live mode stay untouched;
- optional diagnostic failures never prevent the main debug report.

## Scope

Add only selected Power Profiles Daemon D-Bus state that materially helps issue
triage:

- `ActiveProfile`;
- `PerformanceDegraded`;
- `ActiveProfileHolds` with `ApplicationId`, `Profile`, and `Reason`.

Do not add broad PPD introspection, actions, configuration, setters, or profile
mutation.

## D-Bus transport

Use `busctl --system get-property` through an injected subprocess runner.
`busctl get-property` is a stable systemd interface and avoids a new Python
D-Bus dependency. Parse only the documented terse format needed by these
properties.

Try the current PPD namespace first:

- service/interface: `org.freedesktop.UPower.PowerProfiles`;
- object: `/org/freedesktop/UPower/PowerProfiles`.

If the current `ActiveProfile` query fails, fall back to the legacy namespace
for distributions shipping PPD before the 0.20 migration:

- service/interface: `net.hadess.PowerProfiles`;
- object: `/net/hadess/PowerProfiles`.

Once one namespace succeeds, use that namespace for all remaining properties.

## Read-only activation invariant

Do not query PPD D-Bus merely because a unit is installed. A D-Bus property
request can activate a bus-activatable daemon, which would violate the intent of
a purely observational debug command.

Attempt PPD D-Bus collection only when Stage 2 service diagnostics already show
one of these providers as active:

- `power-profiles-daemon`;
- `tuned-ppd`.

Do not query host PPD D-Bus from the Snap path, where host services are not
observable through the existing confinement-aware service collector.

## Data model

Add immutable values conceptually equivalent to:

```python
@dataclass(frozen=True)
class PpdProfileHold:
    application_id: str | None = None
    profile: str | None = None
    reason: str | None = None

@dataclass(frozen=True)
class PpdDiagnostics:
    active_profile: str | None = None
    performance_degraded: str | None = None
    active_profile_holds: tuple[PpdProfileHold, ...] | None = None
```

`active_profile_holds=()` means the property was read successfully and no holds
exist. `None` means the property could not be read or parsed.

Preserve an empty `PerformanceDegraded` string as a successful observation: the
PPD API defines the empty string as "not degraded".

## Task 1: RED transport/parser contracts

Before production changes, add temporary tests covering:

1. parsing a scalar `s "balanced"` value;
2. preserving `s ""` for non-degraded performance;
3. parsing `aa{sv}` profile holds in busctl terse form;
4. empty holds (`aa{sv} 0`) becoming `()`;
5. malformed signatures/counts becoming unavailable rather than raising;
6. current namespace being preferred;
7. legacy namespace being attempted only when the current ActiveProfile query
   fails;
8. later property failure preserving already collected properties.

Observe RED before implementation.

## Task 2: Implement the one-shot PPD collector

Add a collector in `auto_cpufreq/modules/diagnostics.py` that:

- executes only read-only `busctl get-property` commands;
- accepts an injected runner;
- uses `capture_output=True`, `text=True`, `check=False`;
- catches command absence/permission/process errors at the diagnostic boundary;
- returns structured values only;
- performs no printing and no service mutation.

Reach GREEN on the focused parser/collector contract before integration.

## Task 3: RED integration/activation contract

Add tests proving `collect_diagnostics()`:

- calls the PPD collector when either `power-profiles-daemon` or `tuned-ppd` is
  already active;
- does not call it when the provider is merely installed but inactive;
- does not call it on non-systemd/no-provider systems;
- does not call it on the Snap confinement path.

Observe RED, then integrate the smallest provider-active predicate.

## Task 4: RED formatter contract

Before formatting changes, require:

- active profile is shown when available;
- empty degraded reason formats as not degraded;
- non-empty degradation reason is preserved verbatim rather than restricted to
  a hard-coded enum, because upstream may add reasons;
- empty holds format distinctly from unavailable holds;
- each hold shows profile, application ID, and reason without hiding missing
  individual fields;
- no PPD section appears when collection was intentionally skipped because no
  provider was active.

Then implement pure formatting only.

## Task 5: Verification and clean candidate

On the investigation branch run all Stage 3 contracts plus:

```bash
python -m py_compile auto_cpufreq/modules/diagnostics.py \
  auto_cpufreq/bin/auto_cpufreq.py
git diff --check 39a4de81baa8a9b2ade09770b5d5515a089f18ee...HEAD
```

Require Linux Build and Nix Flake to pass on the exact validated SHA.

Then create a clean Stage 3 candidate directly from `39a4de81...` containing
only production changes. Verify candidate blobs against the validated
investigation SHA and require Linux/Nix to pass on the exact candidate SHA
before closing Stage 3.
