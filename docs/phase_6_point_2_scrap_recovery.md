# Phase 6 Point 2 — Usable Scrap Recovery and Permitted Reuse

## Existing functionality identified and reused

- Phase 6 Point 1 already stores `usable_scrap` on SmartEdge Production Job and copies it to the operation Production Lot.
- Phase 3 already provides operation lots, operation/physical Batch identities, Stock Entry genealogy capture, and forward/backward batch reporting.
- Phase 2 physical operation posting is opt-in and uses submitted Manufacture/Repack Stock Entries. It previously blocked usable scrap because no inventory disposition existed.
- Phase 4 Quality Inspection permissions and decisions remain unchanged. Recovery QC uses a separate standard Quality Inspection link and never enters or alters the mandatory route-QC lifecycle.
- ERPNext Batch, Serial and Batch Bundle, Stock Entry, Bin/SLE availability, Employee, Workstation, Warehouse, Item, and Quality Inspection are reused. No stock balance is updated directly.

No Grinder/Pulverizer configuration, recovery stock item mapping, recovered-material warehouse mapping, or permitted-reuse rules existed before Point 2.

## New DocTypes and justification

1. **SmartEdge Recovery Material** — one explicit source-item mapping to the batch-controlled usable-scrap, ground-material, and recovered-material Items and their company Warehouses. It also controls whether recovered stock needs QC.
2. **SmartEdge Recovery Reuse Rule** — explicit recovered Item/Item Group, permitted Operation, target Item/Item Group, optional maximum percentage/quantity, QC requirement, and disabled state. No universal reuse permission or percentage is assumed.
3. **SmartEdge Usable Scrap Lot** — immutable physical collection record for one originating Production Job. It links the source operation lot/batch to the submitted scrap receipt, scrap Batch, quantity, UOM, and Warehouse. Separating collection from processing permits partial and repeated recovery.
4. **SmartEdge Scrap Recovery** — one partial or full Grinder→Pulverizer processing cycle. It records employees, workstations, actual timestamps, quantities, losses, batches, Stock Entries, and optional Quality Inspection.
5. **SmartEdge Recovered Material Allocation** — an explicit, auditable selection of one recovered Batch for one Production Job. It reserves only within SmartEdge validation and becomes `Consumed` only after the submitted manufacturing Stock Entry consumes it.

Batch, Stock Entry, and Quality Inspection receive read-only SmartEdge recovery reference fields through `setup_scrap_recovery.py`. These links provide stable idempotency, cancellation checks, and traceability without replacing standard inventory records.

## Usable scrap collection

When a non-QC production operation completes with `usable_scrap > 0`, completion requires an enabled Recovery Material mapping for its source Item. After the normal operation posting and Batch identity succeed, SmartEdge creates exactly one:

- physical usable-scrap Batch;
- SmartEdge Usable Scrap Lot linked to Production Job, operation lot, source Item, and source Batch;
- submitted **Material Receipt** into the configured usable-scrap Warehouse.

The Production Job lock, unique job link on the scrap lot, deterministic Batch identity, and stable Stock Entry reference make retries idempotent. A cancelled receipt cannot be silently recreated. Completion with zero usable scrap follows the prior path unchanged.

## Grinder processing

An operator opens an active Usable Scrap Lot and starts a Scrap Recovery record. The form captures source stock, quantity to process, Grinder Workstation, active company Employee, actual date/time, configured ground Item, actual output, process loss, and remarks.

Completion validates:

- enabled stock/batch Items and company Warehouses;
- actual source Batch availability in the configured scrap Warehouse;
- positive input/output and non-negative loss;
- `Scrap Input = Ground Output + Process Loss` within six-decimal tolerance;
- no consumption beyond available stock.

A submitted **Repack** Stock Entry consumes the selected scrap Batch and creates a separate ground-material Batch in the configured ground-material Warehouse. Partial input leaves the remaining scrap Batch stock available for another recovery record. Repeated completion returns the existing submitted Stock Entry.

## Pulverizer processing

