"""Opt-in physical WIP posting for operation Batches.

The feature is disabled unless ``smartedge_physical_operation_stock`` is set in
site config.  Normal traceability Batches and the live accounting flow are
unchanged by default.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

NON_STOCK = "Non-Stock Operation"
STOCKABLE_OUTPUT = "Stockable WIP Output"
WIP_PROCESSING = "WIP Transfer / Processing"
PHYSICAL_TYPES = {STOCKABLE_OUTPUT, WIP_PROCESSING}


def enabled():
	return cint(frappe.conf.get("smartedge_physical_operation_stock")) == 1


def movement_type(entry):
	return entry.get("stock_movement_type") or NON_STOCK


def should_post_operation(entry):
	"""Only an explicitly configured, non-final route stage creates physical WIP."""
	if not enabled() or movement_type(entry) not in PHYSICAL_TYPES:
		return False
	from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
	return not is_final_stock_posting_entry(entry)


def production_uses_physical_stock(production):
	"""Use the immutable generator operation snapshot, never an operation-name heuristic."""
	if not enabled():
		return False
	return bool(frappe.db.exists("Final EB BOM Operation", {
		"parent": production, "parenttype": "Final EB BOM Generator", "mandatory": 1,
		"stock_movement_type": ["in", sorted(PHYSICAL_TYPES)],
	}))


def _wip_warehouse(entry, company):
	warehouse = entry.get("wip_warehouse")
	if not warehouse:
		frappe.throw(_("Output WIP Warehouse is required for stockable operation {0}.").format(entry.get("operation")))
	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse
	return validate_stock_warehouse(warehouse, company, _("Physical operation WIP Warehouse"))


def stock_row(item_code, quantity, warehouse, inward=False):
	"""Use the same Meter-to-stock conversion as final manufacturing."""
	from smart_edge_custom.manufacturing.planning import get_required_conversion_factor, validate_stock_item
	item = validate_stock_item(item_code)
	factor = get_required_conversion_factor(item_code, "Meter", item.stock_uom)
	if flt(quantity) <= 0:
		frappe.throw(_("Physical operation quantity must be greater than zero."))
	return {"item_code": item_code, "qty": flt(quantity), "uom": "Meter",
		"stock_uom": item.stock_uom, "conversion_factor": factor,
		"t_warehouse" if inward else "s_warehouse": warehouse}


def _physical_batch_for_lot(lot_name):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PHYSICAL, PURPOSE
	return frappe.db.get_value("Batch", {LOT_LINK: lot_name, PURPOSE: PHYSICAL}, "name")


def find_physical_input_lot(lot_name):
	"""Walk through non-stock/QC lots to the latest physically posted predecessor."""
	seen = set()
	while lot_name:
		if lot_name in seen:
			frappe.throw(_("Production Lot ancestry contains a cycle at {0}.").format(lot_name))
		seen.add(lot_name)
		if _physical_batch_for_lot(lot_name):
			return lot_name
		lot_name = frappe.db.get_value("SmartEdge Production Lot", lot_name, "input_production_lot")
	return None


def physical_input_batch(lot_name):
	physical_lot = find_physical_input_lot(lot_name)
	name = _physical_batch_for_lot(physical_lot) if physical_lot else None
	if not name:
		frappe.throw(_("Production Lot {0} has no physical input Batch. Complete and post its predecessor first.").format(lot_name))
	return name


def _batch_source(batch_no, item_code, required_stock_qty, company):
	"""Resolve one authoritative source warehouse with enough eligible Batch stock."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse
	rows = get_batch_qty(batch_no=batch_no) or []
	candidates = []
	for row in rows:
		warehouse = row.get("warehouse")
		qty = flt(row.get("qty"))
		if warehouse and qty > 0 and frappe.db.get_value("Warehouse", warehouse, "company") == company:
			candidates.append((warehouse, qty))
	eligible = [(warehouse, qty) for warehouse, qty in candidates if qty + 0.000001 >= flt(required_stock_qty)]
	if len(eligible) != 1:
		frappe.throw(_("Physical Batch {0} requires {1} {2}, but a single eligible company warehouse could not be resolved. Available: {3}").format(
			batch_no, flt(required_stock_qty, 9), frappe.db.get_value("Item", item_code, "stock_uom"),
			", ".join(f"{warehouse}: {flt(qty, 9)}" for warehouse, qty in candidates) or _("none"),
		))
	return validate_stock_warehouse(eligible[0][0], company, _("Physical WIP Source Warehouse"))


