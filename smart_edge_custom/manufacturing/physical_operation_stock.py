"""Opt-in physical WIP posting for operation Batches.

The feature is disabled unless ``smartedge_physical_operation_stock`` is set in
site config.  Normal traceability Batches and the live accounting flow are
unchanged by default.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate
from decimal import Decimal, ROUND_HALF_UP

NON_STOCK = "Non-Stock Operation"
STOCKABLE_OUTPUT = "Stockable WIP Output"
WIP_PROCESSING = "WIP Transfer / Processing"
PHYSICAL_TYPES = {STOCKABLE_OUTPUT, WIP_PROCESSING}


def _posting_log(entry, reason, **details):
	"""Emit one compact diagnostic record for physical-posting decisions."""
	try:
		context = {
			"plan_entry": entry.get("name") if entry else None,
			"generator": entry.get("final_eb_bom_generator") if entry else None,
			"operation": entry.get("operation") if entry else None,
			"sequence": entry.get("sequence") if entry else None,
			"movement_type": movement_type(entry),
			"reason": reason,
			**details,
		}
		frappe.logger("smartedge.physical_operation_stock").info(context)
	except Exception:
		# Diagnostics must never affect completion or rollback behavior.
		pass


def enabled():
	return cint(frappe.conf.get("smartedge_physical_operation_stock")) == 1


def movement_type(entry):
	return entry.get("stock_movement_type") or NON_STOCK if entry else NON_STOCK


def physical_processing_mode(entry):
	"""Return the persisted route processing mode, preserving legacy semantics."""
	return (entry.get("physical_processing_mode") if entry else None) or "New Operation Batch Transformation"


@frappe.whitelist()
def diagnose_operation_posting(plan_entry=None, production_job=None):
	"""Return read-only evidence explaining an operation posting decision."""
	if not plan_entry and not production_job:
		frappe.throw(_("Provide a Plan Entry or Production Job for posting diagnostics."))
	if production_job and not plan_entry:
		plan_entry = frappe.db.get_value("SmartEdge Production Job", production_job, "plan_entry")
	if not plan_entry:
		return {"posting_required": False, "reason": "No Plan Entry is linked to the Production Job."}
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	job_name = production_job or entry.get("production_job") or frappe.db.get_value(
		"SmartEdge Production Job", {"plan_entry": entry.name, "status": ["!=", "Cancelled"]}, "name"
	)
	job = frappe.get_doc("SmartEdge Production Job", job_name) if job_name else None
	mode = physical_processing_mode(entry)
	movement = movement_type(entry)
	posting_required = False
	reason = None
	try:
		posting_required = bool(should_post_operation(entry))
	except Exception as exc:
		reason = str(exc)
	predecessor_batch = None
	output_batch = None
	if job:
		lot_name = job.get("output_production_lot")
		if lot_name:
			try:
				from smart_edge_custom.manufacturing.operation_batch import stock_batch_for_lot
				output_batch = stock_batch_for_lot(lot_name, getattr(job, "finished_item", None))
			except Exception:
				output_batch = None
		input_lot = job.get("input_production_lot")
		if input_lot:
			try:
				predecessor_batch = physical_input_batch(input_lot)
			except Exception as exc:
				reason = reason or str(exc)
	existing = []
	if job and job.get("manufacturing_stock_entry"):
		if frappe.db.get_value("Stock Entry", job.manufacturing_stock_entry, "docstatus") == 1:
			existing.append(job.manufacturing_stock_entry)
	qc_quantities = {"accepted_qty": None, "rework_qty": None, "rejected_qty": None}
	if str(entry.get("planning_category") or "").upper() == "QC":
		try:
			from smart_edge_custom.manufacturing.quality_control import aggregate
			qc = aggregate(entry.name)
			qc_quantities = {"accepted_qty": qc.accepted_qty, "rework_qty": qc.unresolved_rework_qty, "rejected_qty": qc.rejected_qty}
		except Exception as exc:
			reason = reason or str(exc)
	return {
		"plan_entry": entry.name,
		"planning_route": frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "selected_route"),
		"operation": entry.get("operation"),
		"operation_sequence": entry.get("sequence"),
		"planning_category": entry.get("planning_category"),
		"stock_movement_type": movement,
		"physical_processing_mode": mode,
		"wip_warehouse": entry.get("wip_warehouse"),
		"production_job": job.name if job else None,
		"source_production_lot": job.get("input_production_lot") if job else None,
		"output_production_lot": job.get("output_production_lot") if job else None,
		"predecessor_batch": predecessor_batch,
		"output_batch": output_batch,
		"posting_required": posting_required,
		"posting_function": "post_material_transfer_operation" if mode == "Same Batch Material Transfer" else "post_operation" if posting_required else None,
		"existing_submitted_stock_entries": existing,
		"qc_passed_quantity": qc_quantities["accepted_qty"],
		"qc_rework_quantity": qc_quantities["rework_qty"],
		"qc_rejected_quantity": qc_quantities["rejected_qty"],
		"reason": reason or ("Posting is not required for this saved route snapshot." if not posting_required else None),
	}


def should_post_operation(entry):
	"""Only an explicitly configured, non-final route stage creates physical WIP."""
	if not entry:
		return False
	production = entry.get("final_eb_bom_generator")
	if production:
		from smart_edge_custom.manufacturing.final_bom_generator import get_stored_stock_posting_mode

		mode = get_stored_stock_posting_mode(production)
		if mode == "Legacy Non-Stock":
			return False
		if mode == "Physical WIP":
			from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
			if _is_first_material_consumption_entry(entry) and _has_material_requirements(production):
				if movement_type(entry) not in PHYSICAL_TYPES:
					frappe.throw(_(
						"Physical WIP material-consuming operation {0} must use Stockable WIP Output "
						"or WIP Transfer / Processing in the locked route snapshot."
					).format(entry.get("operation") or entry.get("name") or _("current operation")))
				# post_operation() revalidates the persisted allocation rows and
				# raises the existing clear batch-count/stock errors when missing.
				return True
			if movement_type(entry) not in PHYSICAL_TYPES:
				# QC checkpoints and the final warehouse posting have their own
				# approved workflows. A QC checkpoint is allowed to remain non-stock
				# only when it has no physically posted predecessor. Once a predecessor
				# carries ledger-backed WIP, silently completing this row would strand
				# stock and make the next operation consume the wrong lot.
				is_qc = (entry.get("planning_category") or "").upper() == "QC"
				if is_qc and not _has_physical_predecessor(entry):
					return False
				if is_final_stock_posting_entry(entry):
					return False
				frappe.throw(_(
					"Physical WIP operation {0} has a non-physical route snapshot "
					"({1}); correct the route before completing it."
				).format(entry.get("operation") or entry.get("name") or _("current operation"), movement_type(entry)))
		else:
			if not production_uses_physical_stock(production):
				return False
	else:
		if not enabled():
			return False
		if movement_type(entry) not in PHYSICAL_TYPES:
			return False
	from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
	return not is_final_stock_posting_entry(entry)


def _has_physical_predecessor(entry):
	"""Return true only when the previous route stage has posted real stock.

	This is deliberately evidence-based: a predecessor Production Lot or Batch
	metadata record alone is insufficient.  The predecessor must have a submitted
	Stock Entry and a ledger-backed physical Batch, otherwise a genuine trace-only
	QC checkpoint retains its existing non-stock behavior.
	"""
	try:
		from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry
		previous = get_previous_mandatory_entry(entry)
		if not previous:
			return False
		job = frappe.db.get_value(
			"SmartEdge Production Job",
			{"plan_entry": previous.name, "status": "Completed"},
			["name", "output_production_lot", "manufacturing_stock_entry"],
			as_dict=True,
		)
		if not job or not job.output_production_lot or not job.manufacturing_stock_entry:
			return False
		if frappe.db.get_value("Stock Entry", job.manufacturing_stock_entry, "docstatus") != 1:
			return False
		return bool(_physical_batch_for_lot(job.output_production_lot))
	except Exception:
		# Route validation must not turn a trace-only QC row into a stock row just
		# because optional historical metadata is unavailable.
		return False


def _is_first_material_consumption_entry(entry):
	"""Resolve the first FG route stage without depending on its name."""
	from smart_edge_custom.manufacturing.planning import is_first_fg_operation_entry

	return is_first_fg_operation_entry(entry)


def _has_material_requirements(production):
	"""Detect configured raw materials without reading frontend-only values."""
	generator = frappe.get_doc("Final EB BOM Generator", production)
	return bool(
		generator.get("compound_item")
		or generator.get("base_colour_item")
		or flt(generator.get("compound_total_required"))
		or flt(generator.get("shade_total_required"))
	)


def production_uses_physical_stock(production):
	"""Resolve the locked parent mode; missing mode is never physical."""
	from smart_edge_custom.manufacturing.final_bom_generator import (
		get_stored_stock_posting_mode,
	)
	mode = get_stored_stock_posting_mode(production)
	if mode == "Physical WIP":
		return True
	if mode == "Legacy Non-Stock":
		return False
	return has_physical_posting_evidence(production)


def has_physical_posting_evidence(production):
	"""Recognize only already-submitted physical transactions for old records."""
	if not production:
		return False
	stock_entries = []
	try:
		final_entry = frappe.db.get_value(
			"Final EB BOM Generator", production, "final_manufacturing_stock_entry"
		)
		if final_entry:
			stock_entries.append(final_entry)
		for job in frappe.get_all(
			"SmartEdge Production Job",
			filters={"final_eb_bom_generator": production},
			fields=["manufacturing_stock_entry"],
			limit_page_length=0,
		):
			if job.manufacturing_stock_entry:
				stock_entries.append(job.manufacturing_stock_entry)
		for stock_entry in set(stock_entries):
			remarks = frappe.db.get_value("Stock Entry", stock_entry, "remarks") or ""
			purpose = frappe.db.get_value("Stock Entry", stock_entry, "purpose")
			if frappe.db.get_value("Stock Entry", stock_entry, "docstatus") == 1 and (
				remarks.startswith("SmartEdge physical operation ")
				or remarks.startswith("SmartEdge incremental Packing physical Repack ")
				or remarks.startswith("SmartEdge physical Packing roll ")
				or (stock_entry == final_entry and purpose == "Repack")
			):
				return True
	except Exception:
		return False
	return False


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
		stock = [row for row in (get_batch_qty(batch_no=batch) or []) if flt(row.qty) > 0]
		warehouses = list(dict.fromkeys(row.warehouse for row in stock if row.warehouse))
		if len(warehouses) == 1:
			warehouse = warehouses[0]
		elif len(warehouses) > 1:
			frappe.throw(_("Physical operation WIP Warehouse is ambiguous for predecessor Batch {0}; configure wip_warehouse on Plan Entry {1} or Work Order {2}.").format(batch, entry.name, entry.get("work_order") or _("not set")))
	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse
	if not warehouse:
		frappe.throw(_("Physical operation WIP Warehouse is required for Plan Entry {0}. Set its wip_warehouse field, the linked Work Order WIP Warehouse, or Company default_wip_warehouse before posting.").format(entry.name))
	warehouse = validate_stock_warehouse(warehouse, company, _("Physical operation WIP Warehouse"))
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


def _physical_batch_for_lot(lot_name):
	from smart_edge_custom.manufacturing.operation_batch import stock_batch_for_lot
	return stock_batch_for_lot(lot_name)


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
				if row.item_code == lot.finished_item and frappe.db.exists("Stock Ledger Entry", {"batch_no": row.batch_no, "is_cancelled": 0}):
					return row.batch_no
	if not name:
		frappe.throw(_("Predecessor Production Lot {0} has no transaction-backed physical output Batch. Complete and submit its predecessor operation first; no stock was fabricated.").format(lot_name))
	return name


def validate_physical_batch_for_production(batch_name, generator, lot_name=None):
	"""Reject a physically valid Batch that belongs to another production."""
	batch_item = frappe.db.get_value("Batch", batch_name, "item")
	if batch_item != generator.finished_item:
		frappe.throw(_("Physical Batch {0} belongs to Item {1}, not {2}.").format(batch_name, batch_item, generator.finished_item))
	batch_lot = frappe.db.get_value("Batch", batch_name, "custom_operation_lot")
	owner = frappe.db.get_value("SmartEdge Production Lot", batch_lot, "final_eb_bom_generator") if batch_lot else None
	if owner != generator.name:
		frappe.throw(_("Physical Batch {0} is not owned by production {1}; no cross-production WIP consumption is permitted.").format(batch_name, generator.name))
	return batch_name


def _batch_source(batch_no, item_code, required_stock_qty, company):
	"""Resolve one authoritative source warehouse with enough eligible Batch stock."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse
	# Pass the predecessor Item explicitly. Without this argument ERPNext can
	# return no warehouse rows for a Batch when the item is resolved through
	# Serial and Batch Bundle records.
	rows = get_batch_qty(batch_no=batch_no, item_code=item_code) or []
	candidates = []
	for row in rows:
		warehouse = row.get("warehouse")
		qty = flt(row.get("qty"))
		if warehouse and qty > 0 and frappe.db.get_value("Warehouse", warehouse, "company") == company:
			candidates.append((warehouse, qty))
	eligible = [(warehouse, qty) for warehouse, qty in candidates if qty + 0.000001 >= flt(required_stock_qty)]
	if len(eligible) != 1:
		# Keep warehouse/quantity diagnostics in server logs only; operators need
		# an actionable stock message rather than an internal resolver dump.
		frappe.logger("smartedge.physical_operation_stock").warning({
			"batch": batch_no, "item": item_code, "required_qty": flt(required_stock_qty),
			"company": company, "candidates": candidates,
		})
		frappe.throw(_("The previous operation's Batch has no available stock. Check its submitted Stock Entry."))
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
		validate_physical_batch_for_production(batch, generator, lot)
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


