# Phase 7 Point 5 — Configuration-Driven Capacity Rates

## Existing configuration structure

`SmartEdge Workstation Capacity Rate` is the existing Phase 7 configuration DocType. Each enabled row belongs to an existing ERPNext Workstation and one capacity unit (`Kg/Hour` or `Meter/Hour`). Optional selectors scope a row by Operation, Company, Product Size, Finished Item, and Process Route.

The DocType already enforces an existing, enabled Workstation, a positive rate, a supported unit, valid selector scope, and no exact duplicate selector row. It does not create or rename Workstations.

## Authoritative service

All Point 3 board metrics and Point 4 drag/drop projections use `manufacturing.machine_capacity`:

- `select_capacity_rate` resolves the most specific eligible row and rejects equally specific ambiguity.
- `get_rate_payload` maps Extrusion to configured `Kg/Hour`, and Printing/Slitting to configured `Meter/Hour`.
- `prepare_capacity_allocations` converts each operation quantity to hours using its own selected rate.
- `get_context_capacity_metrics` aggregates those hours against the configured calendar/shift/downtime availability.
- `get_schedule_capacity_projection` uses the same lookup and allocation calculation for overload preflight and confirmation.

The browser only formats backend values; it contains no capacity-rate map or production-speed formula.

## Point 5 hardening

The audit found no sample 35/50 Kg/Hour or fixed Meter/Hour values in active runtime capacity logic. Those values occur only in tests and documentation.

One non-authoritative fallback was removed: an applicable Extrusion/Printing/Slitting allocation without a valid rate previously used its scheduled start/end duration in shared utilization. Such an allocation now has no calculated machine hours and marks the Workstation/date/shift calculation unverified. Consequently:

- Planned Hours remains `Not Configured`/`Not Available`.
- Available production quantity is not fabricated.
- Utilization is `N/A` instead of a partial or misleading percentage.
- Drag/drop cannot falsely report that the operation fits; it uses the existing `Machine Capacity Not Configured` flow and remains non-blocking as established in Point 4.

Non-rate-driven operation categories retain their genuine scheduled-duration allocation; this change is limited to categories whose Phase 7 hours must come from configured production rates.

## Freshness

Capacity lookups are request-local only—there is no persistent rate cache. Saving or deleting a capacity-rate record now also clears that request-local cache. The next calculation therefore reads the latest enabled configuration without deployment or code changes. Historical actual execution hours and production records are not rewritten.

## Scope and verification

- No sample rates were inserted.
- No Workstation, Machine Master, capacity table, schema field, or migration was created.
- No production quantity, route, Workstation assignment, shift assignment, execution, stock, QC, genealogy, or scrap/recovery behavior was changed.
- Tests cover Workstation/unit isolation, product specificity, multiple machine rates, Printing and Slitting Meter/Hour calculations, rate-update freshness, missing-rate behavior, shared-context invalidation, and drag/drop unverified-capacity handling.
