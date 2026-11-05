# Phase 4 — Mandatory Quality Control

## Existing functionality audited

- The configured route snapshots already contain `QC After Extrusion` and, where applicable, `QC After Offline Printing`. No QC operation was inserted or duplicated.
- Production Jobs, route sequence/release quantities, Planning Board execution controls, Production Lots, WIP Ledger QC Hold/Release movements, operation Batches, and Batch Genealogy already existed.
- The old QC Production Job completion path treated good output as implicitly released. It did not persist an inspection decision, measurements, authorization, partial acceptance, or a server-side accepted-quantity gate.
- ERPNext Quality Inspection was suitable as the inspection record, but its standard status/reference fields alone do not model the SmartEdge route lifecycle or partial quantities.

## Implementation

The implementation reuses ERPNext `Quality Inspection`. A patch adds SmartEdge-only custom fields for the production definition, QC plan entry, source Production Job/Lot/Batch, checkpoint, lifecycle status, QC Employee and inspection time, width, thickness, four Pass/Fail/Not Applicable results, eligible/inspected/accepted/rejected/rework quantities, reason, remarks, and reinspection links. `SmartEdge Production Job` receives one Quality Inspection link.

The standard reference options are extended with `SmartEdge Production Job`. A subclass preserves all standard Quality Inspection behavior and skips only ERPNext's assumption that this custom reference has a child `Item` table. Standard purchase, delivery, stock, and Job Card inspections are unchanged.

### Pending activation and review

After a normal manufacturing job completes and its existing Production Lot is recorded, the next mandatory route entry is inspected. If that entry is QC, one idempotent Pending Quality Inspection is created for the released quantity and linked to the actual source job, lot, and operation Batch. Retries reuse an active record. Existing historical completions are not backfilled or approved. A ready in-progress checkpoint with no record can safely create its pending inspection when an authorized user starts QC.

Starting QC requires the existing `Quality Manager` role and an active Employee. It changes Pending to Under Review. Both Planning Board endpoints and direct Production Job document methods enforce this authorization.

### Decisions and quantities

The Planning Board popup records the required measurements, categorical results, quantities, reason, remarks, Employee, lot, and Batch. The server requires:

- non-negative quantities;
- `Inspected = Accepted + Rejected + Rework`;
- inspected quantity not exceeding that inspection's eligible balance; and
- a reason for rejection or rework.

Partial inspection creates another Pending record only for the uninspected balance. Accepted quantity is aggregated across decisions and becomes the sole downstream release quantity. Rejected quantity is never released. Rework remains blocked; `Resubmit Rework` creates a linked reinspection without altering the original decision. No physical rework operation or Stock Entry is invented because no separate configured rework execution route exists.

### Mandatory gates

QC Production Jobs cannot be completed through the normal completion API. Acceptance synchronizes only cumulative accepted meter/kg quantities to the next mandatory plan entry. Start, resume, direct Production Job completion, downstream physical WIP posting, and final manufacturing posting all validate the accepted quantity on the server. The Planning Board also disables downstream Start while the prior QC release is zero and displays the required QC-pending message. Existing scheduling, workstation, capacity, and date checks continue to run.

The QC job completes only when the original checkpoint quantity has been fully classified and no rework remains unresolved. Its existing WIP Ledger handling then records QC release/rejection information without creating duplicate inventory.

### Notifications and traceability

Creating a Pending inspection makes one idempotent Notification Log alert per enabled user holding `Quality Manager`; it does not notify all employees. The alert links directly to the Quality Inspection and includes the source Production Job, item, operation, workstation, quantity, and completion time. If no role recipient exists, production is not interrupted and a configuration warning is shown in QC visibility details.

Quality Inspection links are written to the existing Production Lot, and existing WIP Ledger/Batch Genealogy structures are reused. QC does not create a Batch, duplicate genealogy relation, or fabricate a Stock Entry. Final stock posting aggregates accepted evidence for every mandatory QC entry in the actual plan and blocks quantities beyond that evidence.

## Phase 4 files

- `smart_edge_custom/manufacturing/quality_control.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/manufacturing/physical_operation_stock.py`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.py`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.json`
- `smart_edge_custom/smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js`
- `smart_edge_custom/hooks.py`
- `smart_edge_custom/patches/setup_quality_control.py`
- `smart_edge_custom/patches.txt`
- `smart_edge_custom/test_quality_control.py`
- `docs/phase_4_quality_control.md`

## Verification

- `bench --site smart-edge migrate`: passed; the Phase 4 patch executed successfully.
- Live metadata inspection: passed; custom fields and the SmartEdge Production Job reference option exist.
- Historical safety check: zero SmartEdge QC inspections were created by migration; completed historical QC entries were not marked Accepted.
- In-progress safety check: an existing released QC entry reports Pending with its exact remaining eligible quantity without creating a record during the read-only check.
- Python focused suite: 39 tests passed across QC validation/authorization/reinspection plus Phase 2 physical stock, Production Lot, and Batch Genealogy regressions.
- Planning Board JavaScript syntax and two existing behavior suites: passed.
- `bench build --app smart_edge_custom`: passed.
- Python compilation and `git diff --check`: passed.

The site has `allow_tests` disabled, so Frappe's database-writing integration test runner was not enabled. No fabricated production completion or QC approval was inserted merely to test the workflow. Live schema/context checks and rollback-free read-only validation were used instead. Existing Compound/Shade Work Orders, FIFO allocation, recipes, valuation, scheduling, grouping, and final-stock calculations were not redesigned.