def _batch(lot, job, entry, batch_id, previous_entry):
	from smart_edge_custom.manufacturing.operation_batch import LOT_LINK, stock_batch_for_lot
	physical = stock_batch_for_lot(lot.name, lot.finished_item)
	if physical:
		return physical
	# A lot may have a legacy metadata-only Batch. Keep that historical record
	# intact and create a separate stock-backed Batch only after the new Stock
	# Entry succeeds; never convert a Batch that already has stock evidence.
	# An existing Batch with this lot link is only an identity hint.  Stock
	# eligibility is established by ledger/bundle evidence, never by its
	# informational purpose value.
	if not frappe.db.get_value("Item", lot.finished_item, "has_batch_no"):
		frappe.throw(_("Finished Goods Item {0} must be batch-controlled before physical operation posting.").format(lot.finished_item))
	previous_lot = (lot.input_production_lot if previous_entry else None) or (frappe.db.get_value(
		"SmartEdge Production Lot", {"production_job": entry.get("released_from_production_job")}, "name"
	) if previous_entry and entry.get("released_from_production_job") else None)
	doc = frappe.get_doc({
		"doctype": "Batch", "batch_id": batch_id, "item": lot.finished_item,
		"item_name": frappe.db.get_value("Item", lot.finished_item, "item_name"),
		"reference_doctype": "SmartEdge Production Job", "reference_name": job.name,
		"manufacturing_date": getdate(job.actual_end), "disabled": 0,
		# This Batch is created only inside the same transaction as the physical
		# Stock Entry. If posting fails, Frappe rolls back both records; a
		# persisted trace-only Batch is never used as physical stock.
		LOT_LINK: lot.name,
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


def resolve_packing_input_lot(job):
	input_lot = job.get("input_production_lot")
	if input_lot:
		return _latest_physical_successor_lot(input_lot, job.get("final_eb_bom_generator"))
	from smart_edge_custom.manufacturing.planning import is_mandatory_operation
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	candidate = entry.get("released_from_production_job")
	if candidate:
		input_lot = frappe.db.get_value("SmartEdge Production Job", candidate, "output_production_lot")
		if input_lot:
			return _latest_physical_successor_lot(input_lot, entry.final_eb_bom_generator)
	previous_entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"plan_type": entry.get("plan_type") or "Final EB",
			"sequence": ["<", entry.sequence],
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "operation_row", "sequence"],
		order_by="sequence desc",
		limit_page_length=50,
	)
	for previous in previous_entries:
		if not is_mandatory_operation(previous.operation_row):
			continue
		input_lot = frappe.db.get_value(
			"SmartEdge Production Job",
			{"plan_entry": previous.name, "status": "Completed"},
			"output_production_lot",
		)
		if input_lot:
			return _latest_physical_successor_lot(input_lot, entry.final_eb_bom_generator)
	return None


