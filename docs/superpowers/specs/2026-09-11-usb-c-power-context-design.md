# USB-C and USB-PD Power Context Architecture

**Status:** Design specification  
**Date:** 2026-09-11  
**Repository:** `AdnanHodzic/auto-cpufreq`  
**Design branch:** `usb-c-power-context-design`  
**Development base:** `modern-intel-rapl-envelopes` at `d47b783d91ab616a863027069e1511e5d557bf22`

## 1. Summary

This document defines the first implementation of richer external-power awareness in `auto-cpufreq`, with specific support for Linux `power_supply`, USB Type-C, and USB Power Delivery (USB-PD) interfaces.

The first delivery is intentionally **read-only and observational**. It introduces a normalized `PowerContextSnapshot` that can report what the kernel exposes about external power, system batteries, Type-C ports, connected partners, and USB-PD capabilities. It does **not** use that information to change CPU policy, EPP, turbo, HWP Dynamic Boost, RAPL limits, daemon scheduling, or any other hardware setting.

The design follows five central rules:

1. **Observe facts, do not infer charger quality.** `external power online + battery discharging` is a valid observation and is not automatically classified as a weak or insufficient charger.
2. **Keep current state separate from advertised capabilities.** A source advertising a 65 W PDO does not prove that the machine is currently receiving 65 W.
3. **Preserve multiple objects.** Multiple batteries, chargers, Type-C ports, and USB-PD objects are represented independently instead of being collapsed into one guessed active charger.
4. **Do not invent cross-subsystem identity.** `power_supply`, Type-C, and USB-PD objects are not correlated by names, indexes, or heuristics unless the kernel exposes an explicit, unambiguous relationship.
5. **Unknown remains unknown.** Missing, unreadable, malformed, or unsupported data is represented explicitly instead of being filled by heuristics.

This delivery establishes a trustworthy observation layer that can later be used by a separate policy design if and only if real hardware validation demonstrates that the data is sufficiently portable and semantically reliable.

## 2. Context

The existing Modern Intel work introduced a binary policy-facing `PowerSource` model:

```text
CHARGER
BATTERY
```

That binary model is sufficient for current policy selection, but it cannot represent increasingly common laptop power states such as:

- several power-supply objects present at once;
- a USB-C source being online while the battery is still discharging;
- a Type-C connection operating in USB-PD mode;
- source and sink PDO capabilities exposed by the kernel;
- multiple system batteries with different charge states;
- a kernel exposing Type-C information but not the USB-PD class ABI;
- a platform where USB-PD details are only partially exposed.

The existing Modern Intel design explicitly deferred richer USB-C / USB-PD source awareness. This specification implements only the observation layer of that extension.

## 3. Goals

### 3.1 Add a richer, factual power-context snapshot

The implementation must be able to represent:

- aggregate external-power presence as `ONLINE`, `OFFLINE`, or `UNKNOWN`;
- aggregate battery flow as `CHARGING`, `DISCHARGING`, `NOT_CHARGING`, `FULL`, `MIXED`, or `UNKNOWN`;
- all relevant `power_supply` objects individually;
- all system batteries individually;
- Type-C ports and their current role/operation metadata where exposed;
- connected Type-C partner presence and USB-PD capability where explicitly exposed;
- USB-PD source/sink capabilities where the USB-PD class ABI is available;
- subsystem availability independently from collection contents.

### 3.2 Preserve kernel semantics

The implementation must keep distinct concepts distinct.

Examples:

- `input_power_limit` remains an input power limit and is not renamed to `charger_power`;
- an advertised PDO remains a capability and is not treated as the current contract;
- `voltage_now` and `current_now` are not multiplied to manufacture a charger wattage unless a later design proves that such a calculation is semantically valid for a specific use;
- `USB Power Delivery` as a Type-C power operation mode means that PD negotiation mechanisms are in use, not that the current negotiated wattage is known.

### 3.3 Improve diagnostics without changing control behavior

The new snapshot is used only for reporting in this delivery:

- `--debug`: detailed power-context diagnostics;
- `--stats`: compact factual summary;
- `--monitor`: unchanged;
- `--live`: unchanged.

### 3.4 Keep discovery portable and testable

All sysfs roots must be injectable. Production defaults point at the normal host sysfs paths, while Snap/hostfs resolution is performed by the integration layer rather than inside each reader.

