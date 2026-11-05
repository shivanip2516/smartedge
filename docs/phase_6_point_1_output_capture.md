# Phase 6 Point 1 — Operation Output Capture

## Existing implementation findings

The existing SmartEdge completion workflow already contained the required fields on `SmartEdge Production Job`:

- `good_qty`
- `rejected_qty`
- `rework_qty`
- `usable_scrap`
- `unusable_scrap`

All five are Float fields with precision 6. They were already returned by the completion-context API, submitted by the Planning Board completion dialog, saved on the Production Job, copied to the operation Production Lot, and returned to completed Planning Board cards. The Production Job form already displays them. No new field, DocType, child table, fixture, or schema patch was required.

Existing downstream production logic continues to use Good Quantity or Actual Completed Quantity as previously implemented. This change does not introduce a reconciliation formula or use rejection, rework, or scrap quantities for downstream release.

## Point 1 changes

- The existing completion fields now use the requested labels: Good Output, Rejected Output, Rework, Usable Scrap, and Unusable Scrap.
- Each completion-dialog label displays the applicable output UOM. Finished operations use metres; material-production operations use their configured quantity UOM.
- The five saved values appear in the existing expanded operation details after completion. Compact card dimensions are unchanged.
- Frontend validation rejects negative and non-numeric values before calling the completion API.
- Backend parsing rejects negative, non-numeric, NaN, and infinite values instead of silently converting invalid input to zero. Values retain the existing field precision.
- Missing optional categories continue to default to zero during completion.
- The existing job lock and completed-job retry handling continue to prevent duplicate completion effects.

## Persistence and scope

Values are saved on the authoritative `SmartEdge Production Job` during the existing completion transaction. Existing production-lot recording copies them to `good_quantity`, `rejected_quantity`, `rework_quantity`, `usable_scrap`, and `unusable_scrap`, preserving completed-operation history and Planning Board visibility.

No stock entry, batch, scrap transfer, recovery, disposal, rework job, QC decision, or downstream quantity rule was added or changed. Existing physical-stock validations remain intact.

## Files changed

- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js`
- `smart_edge_custom/test_phase6_output_capture.py`
- `smart_edge_custom/test_planning_board_traversal.js`
- `smart_edge_custom/test_finished_operation_batches.py`
- `docs/phase_6_point_1_output_capture.md`

## Deployment

This point adds no schema changes and requires no migration. After review and approval, deploy the custom-app code, build the SmartEdge assets, and clear the site cache.