def _latest_physical_successor_lot(lot_name, production=None):
	"""Follow completed, transaction-backed successors without changing roll identity."""
	from smart_edge_custom.manufacturing.operation_batch import stock_batch_for_lot
	current = lot_name
	seen = set()
	while current and current not in seen:
		seen.add(current)
		children = frappe.get_all(
			"SmartEdge Production Lot",
			filters={"input_production_lot": current},
			fields=["name", "operation_sequence", "final_eb_bom_generator", "production_job"],
			order_by="operation_sequence asc, creation asc",
			limit_page_length=50,
		)
		eligible = []
		for child in children:
			if production and child.final_eb_bom_generator != production:
				continue
			job = frappe.db.get_value(
				"SmartEdge Production Job", child.production_job,
				["status", "manufacturing_stock_entry"], as_dict=True,
			) if child.production_job else None
			if not job or job.status != "Completed" or not job.manufacturing_stock_entry:
				continue
			if frappe.db.get_value("Stock Entry", job.manufacturing_stock_entry, "docstatus") != 1:
				continue
			if stock_batch_for_lot(child.name):
				eligible.append(child)
		if not eligible:
			break
		current = eligible[-1].name
	return current


def packing_stock_entry_reference(job_name, roll_name=None):
	"""Return the stable idempotency remark for a physical Packing receipt."""
	return f"SmartEdge physical Packing roll {roll_name}" if roll_name else f"SmartEdge incremental Packing physical Repack {job_name}"


def post_incremental_packing_repack(generator, job, output_row, batch_name=None, roll_name=None):
	"""Consume only one newly packed physical-WIP allocation into the FG Batch.

	This is deliberately separate from the legacy incremental Material Receipt
	path.  It is called only for an explicitly enabled physical route, so the
	current non-stock route retains its established accounting behavior.
	"""
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry

	reference = packing_stock_entry_reference(job.name, roll_name)
	existing = frappe.get_all(
		"Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]},
		fields=["name", "docstatus"],
	)
	if len(existing) > 1:
		frappe.throw(_("Multiple physical Packing receipts exist for {0}; review them before retrying.").format(job.name))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft physical Packing receipt {0} exists; review it before retrying.").format(existing[0].name))
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("The physical Packing receipt for {0} was cancelled; use the controlled amendment workflow.").format(job.name))

	job_qty = flt(output_row.get("qty")) or flt(job.good_qty) or flt(job.actual_qty_meter)
	if job_qty <= 0:
		frappe.throw(_("Packing Job {0} has no positive completed quantity.").format(job.name))
	input_lot = resolve_packing_input_lot(job)
	if not input_lot:
		frappe.throw(_("Physical Packing Job {0} requires a released input Production Lot.").format(job.name))

	physical_batch = physical_input_batch(input_lot)
	validate_physical_batch_for_production(physical_batch, generator, input_lot)
	input_row = stock_row(generator.finished_item, job_qty, "__source__")
	input_row["s_warehouse"] = _batch_source(
		physical_batch, generator.finished_item,
		flt(input_row["qty"]) * flt(input_row["conversion_factor"]), generator.company,
	)
	output = stock_row(generator.finished_item, job_qty, output_row.t_warehouse, inward=True)
	output["is_finished_item"] = 1

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = generator.company
	stock_entry.purpose = "Repack"
	stock_entry.set_stock_entry_type()
	stock_entry.posting_date = nowdate()
	stock_entry.set_posting_time = 1
	stock_entry.remarks = reference
	stock_entry.append("items", input_row)
	stock_entry.append("items", output)
	stock_entry.insert(ignore_permissions=True)
	validate_posted_precision(stock_entry)
	_bundle(stock_entry, stock_entry.items[0], physical_batch, stock_entry.items[0].transfer_qty,
		stock_entry.items[0].s_warehouse, "Outward")
	if batch_name:
		_bundle(stock_entry, stock_entry.items[1], batch_name, stock_entry.items[1].transfer_qty,
			stock_entry.items[1].t_warehouse, "Inward")
	stock_entry.save(ignore_permissions=True)
	stock_entry.submit()
	capture_stock_entry(stock_entry)
	if roll_name:
		from smart_edge_custom.manufacturing.roll_tracking import append_stock_movement_history
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		append_stock_movement_history(
			roll_name, operation=entry.operation, operation_sequence=entry.sequence,
			production_job=job.name, plan_entry=entry.name, quantity=job_qty,
			source_batch=physical_batch, output_batch=batch_name,
			source_warehouse=stock_entry.items[0].s_warehouse,
			target_warehouse=stock_entry.items[1].t_warehouse,
			stock_entry=stock_entry.name, movement_type="Packing", status="Submitted",
		)
	return stock_entry.name


def get_packing_fg_batch(generator):
	"""Return the one submitted FG Batch established by physical Packing."""
	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements

	job_names = frappe.get_all(
		"SmartEdge Production Job",
		filters={"final_eb_bom_generator": generator.name},
		pluck="name", limit_page_length=500,
	)
	if not job_names:
		return None
	remarks = [f"SmartEdge incremental Packing physical Repack {name}" for name in job_names]
	roll_names = frappe.get_all("SmartEdge Roll", filters={"packing_job": ["in", job_names]}, pluck="name", limit_page_length=1000)
	entries = frappe.get_all(
		"Stock Entry",
		filters={"docstatus": 1},
		or_filters=[
			{"remarks": ["in", remarks]},
			{"remarks": ["in", [f"SmartEdge physical Packing roll {name}" for name in roll_names]]},
		],
		fields=["name"], order_by="creation asc", limit_page_length=500,
	)
	batches = []
	for row in entries:
		stock_entry = frappe.get_doc("Stock Entry", row.name)
		for movement in extract_batch_movements(stock_entry):
			if movement.get("direction") == "Output" and movement.get("item_code") == generator.finished_item:
				batches.append(movement.get("batch_no"))
	unique = list(dict.fromkeys(batch for batch in batches if batch))
	if len(unique) > 1:
		frappe.throw(_("Physical Packing has multiple finished-goods Batches for {0}; review them before retrying.").format(generator.name))
	return unique[0] if unique else None


