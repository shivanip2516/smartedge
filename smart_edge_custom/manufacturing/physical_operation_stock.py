"""Physical WIP posting for operation Batches.

Physical posting is enabled by default for the application workflow.  Sites
that need to hold the legacy metadata-only behavior during a controlled
migration can explicitly set ``smartedge_physical_operation_stock`` to ``0``.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate
from decimal import Decimal, ROUND_HALF_UP


def enabled():
	return cint(frappe.conf.get("smartedge_physical_operation_stock", 1)) == 1


def _field_precision(doctype, fieldname):
	try:
		precision = frappe.get_precision(doctype, fieldname)
	except Exception:
		precision = None
	return int(precision) if precision is not None else None


def _round(value, precision):
	if precision is None:
		return flt(value)
	quantum = Decimal(1).scaleb(-precision)
	return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def effective_stock_precision():
	precisions = [
		_field_precision("Stock Entry Detail", "qty"),
		_field_precision("Stock Entry Detail", "transfer_qty"),
		_field_precision("Serial and Batch Entry", "qty"),
	]
	precisions = [precision for precision in precisions if precision is not None]
	return min(precisions) if precisions else None


def normalize_posting_quantity(quantity):
	precision = effective_stock_precision()
	posted = _round(quantity, precision)
	if posted <= 0:
		frappe.throw(_("The stock posting quantity {0} becomes zero at the effective ERPNext precision ({1}). Increase the operation quantity or review precision configuration.").format(quantity, precision if precision is not None else _("not available")))
	return posted


def normalize_stock_row(row):
	row["qty"] = normalize_posting_quantity(row.get("qty"))
	return row


def _wip_warehouse(entry, company, input_lot=None):
	"""Resolve one validated WIP warehouse for the complete physical route.

	Explicit plan/work-order/company configuration wins.  For downstream
	operations, when configuration is absent, a single warehouse with positive
	stock for the predecessor physical Batch is safe to inherit; ambiguous stock
	is rejected instead of risking a mismatched or negative balance.
	"""
	candidates = [entry.get("wip_warehouse")]
	if entry.get("work_order"):
		candidates.append(frappe.db.get_value("Work Order", entry.work_order, "wip_warehouse"))
	candidates.append(frappe.db.get_value("Company", company, "default_wip_warehouse"))
	warehouse = next((value for value in candidates if value), None)
	if not warehouse and company:
		abbr = frappe.db.get_value("Company", company, "abbr")
		if abbr:
			candidates.append(f"Work In Progress - {abbr}")
		warehouse = next((value for value in candidates if value), None)
	if not warehouse and company:
		# Only auto-select a WIP-named warehouse when it is the sole enabled leaf
		# candidate for this company. Never guess from Stores or FG warehouses.
		try:
			rows = frappe.get_all(
				"Warehouse",
				filters={"company": company, "is_group": 0, "name": ["like", "%Work In Progress%"]},
				fields=["name"], order_by="name asc", limit_page_length=10,
			)
		except Exception:
			rows = []
		try:
			has_disabled = frappe.get_meta("Warehouse", cached=True).has_field("disabled")
		except Exception:
			has_disabled = False
		if has_disabled:
			rows = [row for row in rows if not frappe.db.get_value("Warehouse", row.name, "disabled")]
		if len(rows) == 1:
			warehouse = rows[0].name
	if not warehouse and input_lot:
		from erpnext.stock.doctype.batch.batch import get_batch_qty
		batch = physical_input_batch(input_lot)
		item_code = frappe.db.get_value("Batch", batch, "item")
		stock = [row for row in (get_batch_qty(item_code=item_code) or []) if flt(row.qty) > 0]
		warehouses = list(dict.fromkeys(row.warehouse for row in stock if row.warehouse))
		if len(warehouses) == 1:
			warehouse = warehouses[0]
		elif len(warehouses) > 1:
			frappe.throw(_("Physical operation WIP Warehouse is ambiguous for predecessor Batch {0}; configure wip_warehouse on Plan Entry {1} or Work Order {2}.").format(batch, entry.name, entry.get("work_order") or _("not set")))
	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse
	if not warehouse:
		frappe.throw(_("Physical operation WIP Warehouse is required for Plan Entry {0}. Set its wip_warehouse field, the linked Work Order WIP Warehouse, or Company default_wip_warehouse before posting.").format(entry.name))
	warehouse = validate_stock_warehouse(warehouse, company, _("Physical operation WIP Warehouse"))
	if entry.get("wip_warehouse") != warehouse and entry.get("name"):
		try:
			meta = frappe.get_meta("SmartEdge Production Plan Entry", cached=True)
			has_wip_field = meta.has_field("wip_warehouse")
		except Exception:
			has_wip_field = False
		if has_wip_field:
			frappe.db.set_value("SmartEdge Production Plan Entry", entry.name, "wip_warehouse", warehouse, update_modified=False)
			entry.wip_warehouse = warehouse
		# Keep blank downstream entries on the same FG route aligned with the
		# warehouse where the first physical output was posted.
		try:
			linked_entries = frappe.get_all("SmartEdge Production Plan Entry", filters={
				"final_eb_bom_generator": entry.final_eb_bom_generator,
				"plan_type": entry.get("plan_type") or "Final EB", "status": ["!=", "Cancelled"],
				"wip_warehouse": ["is", "not set"],
			}, fields=["name"], limit_page_length=100)
		except Exception:
			linked_entries = []
		for row in linked_entries:
			frappe.db.set_value("SmartEdge Production Plan Entry", row.name, "wip_warehouse", warehouse, update_modified=False)
	return warehouse


def stock_row(item_code, quantity, warehouse, inward=False):
	"""Use the same Meter-to-stock conversion as final manufacturing."""
	from smart_edge_custom.manufacturing.planning import get_required_conversion_factor, validate_stock_item
	item = validate_stock_item(item_code)
	factor = get_required_conversion_factor(item_code, "Meter", item.stock_uom)
	if flt(quantity) <= 0:
		frappe.throw(_("Physical operation quantity must be greater than zero."))
	return {"item_code": item_code, "qty": normalize_posting_quantity(quantity), "uom": "Meter",
		"stock_uom": item.stock_uom, "conversion_factor": factor,
		"t_warehouse" if inward else "s_warehouse": warehouse}


def physical_input_batch(lot_name):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PHYSICAL, PURPOSE
	name = frappe.db.get_value("Batch", {LOT_LINK: lot_name, PURPOSE: PHYSICAL}, "name")
	if not name:
		# Recover the predecessor's actual output Batch from its submitted
		# transaction when an older lot was created before the custom lot link was
		# persisted.  This remains evidence-based; no Batch is fabricated.
		lot = frappe.get_doc("SmartEdge Production Lot", lot_name)
		stock_entry = frappe.db.get_value(
			"SmartEdge Production Job", lot.production_job, "manufacturing_stock_entry"
		) if lot.get("production_job") else None
		if stock_entry and frappe.db.get_value("Stock Entry", stock_entry, "docstatus") == 1:
			rows = frappe.get_all(
				"SmartEdge Batch Genealogy Movement",
				filters={"stock_entry": stock_entry, "direction": "Output", "source_status": "Active"},
				fields=["batch_no", "item_code"],
				limit_page_length=50,
			)
			for row in rows:
				if row.item_code == lot.finished_item and frappe.db.get_value("Batch", row.batch_no, PURPOSE) == PHYSICAL:
					return row.batch_no
	if not name:
		frappe.throw(_("Predecessor Production Lot {0} has no transaction-backed physical output Batch. Complete and submit its predecessor operation first; no stock was fabricated.").format(lot_name))
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
		row = stock_row(generator.finished_item, job.stock_posting_qty, _wip_warehouse(entry, generator.company, lot))
		components.append(row)
		batches.append({batch: flt(row["qty"]) * flt(row["conversion_factor"])})
	return components, batches


def _batch(lot, job, entry, batch_id):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PHYSICAL, PURPOSE
	physical = frappe.db.get_value("Batch", {LOT_LINK: lot.name, PURPOSE: PHYSICAL}, "name")
	if physical:
		return physical
	# A lot may have a legacy metadata-only Batch. Keep that historical record
	# intact and create a separate stock-backed Batch only after the new Stock
	# Entry succeeds; never convert a Batch that already has stock evidence.
	legacy = frappe.db.get_value("Batch", {LOT_LINK: lot.name}, ["name", PURPOSE], as_dict=True)
	if legacy and legacy.get(PURPOSE) not in (None, "", "Operation Traceability"):
		frappe.throw(_("Operation Batch {0} already exists with unsupported purpose {1}.").format(legacy.name, legacy.get(PURPOSE)))
	if not frappe.db.get_value("Item", lot.finished_item, "has_batch_no"):
		frappe.throw(_("Finished Goods Item {0} must be batch-controlled before physical operation posting.").format(lot.finished_item))
	from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry
	previous_entry = get_previous_mandatory_entry(entry)
	previous_lot = (lot.input_production_lot if previous_entry else None) or (frappe.db.get_value(
		"SmartEdge Production Lot", {"production_job": entry.get("released_from_production_job")}, "name"
	) if previous_entry and entry.get("released_from_production_job") else None)
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
	precision = _field_precision("Stock Entry Detail", "transfer_qty")
	for row in stock_entry.items:
		expected = _round(flt(row.qty) * flt(row.conversion_factor), precision)
		if abs(flt(row.transfer_qty) - expected) > 0.000000001:
			frappe.throw(_("ERPNext posted {0} {1}, but the normalized stock quantity is {2} {3}. Review the item conversion and effective Stock Entry precision before posting.").format(row.transfer_qty, row.stock_uom, expected, row.stock_uom))


def link_operation_posting(lot, job, stock_entry_name):
	"""Persist the submitted posting on both execution records for retries and QC."""
	if lot and getattr(lot, "meta", None) and lot.meta.has_field("stock_entry") and lot.get("stock_entry") != stock_entry_name and callable(getattr(lot, "db_set", None)):
		lot.db_set("stock_entry", stock_entry_name, update_modified=False)
	if job and job.get("manufacturing_stock_entry") != stock_entry_name and callable(getattr(job, "db_set", None)):
		job.db_set("manufacturing_stock_entry", stock_entry_name, update_modified=False)


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
	previous_entry = get_previous_mandatory_entry(entry)
	if previous_entry and not lot.input_production_lot:
		frappe.throw(_("A downstream physical operation requires a verified input Production Lot; raw materials cannot be consumed again."))
	if not previous_entry:
		generator_stock_entry = frappe.db.get_value(
			"Final EB BOM Generator", job.final_eb_bom_generator, "final_manufacturing_stock_entry"
		)
		if generator_stock_entry and frappe.db.get_value("Stock Entry", generator_stock_entry, "docstatus") == 1:
			frappe.throw(_("Cannot recover first operation {0}: final manufacturing Stock Entry {1} already consumed the raw materials. Review the existing transaction instead of reposting.").format(job.name, generator_stock_entry))
	# Completion holds the Job lock as well; this also protects direct callers.
	frappe.db.get_value("SmartEdge Production Job", job.name, "name", for_update=True)
	for field in ("rejected_qty", "rework_qty", "usable_scrap", "unusable_scrap"):
		if flt(job.get(field)):
			frappe.throw(_("Physical operation {0} has {1}. Configure and validate its scrap/rework inventory disposition before posting.").format(job.name, field))
	wip = _wip_warehouse(entry, job.company, lot.input_production_lot if previous_entry else None)
	reference = f"SmartEdge physical operation {job.name}"
	existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
	if len(existing) > 1:
		frappe.throw(_("Multiple physical Stock Entries exist for {0}; review before retrying.").format(job.name))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft physical Stock Entry {0} exists; review it before retrying completion.").format(existing[0].name))
		link_operation_posting(lot, job, existing[0].name)
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("Physical posting for {0} was cancelled; use an approved recovery workflow.").format(job.name))
	batch_name = _batch(lot, job, entry, batch_id_factory(lot, job, entry))
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = job.company
	stock_entry.posting_date = nowdate()
	stock_entry.remarks = reference
	if previous_entry:
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
		components = [normalize_stock_row(component) for component in components]
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
	# Preserve a historical trace-only Batch while linking it to the new
	# transaction-backed output when that optional field exists.
	try:
		has_related_batch_field = frappe.get_meta("Batch", cached=True).has_field("custom_related_fg_batch")
	except Exception:
		has_related_batch_field = False
	if has_related_batch_field:
		from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, PURPOSE, TRACE
		for legacy_name in frappe.get_all("Batch", filters={LOT_LINK: lot.name, PURPOSE: TRACE}, pluck="name"):
			frappe.db.set_value("Batch", legacy_name, "custom_related_fg_batch", batch.name, update_modified=False)
	link_operation_posting(lot, job, stock_entry.name)
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	capture_stock_entry(stock_entry)
	return stock_entry.name
