# Phase 7 Point 3 — Planning Board Capacity and Utilization

## Reused service

The implementation extends `smart_edge_custom.manufacturing.machine_capacity`, introduced for Phase 7 Points 1 and 2. No second capacity master or calculation service was created.

Capacity-rate rows are loaded once for the visible Workstations. Active planning allocations are loaded in a batch for the visible date range. Availability is calculated once per unique Workstation/date/shift context and reused by every matching card.

## Planned hours and quantities

- Extrusion planned hours: `planned_kg / applicable Kg/Hour rate`.
- Printing and slitting planned hours: `planned_meter / applicable Meter/Hour rate`.
- Planned Kg and Meter are read directly from `SmartEdge Production Plan Entry`.
- No Meter-to-Kg conversion was added. Meter-based cards show Planned Kg only when that existing field already contains a value.
- Missing rate: `Not Configured`.
- Configured rate with no positive planned quantity: `Not Available`.

## Shared allocation and utilization

Each active `Ready`, `Planned`, `In Progress`, or `On Hold` plan entry is counted once. Cancelled and completed plans are excluded. A SmartEdge plan and its corresponding ERPNext Work Order operation are not counted twice by the underlying availability service.

Rate-derived planned hours are used for SmartEdge allocations when available. A scheduled start/end duration is retained as a safe allocation fallback when a legacy allocation has no usable rate or planned quantity.

For each Workstation/date/shift:

`Utilization % = Total Allocated Planned Hours / Net Available Hours × 100`

Utilization is not capped. Zero or unconfigured net hours produce `N/A`.

Different product sizes use their own applicable rates to derive hours, but those hours are added against the same Workstation time pool. Capacity rates themselves are never added together.

## Current operation exclusion

The total allocation, including the current operation, drives authoritative Workstation utilization. Available-to-plan capacity excludes only the current card's own allocation:

`Other Allocated Hours = Total Allocated Hours - Current Operation Hours`

`Remaining Available Hours = max(Net Available Hours - Other Allocated Hours, 0)`

`Available Capacity = Remaining Available Hours × Current Operation Rate`

This prevents a scheduled operation from reducing its own available-to-plan value twice. Total overload remains `max(Total Allocated Hours - Net Available Hours, 0)` and is displayed separately.

## Planning Board presentation

Existing operation cards now show a compact block containing:

- Planned Kg, when available
- Planned Meter, when applicable
- Planned Hours
- applicable rate and unit
- Available Capacity
- total Workstation utilization

The utilization tooltip explains total allocated hours divided by net available hours. Visual states are:

- below 80%: normal
- 80% through 100%: high
- above 100%: overloaded
- zero/unconfigured net hours: unknown / N/A

Existing Workstation group headers show allocated hours, net available hours, remaining hours, and the same daily utilization summary. Card grouping, columns, counts, scheduling actions, and manufacturing execution were not changed.

## Configuration status

The audited `smart-edge` site still has no Workstation Working Hour rows, Shift Types, Downtime Entries, or installed capacity-rate table because migration and configuration were intentionally not performed. Until deployment and real configuration are completed, the board safely displays `Not Configured`, `Not Available`, or utilization `N/A` instead of inventing capacity.

No Workstation, Machine Master, operation, planning card, Work Order, Stock Entry, or historical production record was created or modified by this implementation.