def _disposition_movement_quantity(production_lot, batch_name):
	"""Return submitted rejected-WIP quantity already explicitly disposed."""
	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements

	prefix = f"SmartEdge rejected WIP disposition {production_lot} — "
	total = 0
	for name in frappe.get_all("Stock Entry", filters={
		"remarks": ["like", f"{prefix}%"], "docstatus": 1,
	}, pluck="name"):
		stock_entry = frappe.get_doc("Stock Entry", name)
		for movement in extract_batch_movements(stock_entry):
			if movement["direction"] == "Input" and movement["batch_no"] == batch_name:
				total += flt(movement["quantity"])
	return total


def _validated_rejected_roll_quantity(production_lot, quality_inspection, rejected_rolls, physical_batch, quantity):
	"""Require QC-linked roll history before any rejected WIP can be issued."""
	if not quality_inspection or not rejected_rolls:
		frappe.throw(_("Rejected-WIP disposition requires a QC inspection and rejected roll quantities."))
	rows = frappe.parse_json(rejected_rolls) if isinstance(rejected_rolls, str) else rejected_rolls
	if not isinstance(rows, (list, tuple)):
		frappe.throw(_("Rejected roll quantities must be a list."))
	total = 0.0
	seen = set()
	for row in rows:
		roll_name = row.get("roll") or row.get("roll_id") or row.get("name")
		roll_qty = flt(row.get("quantity") or row.get("meter_quantity"))
		if not roll_name or roll_name in seen or roll_qty <= 0:
			frappe.throw(_("Each rejected roll must be unique and have a positive quantity."))
		seen.add(roll_name)
		roll = frappe.get_doc("SmartEdge Roll", roll_name)
		if roll.get("source_batch") != physical_batch:
			frappe.throw(_("Rejected roll {0} does not belong to physical Batch {1}.").format(roll_name, physical_batch))
		evidence = 0.0
		for history in roll.get("operation_history") or []:
			if history.get("related_quality_inspection") != quality_inspection:
				continue
			if history.get("event_type") not in {"QC Decision", "Re-QC Decision"}:
				continue
			try:
				details = frappe.parse_json(history.get("remarks") or "{}")
			except Exception:
				continue
			evidence += flt(details.get("rejected_meter_quantity"))
			if not details.get("rejected_meter_quantity") and history.get("new_status") == "Rejected":
				evidence += flt(history.get("new_meter_quantity"))
		if roll_qty > evidence + 0.000001:
			frappe.throw(_("Rejected quantity for roll {0} is not proven by QC inspection {1}.").format(roll_name, quality_inspection))
		total += roll_qty
	if abs(total - flt(quantity)) > 0.000001:
		frappe.throw(_("Rejected roll quantities do not equal the requested disposition quantity."))
	return total


def post_rejected_wip_disposition(production_lot, quantity, reason, request_id, quality_inspection=None, rejected_rolls=None):
	"""Explicitly issue rejected physical WIP after an authorized disposition.

	No QC decision calls this implicitly.  The caller must provide a stable
	request ID and reason, making the ERPNext Material Issue auditable and safe
	to retry without silently writing off rejected material.
	"""
	if not enabled():
		frappe.throw(_("Physical WIP is not enabled for this site."))
	if not str(reason or "").strip() or not str(request_id or "").strip():
		frappe.throw(_("A rejected-WIP disposition reason and request ID are required."))

	lot = frappe.get_doc("SmartEdge Production Lot", production_lot)
	frappe.db.get_value("SmartEdge Production Lot", production_lot, "name", for_update=True)
	rejected = flt(lot.get("rejected_quantity"))
	qty = normalize_posting_quantity(quantity)
	if rejected <= 0 or qty <= 0:
		frappe.throw(_("Production Lot {0} has no positive rejected quantity for disposition.").format(production_lot))
	physical_batch = physical_input_batch(production_lot)
	inspection_lot = frappe.db.get_value("Quality Inspection", quality_inspection, "custom_source_production_lot")
	if inspection_lot != production_lot:
		frappe.throw(_("Quality Inspection {0} is not linked to Production Lot {1}.").format(quality_inspection, production_lot))
	_validated_rejected_roll_quantity(production_lot, quality_inspection, rejected_rolls, physical_batch, qty)
	reference = f"SmartEdge rejected WIP disposition {production_lot} — {str(request_id).strip()[:140]}"
	existing = frappe.get_all("Stock Entry", filters={
		"remarks": reference, "docstatus": ["<", 2],
	}, fields=["name", "docstatus"])
	if len(existing) > 1:
		frappe.throw(_("Multiple rejected-WIP dispositions exist for request {0}.").format(request_id))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft rejected-WIP disposition {0} requires review.").format(existing[0].name))
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("Rejected-WIP disposition {0} was cancelled; use a controlled amendment workflow.").format(request_id))
	disposed = _disposition_movement_quantity(production_lot, physical_batch)
	row = stock_row(lot.finished_item, qty, "__source__")
	rejected_stock_qty = _round(rejected * flt(row["conversion_factor"]), effective_stock_precision())
	if disposed + flt(row["qty"]) * flt(row["conversion_factor"]) > rejected_stock_qty + 0.000001:
		frappe.throw(_("Rejected-WIP disposition would exceed the rejected quantity of {0} for Production Lot {1}.").format(rejected, production_lot))
	company = lot.company
	warehouse = _batch_source(
		physical_batch, lot.finished_item,
		flt(row["qty"]) * flt(row["conversion_factor"]), company,
	)
	row["s_warehouse"] = warehouse
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.purpose = "Material Issue"
	stock_entry.set_stock_entry_type()
	stock_entry.posting_date = nowdate()
	stock_entry.set_posting_time = 1
	stock_entry.remarks = reference + f" — QC {quality_inspection} — {str(reason).strip()}"
	for fieldname, value in {
		"custom_smartedge_source_production_lot": production_lot,
		"custom_smartedge_source_batch": physical_batch,
		"custom_smartedge_scrap_movement_type": "Rejected WIP Disposition",
	}.items():
		if stock_entry.meta.has_field(fieldname):
			stock_entry.set(fieldname, value)
	stock_entry.append("items", row)
	stock_entry.insert(ignore_permissions=True)
	validate_posted_precision(stock_entry)
	_bundle(stock_entry, stock_entry.items[0], physical_batch, stock_entry.items[0].transfer_qty, warehouse, "Outward")
	stock_entry.save(ignore_permissions=True)
	stock_entry.submit()
	capture_stock_entry(stock_entry)
	return stock_entry.name


def link_operation_posting(lot, job, stock_entry_name):
	"""Persist the submitted posting on both execution records for retries and QC."""
	if lot and getattr(lot, "meta", None) and lot.meta.has_field("stock_entry") and lot.get("stock_entry") != stock_entry_name and callable(getattr(lot, "db_set", None)):
		lot.db_set("stock_entry", stock_entry_name, update_modified=False)
	if job and job.get("manufacturing_stock_entry") != stock_entry_name and callable(getattr(job, "db_set", None)):
		job.db_set("manufacturing_stock_entry", stock_entry_name, update_modified=False)