### 3.5 Reuse a common sysfs-read result model

The existing Intel discovery semantics are generalized into a small shared module so both Intel and power-context discovery can distinguish:

- `AVAILABLE`;
- `MISSING`;
- `UNREADABLE`;
- `INVALID`.

The Intel refactor must be behavior-neutral.

## 4. Non-Goals

The following are explicitly outside this first delivery:

- classifying a charger as sufficient, insufficient, weak, strong, fast, or slow;
- electing one active charger from multiple sources;
- creating a synthetic `charger_watts` field;
- changing EPP based on USB-PD or charger capability;
- changing governor based on USB-PD or charger capability;
- changing Turbo Boost permission based on USB-PD or charger capability;
- changing HWP Dynamic Boost based on USB-PD or charger capability;
- changing RAPL PL1/PL2 based on USB-PD or charger capability;
- battery-assist heuristics;
- battery percentage tiers for policy;
- special dock policy;
- cross-subsystem matching by object name, numeric suffix, guessed physical port, or vendor convention;
- extending the daemon event loop for Type-C or USB-PD events;
- adding a new policy configuration section for this feature;
- vendor or model allowlists;
- D-Bus profile leases;
- thermal/acoustic adaptation;
- introducing a permanent upstream test framework solely for this feature.

## 5. Design Principles

### 5.1 Observation and policy are separate layers

The first delivery has this data flow:

```text
kernel sysfs
    -> PowerContextDiscovery
    -> PowerContextSnapshot
    -> reporting
```

It deliberately does not have:

```text
PowerContextSnapshot
    -> policy
```

The current `PowerSource.CHARGER/BATTERY` policy model remains unchanged in this delivery.

### 5.2 Facts must not be upgraded into interpretations

For example:

```text
external_power = ONLINE
battery_flow = DISCHARGING
```

must remain exactly that. The implementation must not silently add:

```text
charger_insufficient = true
```

Possible causes of external power plus battery discharge include source limitations, battery assist, charge thresholds, thermal behavior, firmware behavior, transient conditions, or platform-specific power management. The first delivery does not choose between them.

### 5.3 Multiple sources are first-class

The Linux power-supply model allows multiple chargers and power sources. The implementation therefore keeps each observed object rather than selecting one preferred source.

### 5.4 Current state and advertised capability are separate

Type-C connection state, `power_supply` measurements/limits, and USB-PD advertised PDOs are stored independently.

The following inference is forbidden:

```text
20 V x 3.25 A source PDO
    -> machine is currently receiving 65 W
```

### 5.5 Cross-subsystem correlation requires explicit evidence

These identifiers belong to different kernel subsystems:

```text
power_supply: ucsi-source-psy-USBC000
Type-C:       port0
USB-PD:       pd0
```

They must not be assumed to represent the same physical source based on names or numbering.

Relationships defined inside one subsystem may be used, such as a Type-C partner belonging to a Type-C port. Cross-subsystem identity is deferred unless a stable kernel relationship is explicitly exposed and separately designed.

### 5.6 Unknown remains unknown

Absence of evidence is not converted into a guessed value.

Examples:

- no external-source object does not mean `OFFLINE`;
- no USB-PD class directory does not mean the machine cannot use USB-PD;
- a missing source PDO does not imply a default USB current;
- unreadable battery status does not become `NOT_CHARGING`;
- no battery does not automatically make the new snapshot report `ONLINE`.

The existing historical desktop fallback remains outside the new factual snapshot.

### 5.7 Failure is localized

An error reading one attribute must not invalidate an unrelated object or the entire snapshot.

The smallest meaningful unit carries the error.

### 5.8 Read-only means read-only

The new module must not open any new sysfs attribute for writing. It must not modify policy, charging, Type-C roles, USB-PD settings, or other hardware state.

## 6. Shared Sysfs Read Model

A new focused module, provisionally:

```text
auto_cpufreq/modules/sysfs.py
```

provides shared read primitives.

At minimum:

```text
ReadStatus
  AVAILABLE
  MISSING
  UNREADABLE
  INVALID

ReadResult[T]

read_text(...)
read_int(...)
read_bool01(...)
```

Only primitives actually useful to both the Intel and power-context discovery layers should be moved here. This must not become a generic hardware framework.

