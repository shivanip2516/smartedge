# Phase 6 Points 3 and 4 — Unusable Scrap and Movement Traceability

## Existing functionality reused

- Phase 6 Point 1 already captures Good Output, Rejected Output, Rework, Usable Scrap, and Unusable Scrap on the Production Job and Production Lot ledger.
- Phase 6 Point 2 already provides usable-scrap collection, Grinder, Pulverizer, recovered Batch stock, QC release, reuse allocations, batch bundles, and cancellation controls.
- Phase 2 physical WIP and final manufacturing use submitted ERPNext Manufacture/Repack Stock Entries.
- Phase 3 captures Batch genealogy from submitted Stock Entries; no parallel genealogy table was added.
- Standard ERPNext Warehouse, Item, Batch, Stock Entry, Bin/SLE availability, Employee, and Workstation records remain authoritative.

The missing functionality was a physically controlled unusable-scrap identity, storage lifecycle, authorized partial disposal, and a unified Production Job history view with immutable source execution references.

## Configuration

`SmartEdge Recovery Material` now has two optional Point 3 fields:

- **Unusable Scrap Item** — one controlled stock Item representing unusable material. It must be different from the usable, ground, and recovered Items. Batch control is supported but is not forced.
- **Authorized Unusable Scrap Warehouse** — an enabled, non-group Warehouse belonging to the mapping company.

Both fields must be configured together whenever the source production Item can report unusable scrap. No Item or Warehouse is created automatically and no Warehouse name is hardcoded.

## Recording and controlled storage

One `SmartEdge Unusable Scrap Lot` is created for each Production Job with `unusable_scrap > 0`. Its source Production Job, Final EB BOM Generator, Production Lot, Operation, Workstation, Employee, source Item, source Batch when available, quantity, UOM, and generation time are immutable after posting.

The lifecycle is:

`Recorded → Pending Storage → Stored → Pending Disposal → Disposed`

Inventory is never created by a standalone receipt derived only from the quantity field. For a physically posted Final EB or required-material/Work Order operation, the configured unusable-scrap Item is appended as an ERPNext scrap/by-product output of the same existing Manufacture or Repack Stock Entry. This accounts for production input and unusable output in one transaction and prevents double receipt. The lot becomes `Stored` only after that Stock Entry submits successfully.

For a non-stock operation with no physical inventory transaction, the record remains `Pending Storage`; it cannot be disposed or treated as stock. Missing Item/Warehouse configuration blocks completion with a clear configuration error.

Batch-controlled scrap receives one deterministic physical Batch linked to the source lot. Non-batch Items retain their Production Lot genealogy without creating an artificial ERPNext Batch.

## Disposal and authorization

Only Administrator, Manufacturing Manager, or Stock Manager can request and post disposal. The server checks authorization independently of the form.

The `Request Disposal` action moves successfully stored stock to `Pending Disposal`. `Dispose Scrap` captures:

- positive disposal quantity;
- reason and remarks;
- actual posting date/time;
- active responsible Employee in the same company;
- a retry-safe request ID.

The server locks the scrap lot and Batch, rechecks actual inventory, and submits a standard ERPNext **Material Issue** from the authorized scrap Warehouse. Partial disposal is supported. Available quantity is the lesser of the lot's undisposed balance and physical Batch/Bin stock. A repeated request ID returns the submitted transaction; a cancelled request ID requires controlled amendment. Disposal cannot exceed stored stock.

Storage cancellation is blocked while submitted disposal entries exist. Cancelling a disposal restores the pending balance from actual stock. No Bin or Stock Ledger Entry is modified directly.

## Preventing reuse

- The unusable Item must differ from all Point 2 usable/recovery Items.
- Grinder validation accepts only the linked usable-scrap lot and explicitly rejects a Batch marked as unusable scrap.
- Every Stock Entry source row is server-validated. Stock in an authorized unusable-scrap Warehouse or an unusable-scrap Batch can only leave through the controlled disposal transaction.
- Normal production, recovery, Repack, transfer, and manufacturing calls therefore cannot consume this stock even when invoked directly through the API.

The existing usable scrap workflow is unchanged.

## Traceability and visibility

Recovery Stock Entries now retain source Production Job, Production Lot, Operation, Workstation, Employee, Batch, movement Employee/Workstation, movement classification, and linked recovery records. Existing usable-scrap lots also retain their originating Workstation and Employee.

The Production Job dashboard displays a composed movement history without duplicating Point 2 records. It includes usable scrap generation, Grinder input/output, Pulverizer output, recovered-material reuse, unusable generation, controlled storage, disposal, and reversals with quantity/UOM, source identity, Warehouses, time, status, and Stock Entry.

`SmartEdge Unusable Scrap Lot` list indicators, the Manufacturing workspace link, and Desk notification counts expose `Pending Storage` and `Pending Disposal` records to users with permission.

Phase 3 genealogy continues to be captured from the same submitted Manufacture/Repack/Material Issue Stock Entries. The unusable scrap Batch links back to its source Production Job/Batch and the scrap lot links to its Production Lot, so forward and backward tracing remain connected without duplicate genealogy rows.

## Duplicate and concurrency protection

- Production Job locking plus a unique Production Job link permits one unusable lot only.
- Deterministic Batch identity prevents repeated storage batches.
- Storage is part of the existing manufacturing transaction, not a second receipt.
- Disposal locks the lot and Batch before checking current stock.
- Request IDs make API retries idempotent.
- Submitted and cancelled request states are distinguished.
- Actual Batch/Bin stock and per-lot issued quantities are both enforced.
- Status changes occur only after successful Stock Entry submission.

## Files changed for Points 3 and 4

- `smart_edge_custom/manufacturing/unusable_scrap.py`
- `smart_edge_custom/manufacturing/scrap_recovery.py`
- `smart_edge_custom/manufacturing/production_lot.py`
- `smart_edge_custom/manufacturing/physical_operation_stock.py`
- `smart_edge_custom/manufacturing/material_production.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/hooks.py`
- `smart_edge_custom/notifications.py`
- `smart_edge_custom/patches/setup_scrap_recovery.py`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_recovery_material/smartedge_recovery_material.json`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_usable_scrap_lot/smartedge_usable_scrap_lot.json`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_unusable_scrap_lot/*`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.js`
- `smart_edge_custom/smart_edge_custom/workspace/smartedge_manufacturing/smartedge_manufacturing.json`
- `smart_edge_custom/test_unusable_scrap.py`
- `docs/phase_6_points_3_4_unusable_scrap_traceability.md`

## Validation and deployment

Automated validation covers configuration, idempotent recording, changed-quantity rejection, production/recovery consumption blocking, disposal authorization, storage-state enforcement, partial balance calculation, retry handling, and over-disposal. The complete affected unit suite contains 62 passing Python tests plus both passing Planning Board JavaScript suites.

Deployment requires an approved `bench --site <site> migrate`, asset build, and cache clear. Existing Recovery Material mappings that may produce unusable scrap must be completed with the Point 3 Item and Warehouse before such scrap can be recorded. No migration, build, deployment, commit, or push was performed during implementation.

## Limitation

Historical completed jobs are not backfilled or altered. A non-stock operation can record the traceability lot but remains `Pending Storage` until its route uses an existing supported physical Manufacture/Repack transaction; the implementation deliberately does not invent inventory with a Material Receipt. Site-backed integration tests require an enabled Frappe test site and were not run because this site has tests disabled.