def prevent_upstream_cancellation(doc, method=None):
	"""Require downstream stock transactions to be reversed first."""
	if not ((doc.get("remarks") or "").startswith("SmartEdge physical operation ") or (doc.get("remarks") or "").startswith("SmartEdge physical rework roll ")):
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


def _post_rollwise_first_operation(lot, job, entry, batch_id_factory):
	"""Compatibility wrapper for the retired roll-splitting first-operation path.

	Raw materials are intentionally consumed by the single aggregate physical
	operation posting.  Roll declarations remain traceability allocations against
	that shared output Batch; they must not create additional Manufacture entries.
	"""
	return post_operation(lot, job, entry, None, batch_id_factory)
	# The historical implementation below is retained in source for audit only;
	# it is unreachable and must not be called for new production.
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation
	from smart_edge_custom.manufacturing.planning import get_final_component_batch_nos, get_final_stock_components, get_finished_stock_row
	from smart_edge_custom.manufacturing.roll_tracking import ROLL
	declarations = list(job.get("roll_packing_details") or [])
	total = flt(job.good_qty) or flt(job.actual_qty_meter)
	if not declarations or total <= 0:
		frappe.throw(_("Positive Roll declarations are required before physical posting."))
	if any(flt(row.get("meter_quantity")) <= 0 for row in declarations) or abs(sum(flt(row.get("meter_quantity")) for row in declarations) - total) > 0.000001:
		frappe.throw(_("Declared Roll quantities must equal the accepted extrusion output."))
	generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
	wip = _wip_warehouse(entry, job.company, None)
	batch_name = _batch(lot, job, entry, batch_id_factory(lot, job, entry), previous_entry=None)
	frappe.db.set_value("SmartEdge Roll", {"source_production_job": job.name}, "source_batch", batch_name, update_modified=False)
	components = get_final_stock_components(generator, total, generator.source_warehouse)
	batch_groups = get_final_component_batch_nos(generator, components)
	posted = []
	component_used = [0.0 for _ in components]
	batch_used = [dict() for _ in components]
	for index, declaration in enumerate(declarations):
		roll_name = frappe.db.get_value(ROLL, {"source_production_job": job.name, "roll_sequence": int(declaration.get("roll_no"))}, "name")
		if not roll_name:
			frappe.throw(_("Roll {0} was not created before physical posting.").format(declaration.get("roll_no")))
		qty = flt(declaration.get("meter_quantity"))
		reference = f"SmartEdge physical operation roll {roll_name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		if existing:
			if existing[0].docstatus != 1:
				frappe.throw(_("Draft physical Roll Stock Entry {0} exists; review it before retrying.").format(existing[0].name))
			posted.append(existing[0].name)
			continue
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = job.company
		stock_entry.purpose = "Manufacture"
		stock_entry.set_stock_entry_type()
		stock_entry.posting_date = nowdate()
		stock_entry.set_posting_time = 1
		stock_entry.remarks = reference
		ratio = qty / total
		for component_index, (component, groups) in enumerate(zip(components, batch_groups)):
			row = dict(component)
			row["qty"] = (
				flt(component["qty"]) - component_used[component_index]
				if index == len(declarations) - 1
				else normalize_posting_quantity(flt(component["qty"]) * ratio)
			)
			component_used[component_index] += flt(row["qty"])
			stock_entry.append("items", row)
		output = get_finished_stock_row(generator, qty, wip)
		output["is_finished_item"] = 1
		stock_entry.append("items", output)
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		for component_index, groups in enumerate(batch_groups):
			if not groups:
				continue
			row = stock_entry.items[component_index]
			batch_quantities = {}
			for batch_no, allocated in groups.items():
				batch_quantities[batch_no] = (
					flt(allocated) - flt(batch_used[component_index].get(batch_no, 0))
					if index == len(declarations) - 1
					else normalize_posting_quantity(flt(allocated) * ratio)
				)
				batch_used[component_index][batch_no] = flt(batch_used[component_index].get(batch_no, 0)) + batch_quantities[batch_no]
			bundle = SerialBatchCreation({"item_code": row.item_code, "warehouse": row.s_warehouse, "posting_date": stock_entry.posting_date, "posting_time": stock_entry.posting_time, "voucher_type": stock_entry.doctype, "voucher_no": stock_entry.name, "voucher_detail_no": row.name, "qty": -flt(row.transfer_qty), "type_of_transaction": "Outward", "company": stock_entry.company, "do_not_submit": True}).make_serial_and_batch_bundle(batch_nos=batch_quantities)
			row.serial_and_batch_bundle = bundle.name
			row.use_serial_batch_fields = 0
		_bundle(stock_entry, stock_entry.items[-1], batch_name, stock_entry.items[-1].transfer_qty, wip, "Inward")
		stock_entry.save(ignore_permissions=True)
		stock_entry.submit()
		posted.append(stock_entry.name)
		if frappe.get_meta(ROLL, cached=True).has_field("packing_stock_entry"):
			frappe.db.set_value(ROLL, roll_name, "packing_stock_entry", stock_entry.name, update_modified=False)
		from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
		capture_stock_entry(stock_entry)
		# Persist the Roll-level movement alongside ERPNext's authoritative Stock
		# Entry.  This is audit/idempotency metadata; it never creates inventory.
		from smart_edge_custom.manufacturing.production_lot import record_movement
		record_movement(
			"Production Output", ("roll-output", job.name, entry.name, roll_name),
			job, entry, generator, quantity=qty, uom="Meter",
			destination_lot=lot.name, destination_batch=batch_name,
			destination_operation=entry.operation,
			roll=roll_name, operation_sequence=entry.sequence,
			stock_entry=stock_entry.name, destination_warehouse=wip,
			reference_doctype="Stock Entry", reference_name=stock_entry.name,
		)
		job.db_set("manufacturing_stock_entry", stock_entry.name, update_modified=False)
	if not posted:
		frappe.throw(_("No roll-specific physical Stock Entries were posted."))
	if lot.meta.has_field("stock_entry"):
		lot.db_set("stock_entry", posted[-1], update_modified=False)
	return posted[-1]


def _validated_rework_roll_quantity(job):
	"""Return the authorized quantity for a Roll-specific Rework job."""
	meta = getattr(job, "meta", None) if job else None
	roll_name = job.get("rework_roll") if meta and meta.has_field("rework_roll") else None
	if not roll_name:
		return None
	roll = frappe.get_doc("SmartEdge Roll", roll_name)
	if roll.source_production_job != job.get("source_production_job") and roll.source_production_job != job.get("rework_source_job"):
		# The source-job link is optional on older Rework jobs; the Roll link and
		# Production Lot validation below remain authoritative in that case.
		if job.get("input_production_lot") != roll.source_production_lot:
			frappe.throw(_("Rework Job {0} is not linked to Roll {1}.").format(job.name, roll_name))
	if roll.qc_status != "Rework Required" or roll.process_status != "In Rework":
		frappe.throw(_("Roll {0} is not currently authorized for Rework.").format(roll_name))
	quantity = flt(roll.current_meter_quantity)
	if quantity <= 0:
		frappe.throw(_("Roll {0} has no positive eligible Rework quantity.").format(roll_name))
	return quantity