### 6.1 Behavior-neutral Intel refactor

The first implementation commit moves the existing generic read semantics from `intel_power.py` into `sysfs.py` and updates `intel_power.py` to import them.

The refactor must preserve:

- the exact `ReadStatus` semantics;
- Intel discovery behavior;
- Intel reporting behavior;
- handling of missing, unreadable, invalid, and available attributes.

No Intel policy or telemetry behavior change belongs in this commit.

## 7. Power Context Architecture

The new module is provisionally:

```text
auto_cpufreq/modules/power_context.py
```

Its only responsibilities are:

1. enumerate the relevant sysfs objects;
2. read supported attributes;
3. normalize values into immutable snapshots;
4. calculate the narrowly defined aggregate states;
5. return one point-in-time `PowerContextSnapshot`.

It does not format user output and does not make policy decisions.

### 7.1 Discovery entry point

Conceptually:

```text
PowerContextDiscovery(
    power_supply_root=Path("/sys/class/power_supply"),
    typec_root=Path("/sys/class/typec"),
    usb_pd_root=Path("/sys/class/usb_power_delivery"),
)

discover() -> PowerContextSnapshot
```

The exact class/function names may be refined during implementation, but the boundary must remain.

### 7.2 Injectable roots

The readers must not contain scattered Snap checks or hardcoded hostfs prefixes.

Normal execution uses standard `/sys/class/...` roots.

When hostfs is required, the integration layer passes equivalent roots such as:

```text
/var/lib/snapd/hostfs/sys/class/power_supply
/var/lib/snapd/hostfs/sys/class/typec
/var/lib/snapd/hostfs/sys/class/usb_power_delivery
```

This separation means:

```text
discovery knows how to read sysfs
integration knows where host sysfs lives
```

## 8. Top-Level Data Model

Conceptually:

```text
PowerContextSnapshot
  power_supply_status: ReadStatus
  typec_status: ReadStatus
  usb_pd_status: ReadStatus

  external_power: ExternalPower
  battery_flow: BatteryFlow

  power_supplies: tuple[PowerSupplySnapshot, ...]
  batteries: tuple[PowerSupplySnapshot, ...]
  typec_ports: tuple[TypeCPortSnapshot, ...]
  usb_pd_objects: tuple[UsbPdSnapshot, ...]
```

The snapshot is immutable.

### 8.1 Subsystem availability

Subsystem root state is distinct from object count.

Rules:

- root missing -> `MISSING`;
- root exists but cannot be enumerated -> `UNREADABLE`;
- root exists and is readable but has no objects -> `AVAILABLE` plus an empty collection;
- malformed individual objects/attributes do not automatically make the root unavailable.

This distinction allows diagnostics such as:

```text
Power supply: available
Type-C: available, 1 port
USB Power Delivery: missing
```

## 9. Aggregate External-Power State

The aggregate enum is:

```text
ExternalPower
  ONLINE
  OFFLINE
  UNKNOWN
```

There is no `MIXED` state for external-power presence.

### 9.1 Relevant sources

Only system-relevant external power-supply objects participate in the aggregate. Batteries do not. Objects explicitly scoped to a peripheral/device rather than the system do not determine system external-power state.

### 9.2 Aggregation rules

- If any relevant source is positively observed online, result is `ONLINE`.
- If relevant sources exist, every relevant source has a valid observed offline state, and there is no unresolved relevant state, result is `OFFLINE`.
- If evidence is insufficient or materially ambiguous, result is `UNKNOWN`.

Examples:

```text
ADP1: offline
ucsi-source-psy-USBC000: online
=> ONLINE
```

```text
ADP1: offline
ucsi-source-psy-USBC000: unreadable
=> UNKNOWN
```

The new snapshot does not use the historical policy fallback that assumes systems without a usable battery are externally powered.

## 10. Aggregate Battery Flow

The aggregate enum is:

```text
BatteryFlow
  CHARGING
  DISCHARGING
  NOT_CHARGING
  FULL
  MIXED
  UNKNOWN
```

### 10.1 System batteries only

Only batteries that represent system power storage participate in the aggregate.

A battery with `scope=Device` may still be visible as an observed object but must not affect `battery_flow`.

### 10.2 Single battery

Known kernel status values map to the corresponding aggregate state:

