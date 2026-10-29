# SmartEdge Production Lots and WIP Ledger

SmartEdge Production Lots are non-stock traceability records. They always reference the existing finished Item and never create an Item, ERPNext Batch, warehouse balance, or Stock Entry.

## Completion flow

- Required-material MIXING continues to use its submitted manufacturing Stock Entry and real ERPNext batches.
- Extrusion, printing, slitting, packing, and other output-producing route operations create one lot per Production Job.
- QC-only operations preserve the incoming lot and append QC hold, disposition, and release movements.
- Existing predecessor-release quantities are the transfer source of truth. A transfer is rejected when the source stage lacks sufficient recorded quantity.
- Stable movement keys and unique Production Job-to-lot linkage make completion and backfill idempotent.

Lot prefixes are `EXT`, `OPR`, `OFP`, `SLT`, and `PCK`; other operations use a deterministic acronym. Frappe naming generates `<PREFIX>-<YEAR>-<FIVE DIGITS>`. A site may override an exact operation or planning-category prefix with the `smartedge_production_lot_prefixes` JSON mapping in site config.

## Balances

Balances are derived from append-only `SmartEdge WIP Ledger Entry` rows. Production output increases available WIP; rejection, scrap, rework, and finished-goods transfer reduce it. Stage transfers move quantity between operations without changing total available WIP. QC holds and releases are tracked separately.

## Genealogy

The SmartEdge Batch Genealogy page accepts an ERPNext Batch, SmartEdge Production Lot, Production Job, or Final EB BOM Generator. ERPNext batches are labelled as physical stock batches; production lots are labelled as non-stock traceability. Submitted manufacturing Stock Entries remain the authoritative link to actual Compound/Shade/raw-material batches and final finished-goods posting.

## Controlled historical backfill

Backfill is never run automatically. An authorized user may invoke one completed job explicitly:

```bash
bench --site <site> execute smart_edge_custom.manufacturing.production_lot.backfill_completed_job --kwargs '{"production_job":"SE-JOB-..."}'
```

The command validates status, actual output, timestamps, employee, and workstation. Repeating it returns the same lot and does not duplicate movements.