def _physical_handoff_quantity(entry, job, rework_quantity=None):
	"""Return the quantity that this physical transaction may move.

	QC is a disposition checkpoint: when only part of the predecessor output is
	accepted, only that accepted quantity is released into the QC output Batch.
	The remainder stays in the predecessor Batch for its authorized Rework or
	rejection workflow.  Other operations retain the stricter actual-quantity
	behavior so process loss cannot be silently written off.
	"""
	if rework_quantity:
		return flt(rework_quantity)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry
	if is_qc_entry(entry):
		return flt(job.get("good_qty")) or flt(job.get("actual_qty_meter"))
	return flt(job.get("actual_qty_meter"))


def post_material_transfer_operation(lot, job, entry):
	"""Move eligible Rolls between warehouses while retaining their Batch.

	This path is opt-in through the saved route processing mode.  It never creates
	a new operation Batch and never consumes Compound/Base Colour.
	"""
	from smart_edge_custom.manufacturing.roll_tracking import append_stock_movement_history
	previous_plan = entry.get("released_from_plan_entry") or entry.get("name")
	rolls = frappe.get_all("SmartEdge Roll", filters={
		"source_plan_entry": previous_plan,
		"qc_status": "Passed",
	}, fields=["name", "current_meter_quantity", "source_batch", "finished_item"], order_by="roll_sequence asc")
	if not rolls:
		frappe.throw(_("No Passed Rolls are available for {0} material transfer.").format(entry.get("operation") or entry.get("name")))
	destination = entry.get("wip_warehouse") or entry.get("target_warehouse")
	if not destination:
		frappe.throw(_("{0} requires a destination WIP Warehouse for Roll material transfer.").format(entry.get("operation") or entry.get("name")))
	from smart_edge_custom.manufacturing.planning import validate_stock_warehouse, validate_stock_item
	destination = validate_stock_warehouse(destination, job.company, _("Roll Transfer Destination Warehouse"))
	posted = []
	for roll in rolls:
		roll_name = roll.get("name")
		qty = flt(roll.get("current_meter_quantity"))
		if qty <= 0 or not roll.get("source_batch"):
			continue
		item_code = roll.get("finished_item") or lot.finished_item
		item = validate_stock_item(item_code)
		factor = _required_transfer_factor(item_code)
		required_stock_qty = qty * factor
		batch = roll.get("source_batch")
		source = _batch_source(batch, item_code, required_stock_qty, job.company)
		reference = f"SmartEdge material transfer {job.name} {entry.name} {roll_name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		if existing:
			if existing[0].docstatus != 1:
				frappe.throw(_("Draft Roll material transfer {0} exists; review it before retrying.").format(existing[0].name))
			posted.append(existing[0].name)
			continue
		if source == destination:
			append_stock_movement_history(roll_name, operation=entry.operation, operation_sequence=entry.sequence,
				production_job=job.name, plan_entry=entry.name, quantity=qty, source_batch=batch,
				output_batch=batch, source_warehouse=source, target_warehouse=destination,
				movement_type="Material Transfer", status="No Transfer Required")
			continue
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = job.company
		stock_entry.purpose = "Material Transfer"
		stock_entry.set_stock_entry_type()
		stock_entry.posting_date = nowdate()
		stock_entry.set_posting_time = 1
		stock_entry.remarks = reference
		row = stock_entry.append("items", {"item_code": roll.finished_item or lot.finished_item, "qty": qty,
			"uom": "Meter", "stock_uom": item.stock_uom, "conversion_factor": factor,
			"s_warehouse": source, "t_warehouse": destination})
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		_bundle(stock_entry, row, batch, row.transfer_qty, source, "Outward")
		stock_entry.save(ignore_permissions=True)
		stock_entry.submit()
		from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
		capture_stock_entry(stock_entry)
		append_stock_movement_history(roll_name, operation=entry.operation, operation_sequence=entry.sequence,
			production_job=job.name, plan_entry=entry.name, quantity=qty, source_batch=batch,
			output_batch=batch, source_warehouse=source, target_warehouse=destination,
			stock_entry=stock_entry.name, movement_type="Material Transfer", status="Submitted")
		posted.append(stock_entry.name)
	if not posted and not rolls:
		frappe.throw(_("No eligible Roll material transfers were recorded."))
	if lot.meta.has_field("stock_entry") and posted:
		lot.db_set("stock_entry", posted[-1], update_modified=False)
	if posted:
		job.db_set("manufacturing_stock_entry", posted[-1], update_modified=False)
	return posted[-1] if posted else None


def post_roll_material_transfers_after_repack(stock_entry_name, job, entry, output_batch, receiving_warehouse):
	"""Move Repack output for eligible Rolls, preserving the output Batch.

	The operation Repack remains authoritative for Batch transformation. These
	optional transfers run only when Rolls have an explicitly selected destination
	warehouse, and are independently idempotent per Repack/Roll event.
	"""
	from smart_edge_custom.manufacturing.roll_tracking import append_stock_movement_history
	rolls = _eligible_operation_rolls(entry)
	if not rolls or not any(row.get("destination_warehouse") for row in rolls):
		return []
	from smart_edge_custom.manufacturing.planning import get_required_conversion_factor, validate_stock_item, validate_stock_warehouse
	posted = []
	for roll in rolls:
		destination = roll.get("destination_warehouse")
		if not destination:
			frappe.throw(_("Roll {0} requires a destination Warehouse for its post-operation transfer.").format(roll.get("name")))
		item_code = roll.get("finished_item") or job.get("finished_item")
		item = validate_stock_item(item_code)
		qty = flt(roll.get("current_meter_quantity"))
		if qty <= 0:
			continue
		destination = validate_stock_warehouse(destination, job.company, _("Roll Transfer Destination Warehouse"))
		reference = f"SmartEdge post-operation transfer {job.name} {entry.name} {roll.get('name')} {stock_entry_name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		if existing:
			if existing[0].docstatus != 1:
				frappe.throw(_("Draft Roll transfer {0} exists; review it before retrying.").format(existing[0].name))
			posted.append(existing[0].name)
			continue
		if receiving_warehouse == destination:
			append_stock_movement_history(roll.get("name"), operation=entry.operation,
				operation_sequence=entry.sequence, production_job=job.name, plan_entry=entry.name,
				quantity=qty, source_batch=output_batch, output_batch=output_batch,
				source_warehouse=receiving_warehouse, target_warehouse=destination,
				movement_type="Material Transfer", status="No Transfer Required")
			continue
		factor = get_required_conversion_factor(item_code, "Meter", item.stock_uom)
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = job.company
		stock_entry.purpose = "Material Transfer"
		stock_entry.set_stock_entry_type()
		stock_entry.posting_date = nowdate()
		stock_entry.set_posting_time = 1
		stock_entry.remarks = reference
		row = stock_entry.append("items", {"item_code": item_code, "qty": qty, "uom": "Meter",
			"stock_uom": item.stock_uom, "conversion_factor": factor,
			"s_warehouse": receiving_warehouse, "t_warehouse": destination})
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		_bundle(stock_entry, row, output_batch, row.transfer_qty, receiving_warehouse, "Outward")
		stock_entry.save(ignore_permissions=True)
		stock_entry.submit()
		from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
		capture_stock_entry(stock_entry)
		append_stock_movement_history(roll.get("name"), operation=entry.operation,
			operation_sequence=entry.sequence, production_job=job.name, plan_entry=entry.name,
			quantity=qty, source_batch=output_batch, output_batch=output_batch,
			source_warehouse=receiving_warehouse, target_warehouse=destination,
			stock_entry=stock_entry.name, movement_type="Material Transfer", status="Submitted")
		posted.append(stock_entry.name)
	return posted