```text
Charging     -> CHARGING
Discharging  -> DISCHARGING
Not charging -> NOT_CHARGING
Full         -> FULL
```

Unknown, unreadable, absent, or unsupported status remains `UNKNOWN` when the aggregate cannot be established.

### 10.3 Multiple batteries

- If all relevant system batteries have the same known flow state, use that state.
- If known system batteries have different flow states, use `MIXED`.
- If unknown state prevents a defensible aggregate conclusion, use `UNKNOWN`.

No battery is arbitrarily elected primary for this aggregate.

## 11. Power Supply Snapshot

The v1 power-supply snapshot is intentionally limited to attributes directly useful for understanding external power and battery flow.

Conceptually:

```text
PowerSupplySnapshot
  supply_id
  sysfs_path

  type
  scope
  online
  status
  usb_type

  voltage_now
  current_now
  power_now

  input_current_limit
  input_voltage_limit
  input_power_limit

  capacity
  capacity_level
```

Variable attributes use `ReadResult[T]`.

### 11.1 Units

Values are stored in the base units defined by the Linux power-supply ABI, such as microvolts, microamps, and microwatts where applicable.

User-facing formatting may convert those values to V, A, and W.

### 11.2 No synthetic power calculation

The discovery layer must not fill a missing `power_now` by calculating:

```text
voltage_now * current_now
```

The ABI attributes can represent different measurement or control semantics depending on the driver. The first delivery preserves the exposed attributes individually.

### 11.3 Input limits are not charger identity

`input_current_limit`, `input_voltage_limit`, and `input_power_limit` are reported with their kernel-defined meaning. They are not renamed or collapsed into a single charger-capability field.

## 12. Type-C Snapshot

Conceptually:

```text
TypeCPortSnapshot
  port_id
  sysfs_path
  power_role
  power_operation_mode
  partner_present
  partner_usb_pd_capable
```

### 12.1 Port identity

`port_id` identifies the object within the Type-C subsystem, for example `port0`. It is not a universal hardware identifier.

### 12.2 Partner relationship

A partner explicitly represented under the Type-C port hierarchy may be associated with that port because the relationship is defined by the Type-C class itself.

### 12.3 USB-PD operation mode

A Type-C `power_operation_mode` that reports USB Power Delivery is preserved as an observation that USB-PD negotiation mechanisms are active for that Type-C relationship.

It does not establish the current negotiated wattage.

### 12.4 No inferred partner capability

A missing explicit partner USB-PD capability attribute is not automatically replaced with `true` merely because another Type-C field says the port is currently using USB Power Delivery. The first delivery reports what each supported attribute directly says.

## 13. USB-PD Snapshot

The USB-PD class ABI is optional and must not be required for baseline operation.

Conceptually:

```text
UsbPdSnapshot
  pd_id
  sysfs_path
  revision
  version
  source_capabilities: tuple[UsbPdCapabilitySnapshot, ...]
  sink_capabilities: tuple[UsbPdCapabilitySnapshot, ...]
```

### 13.1 Capability model

Each capability keeps its ordinal position and ABI type.

Conceptually:

```text
UsbPdCapabilitySnapshot
  position
  kind
  sysfs_name
  type-specific fields as ReadResult values
```

The implementation should support the capability types exposed by the current kernel ABI where present, including:

- fixed supply;
- variable supply;
- battery;
- programmable supply / PPS;
- SPR adjustable voltage supply when exposed.

### 13.2 Unknown capability types

A capability directory using an unrecognized future type must not invalidate the USB-PD object.

For example:

```text
7:future_supply
```

may be represented as:

```text
position = 7
kind = UNKNOWN
sysfs_name = "7:future_supply"
```

The implementation does not recursively dump arbitrary unknown files. It preserves enough identity to diagnose forward-compatibility gaps without pretending to understand the new type.

### 13.3 Partial capability failure

Malformed or unreadable attributes are localized to that attribute. Other fields and other PDOs remain usable.

## 14. Identity and Correlation Rules

### 14.1 Identity is local to each subsystem

Examples:

```text
PowerSupplySnapshot.supply_id = "ADP1"
TypeCPortSnapshot.port_id = "port0"
UsbPdSnapshot.pd_id = "pd0"
```

These identifiers are useful for stable diagnostics inside their subsystem only.

