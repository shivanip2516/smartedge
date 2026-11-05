# Phase 7 Points 1 & 2 — Machine Capacity Planning

## Existing configuration audit

The `smart-edge` site was inspected without changing master data.

- 36 existing ERPNext Workstations are present and enabled.
- Relevant machines already include `Extruder-1`, `Extruder-2`, `Offline Printing Machine`, `400 mm Slitting Machine`, `80 mm Small Slitting Machine`, `Slitter-1`, `Slitter-2`, `Slitter-Mini`, and `Slitter-U2`.
- Existing Size Weight records include the requested `0.45x22` and `2x22`; they are referenced, not recreated.
- Every audited Workstation currently has Job Capacity `1`.
- No Workstation Working Hour rows exist, `total_working_hours` is zero, and no Workstation has a Holiday List.
- No Shift Type records exist.
- No Downtime Entry records exist.
- The site timezone is `Asia/Kolkata`.

Consequently the Planning Board reports missing working-hours and shift configuration. It does not assume an eight-hour shift or continuous operation.

## Capacity-rate configuration

`SmartEdge Workstation Capacity Rate` is a linked configuration DocType. Every record points to an existing ERPNext Workstation; it does not create or duplicate a machine master.

Fields:

- Workstation (required)
- Operation (optional only for a Workstation-wide default)
- Company (optional scope; blank is the shared fallback)
- Product Size (`Size Weight`)
- Finished Item (`Item`)
- Process Route (`SmartEdge Process Route`)
- Capacity Rate (must be greater than zero)
- Capacity Unit (`Kg/Hour` or `Meter/Hour`)
- Enabled

No example rates are installed. A migration creates only the DocType schema.

### Extruders

Create one or more enabled rows using the existing Extruder Workstation, Extrusion operation, applicable Size Weight, and optional finished item/route. Select `Kg/Hour`.

### Offline printing and slitting

Create rows using the existing printing/slitting Workstation and operation. Select `Meter/Hour`. Product/size/route may be omitted for an operation default or supplied for a more specific rate.

## Deterministic matching

Only rows for the selected Workstation and required unit are considered. A company-specific row is preferred to a shared row at the same specificity.

1. Workstation + Operation + Product/Size + Route
2. Workstation + Operation + Size
3. Workstation + Operation + Product
4. Workstation + Operation default
5. Workstation default

Configured selectors must exactly match the production context. `Kg/Hour` is never substituted for `Meter/Hour`, or vice versa. An equally specific conflict raises an error. An exact duplicate configuration is rejected when saved. A missing match displays: `Capacity not configured for this Workstation and production specification.`

## Availability calculation

Daily scheduled intervals come from enabled ERPNext Workstation Working Hour rows. The Workstation Holiday List is respected. Overlapping intervals are merged before hours are calculated.

ERPNext Downtime Entries intersecting the schedule are merged and deducted once:

`Net Available Hours = Scheduled Hours - Downtime Hours`

Active SmartEdge plan allocations and active ERPNext Work Order operation allocations are then deducted. A Work Order operation represented by a SmartEdge plan entry is not deducted again. Completed/cancelled records are not active reservations.

`Remaining Hours = max(Net Available Hours - Reserved Hours, 0)`

Any excess is exposed separately as `Overallocated Hours`; remaining capacity never becomes a misleading negative available value.

When a rate applies:

`Production Capacity = Net Available Hours × Capacity Rate`

`Remaining Production Capacity = Remaining Hours × Capacity Rate`

## Shift calculation

The selected `planned_shift`/production shift uses the existing Shift Type start and end times. End times earlier than start times are treated as overnight shifts. Equal start/end times are invalid. Shift holidays use the Shift Type Holiday List, falling back to the Workstation Holiday List.

When both a Workstation schedule and a shift exist, their intersection is used. This prevents shift time outside the machine schedule from inflating capacity. Downtime and active reservations are clipped to that intersection.

Missing Shift Type, working hours, or holiday configuration is reported explicitly; it is not fabricated.

## Required production time

- Extrusion: `planned_kg / Kg per Hour`
- Printing: `planned_meter / Meter per Hour`
- Slitting: `planned_meter / Meter per Hour`

No Meter/Kg conversion, setup allowance, BOM calculation, or weight formula was added or changed.

## Planning Board visibility

Existing cards now show, where applicable:

- selected Workstation and product size (existing display)
- applicable rate and unit
- planned quantity in Kg or Meter
- estimated required hours
- shift available and remaining hours
- a clear missing-rate or missing-calendar/shift message

Workstation group headers show reserved hours, net available hours, and remaining hours. The existing card layout and scheduling actions remain intact.

## Deployment

After review, run the normal site migration so Frappe creates `SmartEdge Workstation Capacity Rate`, then clear cache/build assets according to the deployment process. Configure Workstation Working Hours, Holiday Lists, Shift Types, Downtime Entries, and real capacity-rate rows before relying on availability values.

No migration, build, service restart, Workstation creation, sample data creation, or historical-record update was performed during implementation.