@frappe.whitelist()
def reconcile_operation_roll_transfers(stock_entry, apply=False):
	"""Preview or explicitly repair missing Roll transfers for one submitted Repack.

	Preview is the default and never mutates inventory.  Apply is restricted to
	Manufacturing/System Managers and reuses the normal post-Repack handler, so
	its stable per-operation/per-Roll references and bundle validation remain the
	single posting path.  Existing Repack entries are never amended or recreated.
	"""
	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements

	stock = frappe.get_doc("Stock Entry", stock_entry)
	if stock.docstatus != 1 or stock.purpose != "Repack":
		frappe.throw(_("Only a submitted Repack Stock Entry can be reconciled."))
	job_name = frappe.db.get_value("SmartEdge Production Job", {"manufacturing_stock_entry": stock.name}, "name")
	if not job_name:
		remarks = str(stock.remarks or "")
		prefix = "SmartEdge physical operation "
		job_name = remarks[len(prefix):].strip() if remarks.startswith(prefix) else None
	if not job_name or not frappe.db.exists("SmartEdge Production Job", job_name):
		frappe.throw(_("Could not resolve the Production Job for Stock Entry {0}.").format(stock.name))
	job = frappe.get_doc("SmartEdge Production Job", job_name)
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	movements = extract_batch_movements(stock)
	outputs = [row for row in movements if row.get("direction") == "Output" and row.get("batch_no")]
	batch_names = list(dict.fromkeys(row["batch_no"] for row in outputs))
	if len(batch_names) != 1:
		frappe.throw(_("Stock Entry {0} must identify exactly one output Batch before Roll reconciliation.").format(stock.name))
	output_batch = batch_names[0]
	output_item = next((row.get("item_code") for row in outputs if row.get("batch_no") == output_batch), None)
	output_warehouses = list(dict.fromkeys(row.get("warehouse") for row in outputs if row.get("warehouse")))
	if len(output_warehouses) != 1:
		frappe.throw(_("Stock Entry {0} must identify exactly one output Warehouse before Roll reconciliation.").format(stock.name))
	receiving_warehouse = output_warehouses[0]
	rolls = _eligible_operation_rolls(entry)
	from erpnext.stock.doctype.batch.batch import get_batch_qty
	available_rows = [row for row in (get_batch_qty(batch_no=output_batch, item_code=output_item) or []) if flt(row.get("qty")) > 0]
	available = {row.get("warehouse"): flt(row.get("qty")) for row in available_rows if row.get("warehouse")}
	result = {
		"stock_entry": stock.name, "production_job": job.name, "plan_entry": entry.name,
		"operation": entry.operation, "output_batch": output_batch,
		"source_warehouse": receiving_warehouse, "available_output_stock": available.copy(),
		"rolls": [], "apply": bool(apply),
	}
	missing_total = 0.0
	for roll in rolls:
		qty = flt(roll.current_meter_quantity)
		destination = roll.get("destination_warehouse")
		reference = f"SmartEdge post-operation transfer {job.name} {entry.name} {roll.name} {stock.name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": 1}, pluck="name", limit_page_length=2)
		safe = bool(destination and qty > 0 and available.get(receiving_warehouse, 0) + 0.000001 >= qty)
		status = "Existing" if existing else ("Missing" if safe else "Blocked")
		result["rolls"].append({"roll": roll.name, "quantity": qty, "batch": output_batch,
			"source_warehouse": receiving_warehouse, "destination_warehouse": destination,
			"existing_transfers": existing, "reference": reference, "status": status})
		if not existing and safe:
			missing_total += qty
			available[receiving_warehouse] = available.get(receiving_warehouse, 0) - qty
	if apply:
		if frappe.session.user != "Administrator" and not set(frappe.get_roles(frappe.session.user)).intersection({"Manufacturing Manager", "System Manager"}):
			frappe.throw(_("Only Manufacturing Manager or System Manager may apply Roll transfer reconciliation."))
		blocked = [row for row in result["rolls"] if row["status"] == "Blocked"]
		if blocked:
			frappe.throw(_("Roll transfer reconciliation is blocked for: {0}.").format(", ".join(row["roll"] for row in blocked)))
		frappe.db.savepoint("roll_transfer_reconciliation")
		try:
			posted = post_roll_material_transfers_after_repack(stock.name, job, entry, output_batch, receiving_warehouse)
		except Exception:
			frappe.db.rollback(save_point="roll_transfer_reconciliation")
			raise
		result["posted"] = posted
	else:
		result["missing_quantity"] = missing_total
	return result


def _required_transfer_factor(item_code):
	from smart_edge_custom.manufacturing.planning import get_required_conversion_factor
	item = frappe.db.get_value("Item", item_code, ["stock_uom"], as_dict=True)
	return get_required_conversion_factor(item_code, "Meter", item.stock_uom if item else "Meter")


def _operation_roll_source_plans(entry):
	"""Return all upstream Plan Entries that can own this operation's Rolls."""
	plans = []
	current = entry
	seen = set()
	for _ in range(20):
		parent = current.get("released_from_plan_entry") if current else None
		if not parent or parent in seen:
			break
		seen.add(parent)
		plans.append(parent)
		current = frappe.db.get_value(
			"SmartEdge Production Plan Entry", parent,
			["name", "released_from_plan_entry"], as_dict=True,
		)
	return plans


def _eligible_operation_rolls(entry):
	"""Resolve Passed Rolls through the complete upstream operation chain."""
	plan_entries = _operation_roll_source_plans(entry)
	if not plan_entries:
		return []
	return frappe.get_all("SmartEdge Roll", filters={
		"source_plan_entry": ["in", plan_entries],
		"qc_status": "Passed",
		"process_status": ["in", ["Available for Packing", "Reserved for Packing", "Packed"]],
	}, fields=["name", "current_meter_quantity", "source_batch", "finished_item", "destination_warehouse"], order_by="roll_sequence asc")