Pulverizer completion is available only after the same recovery record has a successfully posted Grinder Stock Entry and ground-material Batch. It captures the Pulverizer Workstation, active Employee, actual date/time, ground input, actual recovered output, process loss, and remarks.

It validates actual ground Batch stock and enforces `Ground Input = Recovered Output + Process Loss`. A submitted **Repack** Stock Entry consumes the ground Batch and creates a recovered-material Batch in the configured recovered-material Warehouse. Direct usable-scrap-to-Pulverizer processing is rejected.

## QC release

If the Recovery Material mapping requires QC, Pulverizer completion creates or reuses one standard draft Quality Inspection linked to the recovery, recovered Item, and recovered Batch. Recovered stock exists physically but is excluded from eligible reuse until the inspection is submitted with `Accepted` status. A rejected, draft, missing, or cancelled inspection cannot release reuse. Reuse rules can independently require QC as an additional restriction.

## Permitted reuse and manufacturing consumption

The Production Job action **Allocate Recovered Material** lists only batches that:

- have a completed Pulverizer posting;
- match an enabled rule for the Job operation and target Item/Item Group;
- satisfy required QC;
- have unreserved physical Batch stock;
- remain within the configured per-job maximum quantity and/or percentage.

Allocations are revalidated under a Batch lock immediately before stock posting. For normal Final EB manufacture, explicit recovered inputs are appended to the existing final Manufacture Stock Entry. For opt-in physical-operation manufacture/repack they are appended to that operation Stock Entry. For required-material Work Orders they are appended to the existing Work Order Manufacture Stock Entry. Existing BOM rows and quantities are not rewritten; the explicit allocation is the authorization and audit record for the additional recovered input.

The allocation is marked `Consumed` only after Stock Entry submission. Cancellation of the manufacturing Stock Entry restores it to `Reserved`. Users can cancel an unconsumed allocation.

## Traceability and cancellation

Each recovery Stock Entry is captured by the existing genealogy hooks. Recovery Batches store their source Batch, source Production Job, usable-scrap lot, recovery record, and stage. `get_recovery_trace` returns the complete chain:

Production Job → Production Lot/Batch → Usable Scrap Lot/Batch/Receipt → Grinder Stock Entry/Ground Batch → Pulverizer Stock Entry/Recovered Batch/QI → Reuse Allocation/Production Job/Manufacturing Stock Entry → existing final-output genealogy.

Cancellation follows standard ERPNext Stock Entry cancellation. Additional guards require downstream recovery and allocations to be reversed first. Cancellation synchronizes recovery/lot/allocation states; submitted Stock Entries are never edited.

## Files changed for Point 2

- `smart_edge_custom/manufacturing/scrap_recovery.py`
- `smart_edge_custom/manufacturing/production_lot.py`
- `smart_edge_custom/manufacturing/physical_operation_stock.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/manufacturing/material_production.py`
- `smart_edge_custom/hooks.py`
- `smart_edge_custom/patches.txt`
- `smart_edge_custom/patches/setup_scrap_recovery.py`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.js`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_recovery_material/*`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_recovery_reuse_rule/*`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_usable_scrap_lot/*`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_scrap_recovery/*`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_recovered_material_allocation/*`
- `smart_edge_custom/smart_edge_custom/workspace/smartedge_manufacturing/smartedge_manufacturing.json`
- `smart_edge_custom/test_scrap_recovery.py`
- `smart_edge_custom/test_finished_operation_batches.py`
- `docs/phase_6_point_2_scrap_recovery.md`

## Required configuration and deployment

Before operators record usable scrap:

1. Create enabled, stock, batch-controlled usable-scrap, ground-material, and recovered-material Items with one common stock UOM.
2. Create/select non-group company Warehouses for all three stages.
3. Create a SmartEdge Recovery Material mapping for each source production Item that may generate usable scrap.
4. Configure suitable Grinder and Pulverizer Workstations and active Employees.
5. Create only the required SmartEdge Recovery Reuse Rules, including limits and QC requirement.

This Point adds DocTypes and custom fields, so an approved deployment requires `bench --site <site> migrate`, an asset build, and cache clearing. No migration or deployment was run during implementation.
