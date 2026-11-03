"""Opt-in physical WIP posting for operation Batches.

The feature is disabled unless ``smartedge_physical_operation_stock`` is set in
site config.  Normal traceability Batches and the live accounting flow are
unchanged by default.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate


def enabled():
	return cint(frappe.conf.get("smartedge_physical_operation_stock")) == 1


def _wip_warehouse(entry, company):
	warehouse = entry.get("wip_warehouse") or frappe.db.get_value("Company", company, "default_wip_warehouse")
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


def physical_input_batch(lot_name):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PHYSICAL, PURPOSE
	name = frappe.db.get_value("Batch", {LOT_LINK: lot_name, PURPOSE: PHYSICAL}, "name")
	if not name:
		frappe.throw(_("Production Lot {0} has no physical input Batch. Complete and post its predecessor first.").format(lot_name))
	return name


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
		row = stock_row(generator.finished_item, job.stock_posting_qty, _wip_warehouse(entry, generator.company))
		components.append(row)
		batches.append({batch: flt(row["qty"]) * flt(row["conversion_factor"])})
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
	if not enabled():
		return None
	from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry
	if get_previous_mandatory_entry(entry) and not lot.input_production_lot:
		frappe.throw(_("A downstream physical operation requires a verified input Production Lot; raw materials cannot be consumed again."))
	# Completion holds the Job lock as well; this also protects direct callers.
	frappe.db.get_value("SmartEdge Production Job", job.name, "name", for_update=True)
	for field in ("rejected_qty", "rework_qty", "usable_scrap", "unusable_scrap"):
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
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = job.company
	stock_entry.posting_date = nowdate()
	stock_entry.remarks = reference
	if lot.input_production_lot:
		stock_entry.purpose = "Repack"
		stock_entry.set_stock_entry_type()
		previous_batch = physical_input_batch(lot.input_production_lot)
		qty = flt(job.good_qty) or flt(job.actual_qty_meter)
		if qty <= 0:
			frappe.throw(_("Accepted operation quantity must be greater than zero."))
		for values in (stock_row(lot.finished_item, qty, wip), stock_row(lot.finished_item, qty, wip, inward=True)):
			stock_entry.append("items", values)
		stock_entry.items[1].is_finished_item = 1
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		_bundle(stock_entry, stock_entry.items[0], previous_batch, stock_entry.items[0].transfer_qty, wip, "Outward")
		_bundle(stock_entry, stock_entry.items[1], batch_name, stock_entry.items[1].transfer_qty, wip, "Inward")
	else:
		from smart_edge_custom.manufacturing.planning import get_final_component_batch_nos, get_final_stock_components
		generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
		qty = flt(job.good_qty) or flt(job.actual_qty_meter)
		components = get_final_stock_components(generator, qty, generator.source_warehouse)
		batch_nos = get_final_component_batch_nos(generator, components)
		stock_entry.purpose = "Manufacture"
		stock_entry.set_stock_entry_type()
		for component in components:
			stock_entry.append("items", component)
		output = stock_row(lot.finished_item, qty, wip, inward=True)
		output["is_finished_item"] = 1
		stock_entry.append("items", output)
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		from smart_edge_custom.manufacturing.planning import set_final_component_batch_bundles
		set_final_component_batch_bundles(stock_entry, batch_nos + [None])
		_bundle(stock_entry, stock_entry.items[-1], batch_name, stock_entry.items[-1].transfer_qty, wip, "Inward")
	stock_entry.save(ignore_permissions=True)
	stock_entry.submit()
	# Purpose is promoted only after ERPNext accepted the inventory transaction.
	batch = frappe.get_doc("Batch", batch_name)
	batch.custom_batch_purpose = "Physical Stock"
	batch.save(ignore_permissions=True)
	job.db_set("manufacturing_stock_entry", stock_entry.name, update_modified=False)
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	capture_stock_entry(stock_entry)
	return stock_entry.name