### 14.2 No name/index matching

The implementation must not correlate objects using assumptions such as:

```text
ADP1 == port0
USBC000 == port0
pd0 == port0
```

### 14.3 Resolved paths do not automatically prove semantic identity

Sysfs class entries may be symlinks into `/sys/devices`. Readers may follow normal sysfs symlinks to collect attributes, but similar resolved topology paths are not by themselves sufficient to create a new cross-subsystem semantic relationship in v1.

If future kernel ABI documentation exposes an explicit cross-subsystem relationship, that relationship requires a separate design review before being used for policy.

## 15. Discovery Robustness

### 15.1 Sysfs is dynamic

Hotplug can cause objects to disappear between directory enumeration and attribute reads.

This is normal and must not crash the reporting command.

### 15.2 Local error handling

Examples:

- object disappears before an attribute read -> affected attribute/object degrades appropriately;
- permission failure -> `UNREADABLE`;
- malformed numeric value -> `INVALID`;
- missing optional attribute -> `MISSING`;
- one broken PDO -> other PDOs remain available.

### 15.3 Deterministic ordering

Collections must be emitted in deterministic order rather than raw filesystem enumeration order.

At minimum:

- power-supply objects: stable lexical ordering by local sysfs identity;
- Type-C ports: stable ordering by port identity;
- USB-PD objects: stable ordering by local identity;
- PDOs: numeric ordinal ordering when the ordinal is valid, with a deterministic fallback for malformed/unknown names.

Deterministic output improves diagnostics and synthetic verification.

### 15.4 Synthetic roots must remain isolated

When an injected root is used for tests or development fixtures, discovery must not silently fall back to the machine's real `/sys` tree for missing objects.

This prevents synthetic validation from accidentally observing or depending on real hardware.

## 16. Temporal Semantics

A `PowerContextSnapshot` is a point-in-time **collection pass**, not an atomic transaction across kernel subsystems.

The implementation does not claim that all attributes changed simultaneously or were read at exactly the same instant.

It must not perform broad double-read confirmation of every attribute in an attempt to manufacture atomicity. Such re-reading would increase overhead and hotplug races without creating a true kernel transaction.

Each generated system report receives a new power-context snapshot when requested. There is no long-lived cache in the first delivery.

## 17. System Report Integration

`SystemReport` gains an optional field:

```text
power_context: PowerContextSnapshot | None
```

`SystemInfo.generate_system_report()` gains an explicit opt-in collection flag, conceptually:

```text
include_power_context=False
```

This follows the existing pattern where optional Intel telemetry is collected only when requested.

### 17.1 One snapshot per report

When requested, `PowerContextDiscovery.discover()` runs once during report generation.

Formatters consume the stored snapshot. They must not re-read sysfs independently.

This avoids repeated collection and gives each report one internally consistent observation pass.

## 18. `--debug` Reporting

`--debug` requests the detailed `PowerContextSnapshot`.

The output should include:

- aggregate external-power state;
- aggregate battery flow;
- availability status for each subsystem;
- all relevant power-supply objects and supported attributes;
- all Type-C ports and supported partner metadata;
- USB-PD objects and supported capabilities where available;
- `missing`, `unreadable`, and `invalid` distinctions when diagnostically useful.

Example shape:

```text
Power Context
-------------
External power: online
Battery flow: charging

Power Supply subsystem: available
  ADP1
    Type: Mains
    Online: yes
    Input power limit: missing

Type-C subsystem: available
  port0
    Power role: sink
    Power operation mode: USB Power Delivery
    Partner: present

USB Power Delivery subsystem: available
  pd0
    Source capabilities:
      PDO 1: fixed_supply
      ...
```

Formatting details may follow existing project conventions, but the semantic distinctions above must remain visible.

## 19. `--stats` Reporting

`--stats` requests a fresh power-context snapshot but shows only a compact factual summary.

Suitable summary fields include:

```text
External power: Online
Battery flow: Charging
USB-PD: Available
```

Type-C information may be summarized only when doing so does not imply a false active-port correlation.

For multiple ports with different states, prefer neutral summaries such as port counts and subsystem availability rather than electing one port.

The periodic stats refresh remains reporting polling only. It does not become a policy loop.

## 20. `--monitor` and `--live`

These modes are unchanged in the first delivery.