def final_wip_components(generator, jobs):
	"""Final output consumes only existing WIP; raw materials are not repeated."""
	components, batches = [], []
	for job in jobs:
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		lot = job.get("input_production_lot")
		if not lot and entry.get("released_from_production_job"):
			lot = frappe.db.get_value("SmartEdge Production Job", entry.released_from_production_job, "output_production_lot")
		if not lot:
			frappe.throw(_("Final Job {0} requires a released physical input Production Lot.").format(job.name))
		batch = physical_input_batch(lot)
		row = stock_row(generator.finished_item, job.stock_posting_qty, "__source__")
		components.append(row)
		batches.append({batch: flt(row["qty"]) * flt(row["conversion_factor"])})
	requested = {}
	for batch_group in batches:
		for batch, quantity in batch_group.items():
			requested[batch] = flt(requested.get(batch)) + flt(quantity)
	sources = {batch: _batch_source(batch, generator.finished_item, quantity, generator.company)
		for batch, quantity in requested.items()}
	for component, batch_group in zip(components, batches, strict=True):
		component["s_warehouse"] = sources[next(iter(batch_group))]
	return components, batches


def _batch(lot, job, entry, batch_id):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PHYSICAL, PURPOSE
	if frappe.db.exists("Batch", {LOT_LINK: lot.name}):
		name, purpose = frappe.db.get_value("Batch", {LOT_LINK: lot.name}, ["name", PURPOSE])
		if purpose != PHYSICAL:
			frappe.throw(_("Operation Batch {0} already exists as {1}; historical traceability Batches are not converted automatically.").format(name, purpose or "unknown"))
		return name
	if not frappe.db.get_value("Item", lot.finished_item, "has_batch_no"):
		frappe.throw(_("Finished Goods Item {0} must be batch-controlled before physical operation posting.").format(lot.finished_item))
	previous_lot = lot.input_production_lot or (frappe.db.get_value(
		"SmartEdge Production Lot", {"production_job": entry.get("released_from_production_job")}, "name"
	) if entry.get("released_from_production_job") else None)
	doc = frappe.get_doc({
		"doctype": "Batch", "batch_id": batch_id, "item": lot.finished_item,
		"item_name": frappe.db.get_value("Item", lot.finished_item, "item_name"),
		"reference_doctype": "SmartEdge Production Job", "reference_name": job.name,
		"manufacturing_date": getdate(job.actual_end), "disabled": 0,
		PURPOSE: None, LOT_LINK: lot.name,
		"custom_operation_name": lot.operation, "custom_operation_sequence": lot.operation_sequence,
		"custom_operation_output_quantity": job.actual_qty_meter,
		"custom_operation_completed_at": job.actual_end,
		"custom_previous_operation_batch": frappe.db.get_value("Batch", {LOT_LINK: previous_lot}, "name") if previous_lot else None,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _bundle(stock_entry, row, batch_no, qty, warehouse, direction):
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation
	bundle = SerialBatchCreation({
		"item_code": row.item_code, "warehouse": warehouse,
		"posting_date": stock_entry.posting_date, "posting_time": stock_entry.posting_time,
		"voucher_type": stock_entry.doctype, "voucher_no": stock_entry.name,
		"voucher_detail_no": row.name, "qty": flt(qty) * (-1 if direction == "Outward" else 1),
		"type_of_transaction": direction, "company": stock_entry.company, "do_not_submit": True,
	}).make_serial_and_batch_bundle(batch_nos={batch_no: flt(qty)})
	row.serial_and_batch_bundle = bundle.name
	row.use_serial_batch_fields = 0


def validate_posted_precision(stock_entry):
	for row in stock_entry.items:
		expected = flt(row.qty) * flt(row.conversion_factor)
		if abs(flt(row.transfer_qty) - expected) > 0.000000001:
			frappe.throw(_("Stock precision rounds {0} {1} to {2} {3}. Configure sufficient Stock Entry and bundle quantity precision on the test site before enabling physical operation stock.").format(row.qty, row.uom, row.transfer_qty, row.stock_uom))


def prevent_upstream_cancellation(doc, method=None):
	"""Require downstream stock transactions to be reversed first."""
	if not (doc.get("remarks") or "").startswith("SmartEdge physical operation "):
		return
	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements
	outputs = [r["batch_no"] for r in extract_batch_movements(doc) if r["direction"] == "Output"]
	if not outputs:
		return
	dependent = frappe.get_all("SmartEdge Batch Genealogy Movement", filters={
		"batch_no": ["in", outputs], "direction": "Input", "source_status": "Active",
		"stock_entry": ["!=", doc.name]}, pluck="stock_entry")
	if dependent:
		frappe.throw(_("Cancel downstream Stock Entries first: {0}").format(", ".join(sorted(set(dependent)))))


def post_operation(lot, job, entry, batch_name, batch_id_factory):
	"""Post one physical operation output or a batch-aware handoff.

	Called only when the explicit feature flag is enabled.  Every transaction is
	identified by the Production Job and retries reuse the submitted reference.
	"""
	if not should_post_operation(entry):
		return None
	from smart_edge_custom.manufacturing.quality_control import validate_downstream_quantity

	validate_downstream_quantity(entry, flt(job.actual_qty_meter))
	from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry
	if get_previous_mandatory_entry(entry) and not lot.input_production_lot:
		frappe.throw(_("A downstream physical operation requires a verified input Production Lot; raw materials cannot be consumed again."))
	# Completion holds the Job lock as well; this also protects direct callers.
	frappe.db.get_value("SmartEdge Production Job", job.name, "name", for_update=True)
	# Usable scrap remains in its existing recovery receipt. Unusable scrap is
	# appended below as a by-product of this same physical transaction.
	for field in ("rejected_qty", "rework_qty"):
		if flt(job.get(field)):
			frappe.throw(_("Physical operation {0} has {1}. Configure and validate its scrap/rework inventory disposition before posting.").format(job.name, field))
	wip = _wip_warehouse(entry, job.company)
	reference = f"SmartEdge physical operation {job.name}"
	existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
	if len(existing) > 1:
		frappe.throw(_("Multiple physical Stock Entries exist for {0}; review before retrying.").format(job.name))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft physical Stock Entry {0} exists; review it before retrying completion.").format(existing[0].name))
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("Physical posting for {0} was cancelled; use an approved recovery workflow.").format(job.name))
	batch_name = _batch(lot, job, entry, batch_id_factory(lot, job, entry))
	from smart_edge_custom.manufacturing.unusable_scrap import prepare_unusable_scrap
	unusable_scrap_lot = prepare_unusable_scrap(job, lot, batch_name)
	if unusable_scrap_lot:
		unusable = frappe.get_doc("SmartEdge Unusable Scrap Lot", unusable_scrap_lot)
		if not unusable.source_batch:
			unusable.db_set("source_batch", batch_name, update_modified=False)
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = job.company
	stock_entry.posting_date = nowdate()
	stock_entry.remarks = reference
	if unusable_scrap_lot:
		from smart_edge_custom.manufacturing.unusable_scrap import set_storage_metadata
		set_storage_metadata(stock_entry, [unusable_scrap_lot])
	physical_input_lot = find_physical_input_lot(lot.input_production_lot)
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovered_components
	recovered_components, recovered_batches, recovered_allocations = get_recovered_components(production_job=job.name)
	if physical_input_lot:
		stock_entry.purpose = "Repack"
		stock_entry.set_stock_entry_type()
		previous_batch = physical_input_batch(physical_input_lot)
		input_qty = flt(job.actual_qty_meter)
		output_qty = flt(job.good_qty) or input_qty
		if input_qty <= 0 or output_qty <= 0 or output_qty > input_qty + 0.000001:
			frappe.throw(_("Accepted operation quantity must be greater than zero."))
		if abs(input_qty - output_qty) > 0.000001:
			frappe.throw(_("Physical operation input and accepted output differ. Configure and validate the loss/scrap inventory disposition before posting."))
		input_row = stock_row(lot.finished_item, input_qty, "__source__")
		input_row["s_warehouse"] = _batch_source(
			previous_batch, lot.finished_item,
			flt(input_row["qty"]) * flt(input_row["conversion_factor"]), job.company,
		)
		stock_entry.append("items", input_row)
		for component in recovered_components:
			stock_entry.append("items", component)
		stock_entry.append("items", stock_row(lot.finished_item, output_qty, wip, inward=True))
		stock_entry.items[-1].is_finished_item = 1
		from smart_edge_custom.manufacturing.unusable_scrap import append_storage_output
		unusable_pair = append_storage_output(stock_entry, unusable_scrap_lot) if unusable_scrap_lot else None
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		_bundle(stock_entry, stock_entry.items[0], previous_batch, stock_entry.items[0].transfer_qty, stock_entry.items[0].s_warehouse, "Outward")
		for row, batch_group in zip(stock_entry.items[1:1 + len(recovered_batches)], recovered_batches, strict=True):
			_bundle(stock_entry, row, next(iter(batch_group)), row.transfer_qty, row.s_warehouse, "Outward")
		output_row = next(row for row in stock_entry.items if row.get("is_finished_item"))
		_bundle(stock_entry, output_row, batch_name, output_row.transfer_qty, wip, "Inward")
		from smart_edge_custom.manufacturing.unusable_scrap import attach_storage_bundle
		attach_storage_bundle(stock_entry, unusable_pair)
	else:
		from smart_edge_custom.manufacturing.planning import get_final_component_batch_nos, get_final_stock_components
		generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
		qty = flt(job.good_qty) or flt(job.actual_qty_meter)
		components = get_final_stock_components(generator, qty, generator.source_warehouse)
		batch_nos = get_final_component_batch_nos(generator, components)
		components.extend(recovered_components)
		batch_nos.extend(recovered_batches)
		stock_entry.purpose = "Manufacture"
		stock_entry.set_stock_entry_type()
		for component in components:
			stock_entry.append("items", component)
		output = stock_row(lot.finished_item, qty, wip, inward=True)
		output["is_finished_item"] = 1
		stock_entry.append("items", output)
		from smart_edge_custom.manufacturing.unusable_scrap import append_storage_output
		unusable_pair = append_storage_output(stock_entry, unusable_scrap_lot) if unusable_scrap_lot else None
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		from smart_edge_custom.manufacturing.planning import set_final_component_batch_bundles
		set_final_component_batch_bundles(stock_entry, [*batch_nos, None])
		output_row = next(row for row in stock_entry.items if row.get("is_finished_item"))
		_bundle(stock_entry, output_row, batch_name, output_row.transfer_qty, wip, "Inward")
		from smart_edge_custom.manufacturing.unusable_scrap import attach_storage_bundle
		attach_storage_bundle(stock_entry, unusable_pair)
	stock_entry.save(ignore_permissions=True)
	stock_entry.submit()
	if unusable_scrap_lot:
		from smart_edge_custom.manufacturing.unusable_scrap import mark_stored
		mark_stored([unusable_scrap_lot], stock_entry.name)
	from smart_edge_custom.manufacturing.scrap_recovery import mark_recovered_components_consumed
	mark_recovered_components_consumed(recovered_allocations, stock_entry.name)
	# Purpose is promoted only after ERPNext accepted the inventory transaction.
	batch = frappe.get_doc("Batch", batch_name)
	batch.custom_batch_purpose = "Physical Stock"
	batch.save(ignore_permissions=True)
	job.db_set("manufacturing_stock_entry", stock_entry.name, update_modified=False)
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	capture_stock_entry(stock_entry)
	_link_wip_ledger(job, lot, stock_entry.name, lot.input_production_lot,
		previous_batch if physical_input_lot else None, batch_name)
	return stock_entry.name


def _link_wip_ledger(job, lot, stock_entry, input_lot, input_batch, output_batch):
	"""Attach physical evidence to existing Phase 3 movements; create no second relation."""
	from smart_edge_custom.manufacturing.production_lot import LEDGER_DOCTYPE
	common = {"reference_doctype": "Stock Entry", "reference_name": stock_entry}
	frappe.db.set_value(LEDGER_DOCTYPE, {"production_job": job.name, "movement_type": "Production Output",
		"destination_production_lot": lot.name}, {**common, "destination_stock_batch": output_batch}, update_modified=False)
	if input_lot and input_batch:
		frappe.db.set_value(LEDGER_DOCTYPE, {"production_job": job.name, "movement_type": "Consume for Operation",
			"source_production_lot": input_lot}, {**common, "source_stock_batch": input_batch}, update_modified=False)
		frappe.db.set_value(LEDGER_DOCTYPE, {"production_job": job.name, "movement_type": "Lot Genealogy Link",
			"source_production_lot": input_lot, "destination_production_lot": lot.name},
			{**common, "source_stock_batch": input_batch, "destination_stock_batch": output_batch}, update_modified=False)
