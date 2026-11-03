# SmartEdge Finished Goods Operations, Production Lots and WIP Ledger

SmartEdge Production Lots are non-stock operation traceability batches. They reference the existing finished Item and never create an Item, ERPNext Batch, warehouse balance or Stock Entry.

## Operation source and count

New Finished Goods planning reads the active, submitted BOM for `Final EB BOM Generator.finished_item`, preferring the default BOM and then the latest modified matching BOM. Only that BOM's direct `BOM Operation` rows are used, in table order, with `sequence_id` or the row index as the sequence. Component BOM operations are not exploded or appended.

The selected reference is retained in the Generator's hidden `finished_bom` Link and new Final EB Plan Entries' existing `bom_no` field. Process-route configuration remains available for the existing defaults and consumable/cost-head calculations; it no longer supplies the new Finished Goods operation list. Cost formulas and standard BOM calculations are unchanged.

A missing submitted BOM or an empty operation table produces a configuration message and blocks new planning. A process route is not silently substituted. Already released production retains its saved operation preview and row identities; historical operation records are not deleted or regenerated.

Material-production execution keys remain separate from Finished Goods execution keys. A Finished Goods BOM with three operations therefore has three Finished Goods operations, while Compound and Base Colour remain their own existing material-production processes. Their stock consumption, predecessor checks and genealogy relationships remain in place.

The live investigation found three Final EB operations in each of FEBG-00010 and FEBG-00011, plus two material-production rows for each. It did not find six duplicated Finished Goods rows within one Generator. Six Final EB rows existed across the two Generators. The faulty source/count layers were process-route operation generation and material/Finished Goods execution grouping.

## Completion and quantities

- Successful `SmartEdge Production Job.complete_job` is the common completion path for both the board API and the document method.
- Required Material completion continues to use its existing Work Order, manufacturing Stock Entry and physical batches. It does not create a Finished Goods operation lot.
- Every new BOM-defined Finished Goods operation creates one output lot after completion validation, including BOM-defined QC operations. Legacy route QC records retain their previous incoming-lot behavior.
- QC operations retain incoming-lot hold/release movements. A BOM-defined QC operation does not claim the predecessor's lot as its own output.
- Pending, planned, started, paused or invalid completions do not create output lots.
- The unique `production_job` field, Production Job row lock and current locking lot lookup prevent duplicate lots. Stable WIP movement keys prevent duplicate movements. Repeating a completed request returns the existing lot without resetting actual quantities. An old completed job with no lot requires the existing controlled backfill workflow.
- Lot `finished_item` comes from the linked Generator's `finished_item`. Its display name is read from Item.
- Lot `planned_quantity` comes from Plan Entry `production_quantity`. Board enrichment also preserves that Plan Entry value instead of overwriting it from the Generator.
- `actual_output_quantity` preserves Production Job `actual_qty_meter`, separately from planned quantity. Existing `produced_quantity` and `good_quantity` retain accepted-output semantics for WIP balances. Rejection, rework and usable/unusable scrap remain separate.
- Existing quantity UOM, employee, workstation, completion timestamps, prior lot and available Quality Inspection references are retained.

There is no automatic historical backfill of the newly added planned/actual snapshot fields.

## Naming and display

Existing prefixes remain: `EXT`, `OPR`, `OFP`, `SLT`, `PCK`, and the existing operation acronym fallback. Frappe generates `<PREFIX>-<YEAR>-<FIVE DIGITS>`, for example `EXT-2026-00001`, `OPR-2026-00001`, and `PCK-2026-00001`. Existing site-config prefix overrides remain supported. Historical identifiers are not renamed.

The board retains Start, Pause, Complete and expand/collapse controls. Operation details show the non-stock batch ID and planned/actual quantities. Finished Goods names resolve from Item; Item Codes remain internal identifiers. The lot form displays recorded Compound/Base Colour input batches through the genealogy API.

## Stock and genealogy

Balances come only from append-only `SmartEdge WIP Ledger Entry` rows. Production output increases non-stock available WIP; consumption, rejection, scrap, rework and finished-goods transfer reduce it. Stage transfers do not create inventory.

Previous-operation lot links support forward and backward operation traversal. Input Compound/Base Colour batch links are created only from actual input movements captured from the submitted final manufacturing Stock Entry. Planned FIFO allocations are not relabelled as actual consumption before posting.