They do not request the new snapshot and gain no new hardware-write behavior.

## 21. Daemon and Policy Isolation

The following remain unchanged in this delivery:

- `PowerSource.CHARGER/BATTERY` policy model;
- `charging()` compatibility behavior;
- `SystemInfo.external_power_state()` policy-facing behavior;
- Modern Intel HWP defaults;
- Legacy policy behavior;
- daemon scheduler triggers;
- power-supply event deduplication;
- systemd watchdog behavior;
- RAPL ownership and failsafe behavior.

The new context must not feed:

```text
EPP
governor
turbo
HWP Dynamic Boost
RAPL
platform profile
daemon scheduling
```

If implementation unexpectedly requires a behavior change in these areas, work stops and the design is revisited before proceeding.

## 22. Event Handling

No new Type-C or USB-PD event source is added in v1.

The existing daemon continues to use its existing power-supply event mechanism for current policy behavior.

This decision is deliberate: a read-only reporting feature does not justify expanding the daemon's invalidation graph before any policy consumes the new state.

A later policy design may decide to listen for Type-C or USB-PD changes. If so, events should remain invalidation signals and authoritative state should be re-read from sysfs, consistent with the existing Modern Intel event-loop principle.

## 23. Snap and Hostfs

Power-context readers do not contain direct knowledge of Snap.

The integration layer resolves the appropriate roots and constructs the discovery object.

This avoids duplicated logic such as:

```text
if snap:
    use hostfs
else:
    use /sys
```

inside each individual reader.

## 24. Expected Production Files

The expected production changes are limited primarily to:

```text
auto_cpufreq/modules/sysfs.py
auto_cpufreq/modules/intel_power.py
auto_cpufreq/modules/power_context.py
auto_cpufreq/modules/system_info.py
```

plus the existing reporting/formatting files responsible for `--debug` and `--stats` if that formatting is not contained in `system_info.py`.

No production change is expected in:

```text
auto_cpufreq/modules/policy.py
auto_cpufreq/modules/daemon_scheduler.py
auto_cpufreq/modules/intel_rapl.py
systemd service definitions
configuration examples
```

Unexpected need to change these control-path files is a scope-warning that requires review.

## 25. Planned Commit Structure

The implementation should remain reviewable through small commits with single responsibilities.

Proposed sequence:

1. `refactor: share generic sysfs readers`
   - add shared `sysfs.py`;
   - move only reusable read primitives;
   - preserve Intel behavior.

2. `feat: discover system power context`
   - add `power_context.py`;
   - power-supply enumeration;
   - system batteries;
   - `ExternalPower` and `BatteryFlow` aggregation.

3. `feat: discover USB Type-C context`
   - Type-C ports;
   - partner presence;
   - power role;
   - power operation mode.

4. `feat: discover USB Power Delivery capabilities`
   - optional USB-PD class enumeration;
   - supported PDO types;
   - tolerant unknown/partial parsing.

5. `feat: report power context diagnostics`
   - integrate with `SystemReport`;
   - hostfs/root resolution in the integration layer;
   - detailed `--debug`;
   - compact `--stats`.

Exact wording may be adjusted during implementation if file boundaries reveal a better grouping, but commits must stay narrowly scoped.

## 26. Verification Strategy

The project should validate behavior thoroughly without introducing a permanent upstream test framework solely for this work.

### 26.1 Temporary synthetic fixtures

A temporary verification branch/worktree may create synthetic sysfs trees using injected roots.

Coverage should include at least:

1. normal power-supply discovery;
2. multiple external sources;
3. multiple system batteries;
4. `scope=Device` excluded from system battery aggregation;
5. online + offline sources -> `ONLINE`;
6. offline + unreadable relevant source -> `UNKNOWN`;
7. divergent battery states -> `MIXED`;
8. missing attribute -> `MISSING`;
9. unreadable attribute -> `UNREADABLE`;
10. malformed attribute -> `INVALID`;
11. Type-C port without partner;
12. Type-C port with partner;
13. Type-C `USB Power Delivery` operation mode;
14. missing USB-PD root;
15. readable but empty USB-PD root;
16. fixed PDO;
17. variable PDO;
18. battery PDO;
19. programmable/PPS PDO;
20. adjustable PDO when supported by the ABI;
21. unknown future PDO type;
22. partially invalid PDO;
23. object disappearing during discovery;
24. deterministic ordering;
25. injected-root isolation from real `/sys`.