def _post_rollwise_operation_repack(lot, job, entry, batch_name, physical_input_lot, wip, rolls):
	"""Post one Repack per eligible Roll into one shared output Batch."""
	previous_batch = physical_input_batch(physical_input_lot)
	generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
	validate_physical_batch_for_production(previous_batch, generator, physical_input_lot)
	item_code = lot.finished_item
	total = sum(flt(row.get("current_meter_quantity")) for row in rolls)
	expected = flt(job.good_qty) or flt(job.actual_qty_meter)
	if total <= 0 or abs(total - expected) > 0.000001:
		frappe.throw(_("Eligible Roll quantities ({0}) do not match the accepted operation quantity ({1}).").format(total, expected))
	posted = []
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	from smart_edge_custom.manufacturing.roll_tracking import append_stock_movement_history
	for roll in rolls:
		roll_name = roll.get("name")
		qty = flt(roll.get("current_meter_quantity"))
		reference = f"SmartEdge physical operation roll {job.name} {entry.name} {roll_name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		if existing:
			if existing[0].docstatus != 1:
				frappe.throw(_("Draft physical Roll Stock Entry {0} exists; review it before retrying.").format(existing[0].name))
			posted.append(existing[0].name)
			continue
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = job.company
		stock_entry.purpose = "Repack"
		stock_entry.set_stock_entry_type()
		stock_entry.posting_date = nowdate()
		stock_entry.set_posting_time = 1
		stock_entry.remarks = reference
		input_row = stock_row(item_code, qty, "__source__")
		input_row["s_warehouse"] = _batch_source(previous_batch, item_code, input_row["qty"] * input_row["conversion_factor"], job.company)
		stock_entry.append("items", input_row)
		output_row = stock_row(item_code, qty, wip, inward=True)
		output_row["is_finished_item"] = 1
		stock_entry.append("items", output_row)
		stock_entry.insert(ignore_permissions=True)
		validate_posted_precision(stock_entry)
		_bundle(stock_entry, stock_entry.items[0], previous_batch, stock_entry.items[0].transfer_qty, stock_entry.items[0].s_warehouse, "Outward")
		_bundle(stock_entry, stock_entry.items[1], batch_name, stock_entry.items[1].transfer_qty, wip, "Inward")
		stock_entry.save(ignore_permissions=True)
		stock_entry.submit()
		capture_stock_entry(stock_entry)
		append_stock_movement_history(roll_name, operation=entry.operation, operation_sequence=entry.sequence,
			production_job=job.name, plan_entry=entry.name, quantity=qty, source_batch=previous_batch,
			output_batch=batch_name, source_warehouse=input_row["s_warehouse"], target_warehouse=wip,
			stock_entry=stock_entry.name, movement_type="Repack", status="Submitted")
		posted.append(stock_entry.name)
	if posted:
		job.db_set("manufacturing_stock_entry", posted[-1], update_modified=False)
		if lot.meta.has_field("stock_entry"):
			lot.db_set("stock_entry", posted[-1], update_modified=False)
	return posted[-1]


def post_operation(lot, job, entry, batch_name, batch_id_factory):
	"""Post one physical operation output or a batch-aware handoff.

	Called only when the explicit feature flag is enabled.  Every transaction is
	identified by the Production Job and retries reuse the submitted reference.
	"""
	if not should_post_operation(entry):
		_posting_log(entry, "posting_not_required", production_job=job.get("name") if job else None)
		return None
	if physical_processing_mode(entry) == "Same Batch Material Transfer":
		return post_material_transfer_operation(lot, job, entry)
	from smart_edge_custom.manufacturing.quality_control import validate_downstream_quantity
	rework_quantity = _validated_rework_roll_quantity(job)

	validate_downstream_quantity(entry, rework_quantity or flt(job.actual_qty_meter))
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
	# Usable scrap remains in its existing recovery receipt. Unusable scrap is
	# appended below as a by-product of this same physical transaction.  A
	# Roll-specific Rework job is different: its QC-authorized Roll is the
	# disposition record and _validated_rework_roll_quantity() has already
	# verified the source job/lot, Roll state and positive eligible quantity.
	# Do not apply the parent-job rejected/rework guard to that narrowly scoped
	# transaction, otherwise an authorized Roll rework can never post.  Ordinary
	# production jobs still fail closed until their disposition is recorded.
	if rework_quantity is None:
		for field in ("rejected_qty", "rework_qty"):
			if flt(job.get(field)):
				frappe.throw(_("Physical operation {0} has {1}. Configure and validate its scrap/rework inventory disposition before posting.").format(job.name, field))
	wip = _wip_warehouse(entry, job.company, lot.input_production_lot if previous_entry else None)
	job_meta = getattr(job, "meta", None)
	rework_roll = job.get("rework_roll") if job_meta and job_meta.has_field("rework_roll") else None
	rework_quantity = _validated_rework_roll_quantity(job)
	reference = f"SmartEdge physical rework roll {rework_roll}" if rework_roll else f"SmartEdge physical operation {job.name}"
	existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
	if len(existing) > 1:
		frappe.throw(_("Multiple physical Stock Entries exist for {0}; review before retrying.").format(job.name))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft physical Stock Entry {0} exists; review it before retrying completion.").format(existing[0].name))
		link_operation_posting(lot, job, existing[0].name)
		_posting_log(entry, "reused_submitted_stock_entry", production_job=job.name, stock_entry=existing[0].name)
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("Physical posting for {0} was cancelled; use an approved recovery workflow.").format(job.name))
	batch_name = _batch(lot, job, entry, batch_id_factory(lot, job, entry), previous_entry=previous_entry)
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
	if physical_input_lot and not rework_roll:
		eligible_rolls = _eligible_operation_rolls(entry)
		if eligible_rolls:
			return _post_rollwise_operation_repack(
				lot, job, entry, batch_name, physical_input_lot, wip, eligible_rolls
			)
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovered_components
	recovered_components, recovered_batches, recovered_allocations = get_recovered_components(production_job=job.name)
	if physical_input_lot:
		stock_entry.purpose = "Repack"
		stock_entry.set_stock_entry_type()
		previous_batch = physical_input_batch(physical_input_lot)
		generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
		validate_physical_batch_for_production(previous_batch, generator, physical_input_lot)
		input_qty = _physical_handoff_quantity(entry, job, rework_quantity)
		output_qty = rework_quantity or flt(job.good_qty) or input_qty
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
		components = [normalize_stock_row(component) for component in components]
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
	if rework_roll and frappe.get_meta("SmartEdge Roll", cached=True).has_field("rework_stock_entry"):
		frappe.db.set_value("SmartEdge Roll", rework_roll, "rework_stock_entry", stock_entry.name, update_modified=False)
	_posting_log(
		entry,
		"submitted",
		production_job=job.name,
		stock_entry=stock_entry.name,
		output_batch=batch_name,
		input_lot=lot.input_production_lot,
		accepted_output=rework_quantity or flt(job.good_qty) or flt(job.actual_qty_meter),
	)
	if unusable_scrap_lot:
		from smart_edge_custom.manufacturing.unusable_scrap import mark_stored
		mark_stored([unusable_scrap_lot], stock_entry.name)
	from smart_edge_custom.manufacturing.scrap_recovery import mark_recovered_components_consumed
	mark_recovered_components_consumed(recovered_allocations, stock_entry.name)
	# Preserve operation identity while linking the transaction-backed output.
	try:
		has_related_batch_field = frappe.get_meta("Batch", cached=True).has_field("custom_related_fg_batch")
	except Exception:
		has_related_batch_field = False
	if has_related_batch_field:
		from smart_edge_custom.manufacturing.operation_batch import LOT_LINK
		for legacy_name in frappe.get_all("Batch", filters={LOT_LINK: lot.name}, pluck="name"):
			if legacy_name == batch_name:
				continue
			frappe.db.set_value("Batch", legacy_name, "custom_related_fg_batch", batch.name, update_modified=False)
	link_operation_posting(lot, job, stock_entry.name)
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	capture_stock_entry(stock_entry)
	_link_wip_ledger(job, lot, stock_entry.name, lot.input_production_lot,
		previous_batch if physical_input_lot else None, batch_name)
	# The Batch transformation is the mandatory QC/operation posting.  Roll
	# warehouse relocation is a subsequent, explicitly configured movement; a
	# relocation failure must not roll back an otherwise valid submitted Repack
	# and make the QC Batch appear not to exist.  The failure is retained in the
	# server log for controlled retry.
	try:
		post_roll_material_transfers_after_repack(stock_entry.name, job, entry, batch_name, wip)
	except Exception as exc:
		_posting_log(entry, "roll_transfer_failed_after_repack", production_job=job.name,
			stock_entry=stock_entry.name, error=str(exc))
		# A required relocation failure must not be hidden behind a successfully
		# submitted Repack.  The completion transaction owns the savepoint and can
		# safely retry the same idempotent operation after the blocker is fixed.
		raise
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
