# Phase 6 Point 5 — Scrap and Recovery Analytics

## Report location

The standard Frappe Script Report **Scrap and Recovery Analytics** is available from:

`SmartEdge Manufacturing → Reports / Utilities → Scrap and Recovery Analytics`

It is read-only and is permitted to Manufacturing User and Manufacturing Manager, matching the existing Production Job report permissions and remaining subject to Company and user permissions.

## Authoritative data sources

- **SmartEdge Production Job**: completed-job production quantity, usable scrap, unusable scrap, operation, actual Workstation, actual Shift, Employee, and execution timestamps. Each Job is loaded once, so ledger and Stock Entry copies cannot inflate generated scrap.
- **Final EB BOM Generator / Production Plan Entry**: actual source production Item. Required-material Jobs use their material Item; Final EB Jobs use the generator finished Item.
- **SmartEdge Recovery Material / Item UOM conversions**: configured usable/unusable/recovered Item identities and authoritative stock-UOM-to-Kg conversion.
- **SmartEdge Usable Scrap Lot / Unusable Scrap Lot**: source Batch/Production Lot and storage traceability.
- **SmartEdge Scrap Recovery**: Grinder input/output, Pulverizer input/output, recovered output, QC requirement, recovered Batch, and submitted Pulverizer Stock Entry.
- **Quality Inspection**: submitted Accepted recovery release through the report To Date.
- **SmartEdge Recovered Material Allocation**: actual consumed allocation quantity, included only when its consumption Stock Entry remains submitted through the To Date.
- **Stock Entry / Stock Entry Detail**: submitted storage and disposal evidence. Draft, cancelled, and future transactions are excluded.

No reporting DocType or duplicate movement ledger was created.

## KPI formulas

- `Total Scrap Generated = Usable Scrap Generated + Unusable Scrap Generated`
- `Recovery % = Usable Scrap Generated / Total Scrap Generated × 100`
- `Unusable Scrap % = Unusable Scrap Generated / Total Scrap Generated × 100`
- `Recovery Utilization % = Recovered Material Reused / Released Recovery Cohort × 100`

A zero denominator or incomplete required Kg conversion displays **N/A**. It never becomes zero by assumption.

## Recovery-utilization period basis

Utilization uses a **source-production cohort** basis. The report first selects Production Jobs by their actual production date. It then follows only the recovery records originating from those Jobs:

- Opening recovered cohort: `0` by definition for the selected source cohort.
- Recovered material produced: submitted Pulverizer output from the selected Jobs through To Date.
- Newly released recovery: produced material that either did not require QC or had a submitted Accepted Quality Inspection through To Date.
- Reused: submitted `Consumed` allocations from those exact recovery records through To Date.
- Closing cohort: newly released recovery minus submitted reuse.

This basis deliberately prevents consumption of an older, unrelated Batch from being divided by current-period recovery production. It also permits reliable attribution back to the original Workstation, product, Shift, and operation. Rule-specific QC requirements cannot be treated as a universal release condition; the cohort uses the recovery mapping's QC requirement, while actual reuse remains proven by submitted allocations.

## UOM handling

The report uses `actual_qty_kg` as the authoritative production quantity. Scrap and recovery quantities are converted to Kg only when:

- the Item stock UOM is Kg; or
- the Item has an explicit Kg UOM Conversion Detail.

For a Kg conversion factor, `Kg quantity = stock quantity / conversion factor`. Meter is never inferred as Kg. Detail mode retains the original quantity and stock UOM. When a required conversion is missing, the affected Kg total and percentage are N/A and the report displays a warning.

## Grouping

- **Workstation**: actual source Production Job Workstation. Grinder/Pulverizer Workstations do not replace the scrap-generating machine.
- **Product**: required-material Item or Final EB finished Item, with Item name.
- **Shift**: actual Production Job Shift plus production date. Overnight Shifts use the configured Shift Type start/end times; after-midnight execution remains on the prior Shift production date.
- **Operation**: actual Production Job operation.
- **Date**: calculated production date.
- **Production Job**: detailed traceability and clickable source records.

Missing Workstation, Shift, or operation values remain visible as **Unassigned**. Missing products remain **Unknown**.

## Filters and drill-down

Filters include From Date, To Date, Company, Workstation, Finished Item/Product, Shift, Operation, Scrap Type, Batch/Production Lot, and one Group By selector. The default period is the current month and report ranges are capped at 366 days.

Production Job detail provides links to the Production Job, Item, Production Lot, source Batch, usable/unusable scrap lots, recovery record, recovery Stock Entry, and storage Stock Entry. Recovery quantities are attributed only through their actual source lot and reuse allocation, so mixed sources are never copied to every product.

## Performance and reconciliation

- The Production Job query always includes Company, Completed status, and a bounded date range.
- Related records are fetched in batches, not once per Job.
- One-to-many recovery and disposal records are aggregated before attaching to each Job.
- Submitted Stock Entry name sets prevent duplicate movement counting.
- Group totals are sums of the same one-row-per-Job detail dataset, so Workstation, product, Shift, operation, and date totals reconcile with the overall totals.

## Files changed for Point 5

- `smart_edge_custom/smart_edge_custom/report/__init__.py`
- `smart_edge_custom/smart_edge_custom/report/scrap_and_recovery_analytics/__init__.py`
- `smart_edge_custom/smart_edge_custom/report/scrap_and_recovery_analytics/scrap_and_recovery_analytics.json`
- `smart_edge_custom/smart_edge_custom/report/scrap_and_recovery_analytics/scrap_and_recovery_analytics.js`
- `smart_edge_custom/smart_edge_custom/report/scrap_and_recovery_analytics/scrap_and_recovery_analytics.py`
- `smart_edge_custom/smart_edge_custom/workspace/smartedge_manufacturing/smartedge_manufacturing.json`
- `smart_edge_custom/test_scrap_recovery_analytics.py`
- `docs/phase_6_point_5_scrap_recovery_analytics.md`

## Validation and limitations

The analytics unit tests cover formulas, zero denominators, conversion safeguards, partial utilization, Workstation/product/Shift reconciliation, overnight Shifts, unassigned dimensions, source-cohort isolation, and date validation. Existing Points 1–4, QC, physical stock, production lot, and genealogy regression suites remain passing.

The report does not estimate Kg when conversion data is absent. Product/Workstation/Shift utilization is N/A when recovery cannot be traced to one source cohort; it is never fabricated. Historical records are read only.

No established business definition for "unusable scrap as a percentage of production" exists in the current implementation, so Point 5 does not invent one. The report labels only the required unusable share of total scrap.

No production completion, scrap calculation, Grinder, Pulverizer, reuse, storage, disposal, WIP, genealogy, QC, Work Order, BOM, FIFO, Stock Entry, or valuation code was changed for Point 5. Deployment requires an approved site migration and cache/asset refresh. No migration, build, deployment, commit, or push was performed.
