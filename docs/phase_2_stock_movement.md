# Phase 2 physical WIP stock movement

Phase 2 is an opt-in physical-stock layer over the existing Production Lot, WIP Ledger, and Batch Genealogy implementation. It does not back-post completed jobs or replace Phase 3 traceability.

## Activation and configuration

1. Run `bench --site <site> migrate` to add the route snapshot fields.
2. On each **SmartEdge Process Route** operation choose one policy:
   - **Non-Stock Operation**: trace the operation only. This is the migration default and is appropriate for QC/inspection.
   - **Stockable WIP Output**: manufacture the first physical stage from Compound/Shade, or repack an eligible upstream WIP batch.
   - **WIP Transfer / Processing**: consume the actual eligible upstream WIP quantity and repack the accepted output into the configured stage batch.
3. Set **Output WIP Warehouse** for every physical operation. The posting path validates that it is enabled, is not a group, and belongs to the Production Job company.
4. After route configuration has been reviewed in a test site, enable `smartedge_physical_operation_stock` in site config and restart the workers.

The final mandatory route operation is always treated as Finished Goods output; its physical-WIP policy is ignored. A route consumes WIP at final posting only when its saved Final EB BOM operation snapshot contains at least one mandatory physical stage. Routes with no physical stage retain the existing Compound/Shade-to-Finished-Goods posting.

## Accounting and traceability rules

- The existing Finished Item is reused. A Meter-to-stock-UOM conversion must exist when its stock UOM is not Meter.
- The first configured physical boundary consumes the existing FIFO-selected Compound and Shade batches and receives the operation's existing output lot/batch into its configured WIP warehouse.
- Later physical boundaries use a Repack entry: actual processed quantity is consumed from the resolved physical input batch and accepted good quantity is received into the existing output lot/batch.
- Final posting consumes the most recent physical batch (walking across intervening non-stock/QC lots) and does not consume Compound/Shade again.
- ERPNext Batch stock and submitted Stock Entries remain authoritative. Existing WIP Ledger rows are linked to the Stock Entry and physical batches; a duplicate lot/genealogy relationship is not created.
- Stable Production Job remarks, row locking, submitted-reference checks, and cancelled-reference checks make retry behavior idempotent.

## Deliberate safety limits

- Existing completed jobs remain historical traceability only. No migration posts stock or changes their Batches.
- A Batch split across several warehouses is not consumed automatically; one company warehouse must contain the requested quantity.
- Rejection, scrap, rework, or recovery on a physical operation is blocked until an approved Item/warehouse disposition is configured. The same Finished Item is not silently used as a different scrap identity.
- If a stage changes inventory identity, stock UOM, or physical form beyond the Finished Item's configured UOM conversion, configure a distinct semi-finished Item in a future reviewed extension; this implementation does not create one automatically.

## Historical reconciliation

Use a separately approved Stock Reconciliation/Stock Entry workflow backed by a physical count, identified Batch, warehouse, quantity, and valuation. Do not enable the feature and retry old completions: retry protection intentionally refuses to convert historical traceability Batches into physical stock.