### 26.2 Intel regression verification

The `sysfs.py` refactor must prove that Intel discovery produces equivalent results for representative:

- available attributes;
- missing attributes;
- unreadable attributes;
- invalid attributes.

No Intel `--debug` or `--stats` semantic change belongs to that refactor.

### 26.3 CI

Existing upstream CI workflows applicable to the branch must remain green.

No unrelated test or build infrastructure should be introduced.

### 26.4 Real-hardware validation

After synthetic validation and CI, the Samsung Galaxy Book 4 development machine may be used for read-only validation.

Validation begins by observing what the actual kernel exposes under:

```text
/sys/class/power_supply
/sys/class/typec
/sys/class/usb_power_delivery
```

No interface is assumed to exist before inspection.

The hardware validation compares:

```text
raw sysfs
    <-> PowerContextSnapshot
    <-> --debug output
```

At minimum, validate connected and disconnected external-power states.

If USB-PD capability objects are exposed, compare parsed capabilities with raw sysfs. If the USB-PD class root is absent, that is a valid platform result rather than a feature failure; USB-PD parser behavior remains covered by synthetic fixtures.

### 26.5 No-write verification

Real-hardware validation must verify that the feature does not change:

- governor;
- EPP;
- Turbo Boost permission;
- HWP Dynamic Boost;
- CPU frequency limits;
- RAPL limits;
- platform profile;
- charging thresholds;
- Type-C roles;
- USB-PD state;
- daemon policy behavior.

## 27. Completion Criteria

The first delivery is complete only when there is evidence that:

1. subsystem availability is represented independently from object count;
2. partial/malformed sysfs data degrades locally;
3. multiple external sources are preserved and aggregated conservatively;
4. multiple system batteries are preserved and can report `MIXED`;
5. peripheral/device batteries do not distort the system aggregate;
6. current state and advertised USB-PD capabilities remain separate;
7. Type-C, `power_supply`, and USB-PD objects are not correlated by heuristic;
8. unknown capability types do not invalidate otherwise usable snapshots;
9. `--debug` exposes useful detailed diagnostics;
10. `--stats` exposes only defensible compact summaries;
11. Intel Stage 1 behavior remains unchanged by shared sysfs-reader extraction;
12. daemon and policy behavior remain unchanged;
13. no new hardware write occurs;
14. real-hardware read-only validation matches raw sysfs where the platform exposes the corresponding ABI.

## 28. Future Policy Work

Only after this observational layer is validated on real hardware should a separate design consider whether richer source context should influence policy.

Possible future questions include:

- Can the kernel reliably expose the current negotiated input capability rather than only advertised PDOs?
- How should external-power-online plus battery-discharge be interpreted across different platforms?
- Is source capability stable enough to justify policy thresholds?
- Should RAPL envelopes scale with validated source capability?
- Should EPP or Dynamic Boost react to a known constrained external source?
- Which event sources are necessary to keep a policy-facing context fresh?

Those are deliberately unanswered here.

## 29. Kernel Interfaces and Documentation

The design is based on documented Linux kernel interfaces rather than vendor/model assumptions:

- Power Supply Class: `https://docs.kernel.org/power/power_supply_class.html`
- Charger Manager: `https://docs.kernel.org/power/charger-manager.html`
- USB Type-C connector class: `https://docs.kernel.org/driver-api/usb/typec.html`
- Linux sysfs ABI documentation, including USB Power Delivery class entries: `https://docs.kernel.org/admin-guide/abi-testing-files.html`

The USB-PD class ABI is treated as optional and potentially less stable than long-established class interfaces. The implementation must remain functional when it is absent.

## 30. Final Architectural Boundary

The intended first-delivery boundary is:

```text
Linux kernel interfaces
        |
        v
shared read semantics (`sysfs.py`)
        |
        v
PowerContextDiscovery
        |
        v
immutable PowerContextSnapshot
        |
        +--> detailed `--debug`
        |
        +--> compact `--stats`

NO path from PowerContextSnapshot to policy in v1
```

This boundary is the primary safety property of the design. The first delivery learns how to observe modern external-power topology correctly before any later delivery decides how, or whether, that information should control CPU power policy.