Final operation lots link to physical Finished Goods batches only when submitted output genealogy movements prove the relationship. These links carry the actual stock-event output quantity and are explicitly labelled event-level associations, not inferred per-lot split allocations. They do not change WIP balances or post stock. Stock genealogy can enter the operation chain in either direction; lot genealogy can continue into the existing physical-batch/raw-material tree without recursively re-entering operation attachments.

Only the existing authorized final manufacturing process posts finished inventory and consumes the prepared materials. No intermediate Material Receipt, Finished Goods Stock Entry, physical Batch or Work Order is introduced by lot creation. Existing duplicate-posting and explicit batch-allocation validation remain in place.

The Finished Goods Item must be batch-controlled before final physical posting; otherwise a configuration error is shown. This is not permission to bypass the existing explicit allocation rules or enable batch tracking automatically.

## Live configuration findings

On edge.com at verification time, Finished Item IT-000002 had only draft BOM BOM-IT-000002-001, with no operation rows, and was not batch-controlled. Its existing Generator previews contained three process-route operations. Existing production snapshots were preserved. An authoritative submitted Finished Goods BOM and reviewed batch configuration are required before the new BOM-driven workflow can be exercised on that Item. Neither its Item settings nor its BOM was changed automatically.

## Verification

164 Python tests and five JavaScript checks passed. New coverage includes a rollback-only three-operation BOM, exactly three Final EB plans, separate material groups, completion of all three operations, previous-lot links, forward/backward traversal, planned 500 versus actual 490/480/470 quantities, separate accepted output and scrap, duplicate completion protection, invalid-completion rejection, QC holds, recorded stock-evidence links, and UI display.

The full test run disabled database commits. All fixtures were rolled back. Item, BOM, Batch, Work Order, Stock Entry, Generator, Plan Entry, Production Job, Production Lot and WIP Ledger counts were unchanged after cleanup. Existing Generator, Job, Stock Entry and Batch records and modification timestamps were also verified unchanged.

Intermediate completion tests explicitly disabled final stock posting. Physical input/output batch-link tests used mocked submitted-movement evidence. No live manufacturing Stock Entry was submitted and no real inventory batch was created. A full browser manufacturing run with actual physical posting still requires the reviewed live configuration and explicit transaction authorization.

## Files changed for this correction

Paths are relative to `apps/smart_edge_custom/`:

- `smart_edge_custom/manufacturing/final_bom_generator.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/manufacturing/production_lot.py`
- `smart_edge_custom/manufacturing/batch_genealogy.py`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.js`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.json`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.py`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_lot/smartedge_production_lot.json`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_lot/smartedge_production_lot.js`
- `smart_edge_custom/smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js`
- `smart_edge_custom/smart_edge_custom/page/smartedge_batch_genealogy/smartedge_batch_genealogy.js`
- `smart_edge_custom/test_finished_operation_batches.py`
- `smart_edge_custom/test_finished_operation_display.js`
- `smart_edge_custom/test_planning_board_grouping.py`
- `smart_edge_custom/test_generator_item_links.py`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/test_final_eb_bom_generator.py`
- `docs/production_lot_wip_ledger.md`

The app-owned Generator and Production Lot DocType metadata were reloaded on edge.com. Other installations should synchronize the updated DocType JSON through their normal deployment migration. No historical data migration or backfill was run.

## Changed function references

- `final_bom_generator.py`: `get_finished_bom_operations`, `get_route_operations`, `calculate_bom_preview`, `apply_preview_to_doc`, `validate_ready_for_planning`, `create_planning_entries`.
- `planning.py`: `get_planning_execution_key`, `assign_planning_execution_keys`, `get_plan_entries`, `get_generator_map`, `make_operation_card`, `get_finished_stock_row`, `complete_production_job`, `get_open_job`.
- `production_lot.py`: `is_qc_operation`, `get_operation_lot_mode`, `record_completed_job`, `record_qc_hold`, `attach_lot_details`, `reconcile_final_production`, `link_final_stock_batches`, `get_production_lot_genealogy`, `build_lot_genealogy_node`, `get_input_material_batch_details`.
- `batch_genealogy.py`: `get_genealogy`.
- `SmartEdgeProductionJob`: `complete_job`.
- Client changes cover Generator BOM reference reset/preview application, existing board lot details, existing genealogy rendering, and the Production Lot form's input-batch display.
