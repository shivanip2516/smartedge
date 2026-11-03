# Final EB BOM Generator Item-link migration

Implemented and verified on `edge.com` on 2026-10-30. All source changes are inside `apps/smart_edge_custom/`.

## Dependency audit

The Generator used legacy `compound` and `base_colour` document Links as inputs. Its existing `compound_item` and `base_colour_item` Item Links were read-only preview outputs. Controller validation, readiness checks, preview events, material requirements, and batch allocation refresh still read the legacy inputs. Batch weights came from custom master `total_kg`, even though material Work Orders already selected standard BOMs.

References were audited in Python, JavaScript, DocType definitions, fixtures, patches, planning, stock completion, FIFO, and genealogy. On edge.com there were no Generator Client Scripts or custom fields; its Property Setters only configure naming series. There were no existing Generator records.

The migration changes only references representing those Generator selections. Material-type strings, status/Work Order field prefixes, Item colour attributes, and the standalone master maintenance modules remain intact. Planning response keys named `compound` and `base_colour` remain compatible with the existing board UI, but their values now come from the Item fields.

Genealogy already derives actual relationships from submitted Stock Entries and Serial and Batch Bundles, not Generator master Links. No genealogy implementation changes were needed. Its submitted-movement, traversal, operation-context, and idempotency regression tests remain in the validation suite.

## Resulting behavior

- The existing editable Item Links are authoritative: `compound_item` selects exact Item Group `Compounds` (the site's configured name); `base_colour_item` selects exact `Base Colour`.
- Server validation checks groups and manufacturing eligibility. Conditional mandatory behavior for Sales Order placeholders is retained.
- The old Links are removed from active DocType metadata and field order. Their physical database columns are retained.
- Preview application no longer replaces selected Item values. Selection events trigger the existing preview flow.
- Existing BOM priority is reused: matching Item, submitted, active, `custom_batch = 1`, default first, then most recently modified. A missing valid BOM or nonpositive output quantity produces a validation error.
- Weight per batch comes from that BOM's output quantity in Item stock UOM. Existing conversion to Kg, batch-count, per-meter, weight-difference, mismatch acknowledgement, valuation, and availability formulas remain in place.
- ERPNext's existing Work Order mapper retrieves the selected recipe BOM's raw materials. No BOM creation routine or raw-material consumption formula was introduced.
- Material planning, readiness, stock completion, and FIFO refresh use the selected Items. Existing scheduling, grouping, operation sequence, stock posting, batch naming, and genealogy code paths are retained.

## Migration and rollback

`smart_edge_custom.patches.migrate_generator_material_items` is registered in `pre_model_sync`. It fills only empty Item Links, using existing explicit master Item fields (including the recorded `compound_name`/`colour_name` mapping) and verifying the Item Group. It does not infer mappings from master document names or similar text. Multiple valid candidates or missing mappings stop the migration with record-specific diagnostics before normal schema synchronization retires the fields. Already populated Item Links are never overwritten.

The patch uses direct field updates with `update_modified=False`; it does not save Generator controllers or touch manufacturing/stock transactions. Repeated execution is safe. Original Link values remain in the retained legacy columns. Restoring the previous app schema and reloading this DocType can expose them again; do not run destructive column trimming against them.

The targeted patch and Generator DocType reload were applied on edge.com. Zero production records required mapping. Both active Item fields remain Link fields to Item. Integration tests separately verified both legacy mappings, reruns, preservation of populated links, and missing/ambiguous mapping rejection.

## Files changed for this migration

Paths below are relative to `apps/smart_edge_custom/`:

- `smart_edge_custom/manufacturing/final_bom_generator.py`
- `smart_edge_custom/manufacturing/batch_manufacturing.py`
- `smart_edge_custom/manufacturing/material_production.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.py`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.js`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.json`
- `smart_edge_custom/smart_edge_custom/doctype/final_eb_bom_generator/test_final_eb_bom_generator.py`
- `smart_edge_custom/patches.txt`
- `smart_edge_custom/patches/migrate_generator_material_items.py`
- `smart_edge_custom/test_base_colour_uom.py`
- `smart_edge_custom/test_fifo_batch_selection.py`
- `smart_edge_custom/test_material_production.py`
- `smart_edge_custom/test_sales_order_planning.py`
- `smart_edge_custom/test_generator_item_links.py`
- `smart_edge_custom/test_generator_item_links.js`
- `docs/generator_item_link_migration.md`

Earlier BOM filtering, Process-field retirement, and automatic-quantity changes remain in the working tree and were not rewritten by this migration.

## Verification and limits

143 Python tests passed in the combined suite. Test database commits were disabled and all test data rolled back; the production Generator count remained zero. Coverage includes real Generator save/reload, Item-only preview, no runtime custom-master reads, BOM priority, raw-material retrieval into unsaved Work Orders, planning idempotency, quantity/UOM behavior, migration safety, and existing manufacturing/FIFO/board/genealogy regressions. Route fixtures were updated to use actual Item names, standard BOMs, and valid referenced masters; the High Gloss test now uses a High Gloss Finished Item without changing routing logic.

Generator client, BOM client, and Planning Board traversal JavaScript checks passed. Syntax checks and `git diff --check` passed.

The planning-release integration test isolates stock availability validation; the existing stock/batch regression tests use mocks. No real Stock Entry was submitted, no real batch completion was executed, and no full Start/Pause/Complete browser production run was performed. Those end-to-end checks, including actual backward/forward genealogy on newly manufactured inventory, still require a suitable production/test inventory scenario. No stock movements were fabricated.
