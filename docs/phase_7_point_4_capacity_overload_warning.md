# Phase 7 Point 4 — Workstation Capacity Overload Warning

## Implemented behavior

- A workstation drag/drop and the Plan Operation dialog now run a read-only capacity preflight before saving.
- The projection uses the same Phase 7 rate, calendar, shift, holiday, downtime, and allocation logic used by the Planning Board.
- The moved operation is removed from destination totals before being added back, so the warning reflects the actual incremental effect and does not double count a reschedule.
- An overload dialog shows available hours, already-planned hours, new-operation hours, projected hours, utilization, and overload hours.
- Cancel leaves the saved schedule and board placement unchanged. `Continue Anyway` is available only to Manufacturing Manager, System Manager, or Administrator users.
- A zero-hour configured calendar with positive required hours is treated as overload and displays utilization as `N/A`.

## Server enforcement and concurrency

The preflight issues a five-minute confirmation token only for an authorized user. The token is scoped to the user, planning entry, workstation, date, start time, duration, and the displayed capacity snapshot.

On save, the server locks the destination Workstation, recalculates capacity, and validates the token. A changed allocation/calendar/rate snapshot, an expired token, another user, or a different move requires a fresh confirmation. The token is removed after a successful save. The existing plan validation, compatibility, date/time, sequence, and legacy workstation checks still run.

The existing generic capacity override parameter is not accepted as overload confirmation. The Planning Board no longer presents its generic capacity-override checkbox.

## Missing configuration

Missing rate, quantity, workstation hours, or selected shift configuration uses the separate title `Machine Capacity Not Configured` and message:

> Capacity cannot be verified for this Workstation, product size and selected date/shift.

The user may cancel or continue with the established scheduling behavior. A continued schedule is returned as `not_configured`; it is not labeled capacity-validated.

## Scope notes

- Drag handles represent individual operation rows/cards, including operations displayed inside an item group. Only the operation actually dragged is projected and moved.
- No automatic rescheduling, machine reassignment, new DocType, schema change, migration, or audit DocType was added.
- No suitable existing log records the capacity snapshot/confirmation itself, so the optional confirmation audit was not added; creating a new audit DocType is outside this point's scope. Existing document change tracking continues to record the accepted schedule field changes.

## Verification

- Python unit tests cover destination projection, own-reservation exclusion, overload math, zero availability, missing configuration, and confirmation scope/signature invalidation.
- JavaScript tests cover all warning metrics and `N/A` utilization rendering.
- Python compilation, JavaScript syntax, and `git diff --check` are included in the verification pass.
