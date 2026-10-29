import hashlib
import json
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

LOT_DOCTYPE = "SmartEdge Production Lot"
LEDGER_DOCTYPE = "SmartEdge WIP Ledger Entry"
ACTIVE = "Active"
BALANCE_OUT_TYPES = {
	"Consume for Operation",
	"Rejection",
	"Scrap",
	"Rework",
	"Transfer to Finished Goods",
}
STAGE_TRANSFER_TYPES = {"Transfer to Next Operation", "QC Release"}


def get_operation_lot_mode(entry):
	if entry.get("plan_type") == "Required Material":
		return "Stock Batch"
	operation = str(entry.get("operation") or "")
	category = str(entry.get("planning_category") or "")
	if entry.get("operation_row"):
		is_qc = frappe.db.get_value("Final EB BOM Operation", entry.operation_row, "is_qc")
		if cint(is_qc):
			return "Preserve Incoming Lot"
	if category == "QC" or re.search(r"\bQC\b|quality", operation, re.IGNORECASE):
		return "Preserve Incoming Lot"
	return "New Production Lot"


def get_operation_lot_prefix(entry):
	operation = str(entry.get("operation") or "")
	category = str(entry.get("planning_category") or "")
	configured = (getattr(frappe, "conf", {}) or {}).get("smartedge_production_lot_prefixes") or {}
	if isinstance(configured, str):
		try:
			configured = json.loads(configured)
		except (TypeError, ValueError):
			configured = {}
	configured_prefix = configured.get(operation) or configured.get(category)
	if configured_prefix:
		return str(configured_prefix).strip().upper()
	if category == "Extrusion" or "extrusion" in operation.lower():
		return "EXT"
	if "offline" in operation.lower() and "print" in operation.lower():
		return "OFP"
	if category == "Printing" or "print" in operation.lower():
		return "OPR"
	if category == "Slitting" or "slit" in operation.lower():
		return "SLT"
	if category == "Packing" or "pack" in operation.lower():
		return "PCK"
	letters = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", operation) if word)[:8].upper()
	return letters if len(letters) >= 2 else "LOT"


def record_completed_job(job):
	"""Create/link one non-stock lot and append idempotent WIP movements."""
	if isinstance(job, str):
		job = frappe.get_doc("SmartEdge Production Job", job)
	if job.status != "Completed":
		frappe.throw(_("Production Lot tracking requires a completed Production Job."))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	mode = get_operation_lot_mode(entry)
	if mode == "Stock Batch":
		return {"mode": mode, "production_lot": None}

	production = frappe.db.get_value(
		"Final EB BOM Generator",
		job.final_eb_bom_generator,
		["finished_item", "finished_item_name", "stock_uom"],
		as_dict=True,
	)
	if not production or not production.finished_item:
		frappe.throw(_("Finished Item is required before recording a Production Lot."))
	input_lot = get_input_production_lot(entry)

	if mode == "Preserve Incoming Lot":
		if not input_lot:
			frappe.throw(_("QC operation {0} has no released incoming Production Lot.").format(entry.operation))
		link_job_lots(job, input_lot, input_lot)
		input_quantity = flt(entry.released_qty_meter) or flt(job.actual_qty_meter)
		if input_quantity > 0:
			record_movement(
				"QC Hold",
				("qc-hold", job.name, input_lot),
				job,
				entry,
				production,
				quantity=input_quantity,
				uom=production.stock_uom,
				quantity_kg=entry.released_qty_kg,
				source_lot=input_lot,
				destination_lot=input_lot,
				source_operation=job.operation,
				destination_operation=job.operation,
				remarks=_("Incoming lot held at the QC stage until this QC operation completed."),
			)
		record_quality_movements(job, entry, production, input_lot)
		record_release_movement(job, entry, production, input_lot, qc_release=True)
		return {"mode": mode, "production_lot": input_lot}

	existing = job.get("output_production_lot") or frappe.db.get_value(
		LOT_DOCTYPE, {"production_job": job.name}, "name"
	)
	if existing:
		link_job_lots(job, input_lot, existing)
		ensure_output_movement(job, entry, production, existing)
		record_release_movement(job, entry, production, existing)
		return {"mode": mode, "production_lot": existing}

	produced_qty = flt(job.good_qty) or flt(job.actual_qty_meter)
	if produced_qty <= 0:
		frappe.throw(_("Actual good output is required before creating a Production Lot."))
	lot = frappe.new_doc(LOT_DOCTYPE)
	lot.naming_prefix = get_operation_lot_prefix(entry)
	lot.finished_item = production.finished_item
	lot.finished_item_name = production.finished_item_name or frappe.db.get_value(
		"Item", production.finished_item, "item_name"
	)
	lot.final_eb_bom_generator = job.final_eb_bom_generator
	lot.plan_entry = entry.name
	lot.production_job = job.name
	lot.operation = job.operation
	lot.operation_sequence = entry.sequence
	lot.planning_category = entry.planning_category
	lot.workstation = job.workstation
	lot.employee = job.operator_employee
	lot.planned_date = entry.planned_date
	lot.actual_start = job.actual_start
	lot.actual_end = job.actual_end
	lot.input_quantity = flt(entry.released_qty_meter) if input_lot else 0
	lot.produced_quantity = produced_qty
	lot.quantity_uom = production.stock_uom
	lot.produced_quantity_kg = flt(job.actual_qty_kg)
	lot.good_quantity = flt(job.good_qty) or produced_qty
	lot.rejected_quantity = flt(job.rejected_qty)
	lot.rework_quantity = flt(job.rework_qty)
	lot.usable_scrap = flt(job.usable_scrap)
	lot.unusable_scrap = flt(job.unusable_scrap)
	lot.input_production_lot = input_lot
	lot.work_order = entry.work_order
	lot.insert(ignore_permissions=True)
	link_job_lots(job, input_lot, lot.name)

	record_movement(
		"Production Output",
		("output", job.name),
		job,
		entry,
		production,
		quantity=produced_qty,
		uom=production.stock_uom,
		quantity_kg=job.actual_qty_kg,
		destination_lot=lot.name,
		destination_operation=job.operation,
	)
	if input_lot:
		input_qty = flt(entry.released_qty_meter) or produced_qty
		ensure_available_for_transfer(input_lot, job.operation, input_qty)
		record_movement(
			"Consume for Operation",
			("consume", job.name, input_lot),
			job,
			entry,
			production,
			quantity=input_qty,
			uom=production.stock_uom,
			quantity_kg=entry.released_qty_kg,
			source_lot=input_lot,
			source_operation=job.operation,
		)
		record_movement(
			"Lot Genealogy Link",
			("genealogy", job.name, input_lot, lot.name),
			job,
			entry,
			production,
			quantity=min(input_qty, produced_qty),
			uom=production.stock_uom,
			source_lot=input_lot,
			destination_lot=lot.name,
			source_operation=job.operation,
			destination_operation=job.operation,
		)
	record_quality_movements(job, entry, production, lot.name, informational=True)
	record_release_movement(job, entry, production, lot.name)
	reconcile_final_production(job.final_eb_bom_generator)
	return {"mode": mode, "production_lot": lot.name}


def ensure_output_movement(job, entry, production, lot_name):
	"""Repair an interrupted completion safely using the same stable movement key."""
	lot = frappe.db.get_value(
		LOT_DOCTYPE, lot_name, ["produced_quantity", "produced_quantity_kg", "quantity_uom"], as_dict=True
	)
	if not lot:
		frappe.throw(_("Production Lot {0} no longer exists.").format(lot_name))
	return record_movement(
		"Production Output",
		("output", job.name),
		job,
		entry,
		production,
		quantity=lot.produced_quantity,
		uom=lot.quantity_uom,
		quantity_kg=lot.produced_quantity_kg,
		destination_lot=lot_name,
		destination_operation=job.operation,
	)


def link_job_lots(job, input_lot, output_lot):
	updates = {}
	if job.meta.has_field("input_production_lot") and job.get("input_production_lot") != input_lot:
		updates["input_production_lot"] = input_lot
	if job.meta.has_field("output_production_lot") and job.get("output_production_lot") != output_lot:
		updates["output_production_lot"] = output_lot
	if updates:
		frappe.db.set_value("SmartEdge Production Job", job.name, updates, update_modified=False)
		for fieldname, value in updates.items():
			job.set(fieldname, value)


def get_input_production_lot(entry):
	source_job = entry.get("released_from_production_job")
	if not source_job:
		return None
	return frappe.db.get_value("SmartEdge Production Job", source_job, "output_production_lot")


def record_qc_hold(job, entry=None):
	"""Mark the predecessor lot as held while a QC-only operation is active."""
	entry = entry or frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	if get_operation_lot_mode(entry) != "Preserve Incoming Lot":
		return None
	input_lot = get_input_production_lot(entry)
	if not input_lot:
		frappe.throw(_("QC operation {0} has no released incoming Production Lot.").format(entry.operation))
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		job.final_eb_bom_generator,
		["finished_item", "stock_uom"],
		as_dict=True,
	)
	link_job_lots(job, input_lot, input_lot)
	return record_movement(
		"QC Hold",
		("qc-hold", job.name, input_lot),
		job,
		entry,
		production,
		quantity=flt(entry.released_qty_meter) or flt(job.actual_qty_meter),
		uom=production.stock_uom,
		quantity_kg=entry.released_qty_kg,
		source_lot=input_lot,
		destination_lot=input_lot,
		source_operation=job.operation,
		destination_operation=job.operation,
		remarks=_("Incoming lot held at the QC stage until this QC operation completes."),
	)


def record_quality_movements(job, entry, production, lot_name, informational=False):
	for movement_type, quantity in (
		("Rejection", job.rejected_qty),
		("Rework", job.rework_qty),
		("Scrap", flt(job.unusable_scrap) + flt(job.usable_scrap)),
	):
		if flt(quantity) <= 0:
			continue
		record_movement(
			movement_type,
			("quality", movement_type, job.name, lot_name),
			job,
			entry,
			production,
			quantity=quantity,
			uom=production.stock_uom,
			source_lot=None if informational else lot_name,
			destination_lot=lot_name if informational else None,
			source_operation=job.operation,
			destination_operation=job.operation if informational else None,
			remarks=_("Recorded from Production Job quality disposition."),
		)


def record_release_movement(job, entry, production, lot_name, qc_release=False):
	if not job.get("released_to_plan_entry"):
		return None
	destination = frappe.db.get_value(
		"SmartEdge Production Plan Entry", job.released_to_plan_entry, ["operation"], as_dict=True
	)
	quantity = flt(job.released_qty_meter)
	if not destination or quantity <= 0:
		return None
	movement_key = make_movement_key("release", job.name, job.released_to_plan_entry, lot_name)
	existing = frappe.db.exists(LEDGER_DOCTYPE, movement_key)
	if existing:
		return existing
	ensure_available_for_transfer(lot_name, job.operation, quantity)
	return record_movement(
		"QC Release" if qc_release else "Transfer to Next Operation",
		("release", job.name, job.released_to_plan_entry, lot_name),
		job,
		entry,
		production,
		quantity=quantity,
		uom=production.stock_uom,
		quantity_kg=job.released_qty_kg,
		source_lot=lot_name,
		destination_lot=lot_name,
		source_operation=job.operation,
		destination_operation=destination.operation,
		destination_plan_entry=job.released_to_plan_entry,
	)


def record_movement(
	movement_type,
	key_parts,
	job,
	entry,
	production,
	*,
	quantity,
	uom,
	quantity_kg=0,
	source_lot=None,
	destination_lot=None,
	source_batch=None,
	destination_batch=None,
	source_operation=None,
	destination_operation=None,
	destination_plan_entry=None,
	reference_doctype="SmartEdge Production Job",
	reference_name=None,
	remarks=None,
):
	movement_key = make_movement_key(*key_parts)
	existing = frappe.db.exists(LEDGER_DOCTYPE, movement_key)
	if existing:
		return existing
	doc = frappe.new_doc(LEDGER_DOCTYPE)
	doc.movement_key = movement_key
	doc.movement_type = movement_type
	doc.posting_datetime = job.actual_end or now_datetime()
	doc.status = ACTIVE
	doc.final_eb_bom_generator = job.final_eb_bom_generator
	doc.finished_item = production.finished_item
	doc.production_job = job.name
	doc.source_plan_entry = entry.name
	doc.destination_plan_entry = destination_plan_entry
	doc.employee = job.operator_employee
	doc.reference_doctype = reference_doctype
	doc.reference_name = reference_name or job.name
	doc.source_production_lot = source_lot
	doc.destination_production_lot = destination_lot
	doc.source_stock_batch = source_batch
	doc.destination_stock_batch = destination_batch
	doc.source_operation = source_operation
	doc.destination_operation = destination_operation
	doc.quantity = flt(quantity)
	doc.uom = uom
	doc.quantity_kg = flt(quantity_kg)
	doc.remarks = remarks
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return frappe.db.exists(LEDGER_DOCTYPE, movement_key)
	return doc.name


def make_movement_key(*parts):
	value = "|".join(str(part or "") for part in parts)
	return hashlib.sha256(value.encode()).hexdigest()


def get_lot_balances(lot_names):
	lot_names = list({name for name in lot_names if name})
	if not lot_names:
		return {}
	rows = frappe.get_all(
		LEDGER_DOCTYPE,
		filters={"status": ACTIVE},
		or_filters={
			"source_production_lot": ["in", lot_names],
			"destination_production_lot": ["in", lot_names],
		},
		fields=[
			"movement_type", "source_production_lot", "destination_production_lot",
			"source_operation", "destination_operation", "quantity",
		],
		limit_page_length=10000,
	)
	return calculate_lot_balances(lot_names, rows)


def calculate_lot_balances(lot_names, rows):
	"""Derive balances from immutable movements; kept pure for regression tests."""
	balances = {
		name: {
			"produced": 0.0,
			"available": 0.0,
			"stages": defaultdict(float),
			"transferred": defaultdict(float),
			"rejected": 0.0,
			"scrapped": 0.0,
			"rework": 0.0,
			"held_for_qc": 0.0,
		}
		for name in lot_names
	}
	for row in rows:
		quantity = flt(row.quantity)
		if row.movement_type == "Production Output" and row.destination_production_lot in balances:
			data = balances[row.destination_production_lot]
			data["produced"] += quantity
			data["available"] += quantity
			data["stages"][row.destination_operation] += quantity
		elif row.movement_type in STAGE_TRANSFER_TYPES and row.source_production_lot in balances:
			data = balances[row.source_production_lot]
			data["stages"][row.source_operation] -= quantity
			data["stages"][row.destination_operation] += quantity
			data["transferred"][row.source_operation] += quantity
			if row.movement_type == "QC Release":
				data["held_for_qc"] -= quantity
		elif row.movement_type in BALANCE_OUT_TYPES and row.source_production_lot in balances:
			data = balances[row.source_production_lot]
			data["available"] -= quantity
			data["stages"][row.source_operation] -= quantity
			if row.movement_type == "Rejection":
				data["rejected"] += quantity
			elif row.movement_type == "Scrap":
				data["scrapped"] += quantity
			elif row.movement_type == "Rework":
				data["rework"] += quantity
		elif row.movement_type == "QC Hold" and row.source_production_lot in balances:
			balances[row.source_production_lot]["held_for_qc"] += quantity
		elif row.movement_type in {"Rejection", "Scrap", "Rework"} and row.destination_production_lot in balances:
			data = balances[row.destination_production_lot]
			fieldname = {"Rejection": "rejected", "Scrap": "scrapped", "Rework": "rework"}[row.movement_type]
			data[fieldname] += quantity
		elif row.movement_type == "Reversal / Correction" and row.destination_production_lot in balances:
			data = balances[row.destination_production_lot]
			data["available"] += quantity
			if row.destination_operation:
				data["stages"][row.destination_operation] += quantity
	for data in balances.values():
		data["available"] = max(data["available"], 0)
		data["stages"] = {key: max(value, 0) for key, value in data["stages"].items()}
		data["transferred"] = dict(data["transferred"])
		data["held_for_qc"] = max(data["held_for_qc"], 0)
	return balances


def ensure_available_for_transfer(lot_name, operation, quantity):
	quantity = flt(quantity)
	if not lot_name or quantity <= 0:
		return
	data = get_lot_balances([lot_name]).get(lot_name) or {}
	available = min(
		flt(data.get("available")),
		flt((data.get("stages") or {}).get(operation)),
	)
	if quantity > available + 0.000001:
		frappe.throw(
			_("Production Lot {0} has {1} available at {2}; cannot transfer or consume {3}.").format(
				lot_name, available, operation, quantity
			)
		)


def attach_lot_details(rows):
	job_names = [row.get("production_job") for row in rows if row.get("production_job")]
	if not job_names or not frappe.db.exists("DocType", LOT_DOCTYPE):
		return
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"name": ["in", job_names]},
		fields=["name", "operation", "input_production_lot", "output_production_lot"],
		limit_page_length=len(job_names),
	)
	by_job = {job.name: job for job in jobs}
	lot_names = {job.output_production_lot or job.input_production_lot for job in jobs}
	lot_names.discard(None)
	lots = {
		lot.name: lot
		for lot in frappe.get_all(
			LOT_DOCTYPE,
			filters={"name": ["in", list(lot_names)]},
			fields=["name", "produced_quantity", "quantity_uom", "status"],
			limit_page_length=len(lot_names),
		)
	} if lot_names else {}
	balances = get_lot_balances(lot_names)
	for row in rows:
		job = by_job.get(row.get("production_job"))
		if not job:
			continue
		lot_name = job.output_production_lot or job.input_production_lot
		lot = lots.get(lot_name)
		if not lot:
			continue
		balance = balances.get(lot_name) or {}
		row.production_lot = lot_name
		row.production_lot_status = lot.status
		row.lot_produced_quantity = flt(lot.produced_quantity)
		row.lot_available_quantity = flt(balance.get("available"))
		row.lot_transferred_quantity = flt((balance.get("transferred") or {}).get(job.operation))
		row.lot_remaining_quantity = flt((balance.get("stages") or {}).get(job.operation))
		row.lot_uom = lot.quantity_uom


def reconcile_final_production(production_name, stock_entry=None):
	if not frappe.db.exists("DocType", LOT_DOCTYPE):
		return {"transferred": 0, "linked_batches": 0}
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		production_name,
		["finished_item", "stock_uom", "final_manufacturing_stock_entry", "final_stock_posted_qty"],
		as_dict=True,
	)
	if not production:
		return {"transferred": 0, "linked_batches": 0}
	stock_entry = stock_entry or production.final_manufacturing_stock_entry
	if not stock_entry or frappe.db.get_value("Stock Entry", stock_entry, "docstatus") != 1:
		return {"transferred": 0, "linked_batches": 0}

	all_lots = frappe.get_all(
		LOT_DOCTYPE,
		filters={"final_eb_bom_generator": production_name},
		fields=["name", "production_job", "operation", "operation_sequence", "produced_quantity", "quantity_uom"],
		order_by="operation_sequence desc, actual_end desc",
		limit_page_length=500,
	)
	if not all_lots:
		return {"transferred": 0, "linked_batches": 0}
	final_lot_names, ready = get_final_stock_lot_names(production_name, stock_entry)
	if not ready:
		# Final Stock Entry linking can run inside Production Job completion before
		# record_completed_job has created/linked that final job's output lot.
		return {"transferred": 0, "linked_batches": 0, "pending_final_lot": 1}
	final_lots = [lot for lot in all_lots if lot.name in final_lot_names]
	if not final_lots:
		return {"transferred": 0, "linked_batches": 0, "pending_final_lot": 1}
	balances = get_lot_balances([lot.name for lot in final_lots])
	remaining = flt(production.final_stock_posted_qty)
	transferred = 0.0
	for lot in final_lots:
		available = flt((balances.get(lot.name) or {}).get("available"))
		quantity = min(available, remaining)
		if quantity <= 0:
			continue
		job = frappe.get_doc("SmartEdge Production Job", lot.production_job)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		record_movement(
			"Transfer to Finished Goods",
			("finished-goods", stock_entry, lot.name),
			job,
			entry,
			production,
			quantity=quantity,
			uom=lot.quantity_uom,
			source_lot=lot.name,
			source_operation=lot.operation,
			reference_doctype="Stock Entry",
			reference_name=stock_entry,
		)
		frappe.db.set_value(LOT_DOCTYPE, lot.name, {"status": "Transferred to Finished Goods", "stock_entry": stock_entry}, update_modified=False)
		transferred += quantity
		remaining -= quantity
		if remaining <= 0:
			break
	linked = link_actual_batches_to_first_lot(production_name, all_lots, stock_entry, production)
	return {"transferred": transferred, "linked_batches": linked}


def get_final_stock_lot_names(production_name, stock_entry):
	"""Return output lots of the exact jobs linked to the authoritative final Stock Entry."""
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={
			"final_eb_bom_generator": production_name,
			"manufacturing_stock_entry": stock_entry,
			"status": "Completed",
		},
		fields=["name", "output_production_lot"],
		limit_page_length=500,
	)
	if not jobs or any(not job.output_production_lot for job in jobs):
		return set(), False
	return {job.output_production_lot for job in jobs}, True


def link_actual_batches_to_first_lot(production_name, lots, stock_entry, production):
	first_sequence = min(cint(lot.operation_sequence) for lot in lots)
	first_lot = next((lot for lot in lots if cint(lot.operation_sequence) == first_sequence), None)
	if not first_lot or not frappe.db.exists("DocType", "SmartEdge Batch Genealogy Movement"):
		return 0
	movements = frappe.get_all(
		"SmartEdge Batch Genealogy Movement",
		filters={"stock_entry": stock_entry, "direction": "Input", "source_status": ACTIVE},
		fields=["batch_no", "quantity", "uom"],
		limit_page_length=1000,
	)
	job = frappe.get_doc("SmartEdge Production Job", first_lot.production_job)
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	linked = 0
	for movement in movements:
		record_movement(
			"Stock Batch Input Link",
			("stock-batch-input", stock_entry, movement.batch_no, first_lot.name),
			job,
			entry,
			production,
			quantity=movement.quantity,
			uom=movement.uom,
			source_batch=movement.batch_no,
			destination_lot=first_lot.name,
			destination_operation=first_lot.operation,
			reference_doctype="Stock Entry",
			reference_name=stock_entry,
		)
		linked += 1
	return linked


@frappe.whitelist()
def repair_premature_final_transfers(production_name):
	"""Append reversals for lots transferred before the true final job output lot existed."""
	production_doc = frappe.get_doc("Final EB BOM Generator", production_name)
	production_doc.check_permission("write")
	stock_entry = production_doc.get("final_manufacturing_stock_entry")
	if not stock_entry or frappe.db.get_value("Stock Entry", stock_entry, "docstatus") != 1:
		frappe.throw(_("A submitted final manufacturing Stock Entry is required."))
	final_lot_names, ready = get_final_stock_lot_names(production_name, stock_entry)
	if not ready:
		frappe.throw(_("The final Production Job output lot is not linked yet."))
	premature = frappe.get_all(
		LEDGER_DOCTYPE,
		filters={
			"final_eb_bom_generator": production_name,
			"movement_type": "Transfer to Finished Goods",
			"reference_doctype": "Stock Entry",
			"reference_name": stock_entry,
			"status": ACTIVE,
			"source_production_lot": ["not in", list(final_lot_names)],
		},
		fields=[
			"name", "production_job", "source_production_lot", "quantity", "uom", "quantity_kg",
		],
		limit_page_length=500,
	)
	production = frappe.db.get_value(
		"Final EB BOM Generator", production_name, ["finished_item", "stock_uom"], as_dict=True
	)
	reversed_lots = set()
	for movement in premature:
		job = frappe.get_doc("SmartEdge Production Job", movement.production_job)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		record_movement(
			"Reversal / Correction",
			("reverse-premature-fg", movement.name),
			job,
			entry,
			production,
			quantity=movement.quantity,
			uom=movement.uom,
			quantity_kg=movement.quantity_kg,
			destination_lot=movement.source_production_lot,
			reference_doctype="Stock Entry",
			reference_name=stock_entry,
			remarks=_("Reverses premature custom WIP-to-FG movement {0}; no stock transaction changed.").format(
				movement.name
			),
		)
		reversed_lots.add(movement.source_production_lot)
	for lot_name in reversed_lots:
		frappe.db.set_value(LOT_DOCTYPE, lot_name, "status", ACTIVE, update_modified=False)
	return {
		"production": production_name,
		"stock_entry": stock_entry,
		"reversed_movements": len(premature),
		"reversed_lots": sorted(reversed_lots),
		"final_lots": sorted(final_lot_names),
	}


@frappe.whitelist()
def get_production_lot_genealogy(reference, direction="Backward", reference_type="Production Lot"):
	"""Trace custom lots without representing them as warehouse inventory."""
	if direction not in {"Backward", "Forward"}:
		frappe.throw(_("Trace Direction must be Backward or Forward."))
	lot_names = resolve_trace_roots(reference, reference_type, direction)
	if not lot_names:
		frappe.throw(_("No Production Lot is linked to {0}.").format(reference))
	for lot_name in lot_names:
		frappe.get_doc(LOT_DOCTYPE, lot_name).check_permission("read")
	visited = set()
	roots = [build_lot_genealogy_node(name, direction, visited, 0) for name in lot_names]
	return {
		"traceability_kind": "SmartEdge Production Lot",
		"direction": direction,
		"roots": roots,
		"lot_count": len(visited),
		"truncated": any(node.get("truncated") for node in roots),
	}


def resolve_trace_roots(reference, reference_type, direction):
	if reference_type == "Production Lot":
		return [reference] if frappe.db.exists(LOT_DOCTYPE, reference) else []
	if reference_type == "Production Job":
		values = frappe.db.get_value(
			"SmartEdge Production Job", reference, ["output_production_lot", "input_production_lot"], as_dict=True
		)
		name = (values or {}).get("output_production_lot") or (values or {}).get("input_production_lot")
		return [name] if name else []
	if reference_type == "Final EB BOM Generator":
		order = "operation_sequence desc, actual_end desc" if direction == "Backward" else "operation_sequence asc, actual_end asc"
		rows = frappe.get_all(
			LOT_DOCTYPE,
			filters={"final_eb_bom_generator": reference},
			fields=["name", "operation_sequence"],
			order_by=order,
			limit_page_length=500,
		)
		if not rows:
			return []
		sequence = rows[0].operation_sequence
		return [row.name for row in rows if row.operation_sequence == sequence]
	frappe.throw(_("Unsupported trace reference type {0}.").format(reference_type))


def build_lot_genealogy_node(lot_name, direction, visited, depth):
	if depth >= 12:
		return {"production_lot": lot_name, "truncated": True, "children": []}
	if lot_name in visited:
		return {"production_lot": lot_name, "cycle": True, "children": []}
	visited.add(lot_name)
	lot = frappe.db.get_value(
		LOT_DOCTYPE,
		lot_name,
		[
			"name", "finished_item", "finished_item_name", "operation", "planning_category",
			"production_job", "workstation", "employee", "actual_start", "actual_end",
			"input_quantity", "produced_quantity", "quantity_uom", "good_quantity",
			"rejected_quantity", "rework_quantity", "usable_scrap", "unusable_scrap",
			"quality_inspection", "stock_entry", "status",
		],
		as_dict=True,
	)
	balances = get_lot_balances([lot_name]).get(lot_name) or {}
	movements = frappe.get_all(
		LEDGER_DOCTYPE,
		filters={"status": ACTIVE},
		or_filters={"source_production_lot": lot_name, "destination_production_lot": lot_name},
		fields=[
			"name", "movement_type", "posting_datetime", "source_production_lot",
			"destination_production_lot", "source_stock_batch", "destination_stock_batch",
			"source_operation", "destination_operation", "quantity", "uom", "quantity_kg",
			"production_job", "employee", "reference_doctype", "reference_name", "remarks",
		],
		order_by="posting_datetime asc",
		limit_page_length=2000,
	)
	related_lots = []
	stock_batches = []
	for movement in movements:
		if direction == "Backward" and movement.destination_production_lot == lot_name:
			if movement.source_production_lot and movement.source_production_lot != lot_name:
				related_lots.append(movement.source_production_lot)
			if movement.source_stock_batch:
				stock_batches.append(movement.source_stock_batch)
		elif direction == "Forward" and movement.source_production_lot == lot_name:
			if movement.destination_production_lot and movement.destination_production_lot != lot_name:
				related_lots.append(movement.destination_production_lot)
			if movement.destination_stock_batch:
				stock_batches.append(movement.destination_stock_batch)
	return {
		**dict(lot or {}),
		"production_lot": lot_name,
		"traceability_type": "SmartEdge Production Lot (non-stock)",
		"available_quantity": flt(balances.get("available")),
		"remaining_quantity": flt((balances.get("stages") or {}).get(lot.operation if lot else None)),
		"transferred_quantity": flt((balances.get("transferred") or {}).get(lot.operation if lot else None)),
		"stock_batches": list(dict.fromkeys(stock_batches)),
		"movements": [dict(row) for row in movements],
		"children": [
			build_lot_genealogy_node(name, direction, visited, depth + 1)
			for name in dict.fromkeys(related_lots)
		],
	}


@frappe.whitelist()
def backfill_completed_job(production_job):
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("write")
	frappe.db.sql(
		"select name from `tabSmartEdge Production Job` where name=%s for update",
		(production_job,),
	)
	job.reload()
	missing = [
		label
		for fieldname, label in (
			("actual_start", _("Actual Start")),
			("actual_end", _("Actual Completion")),
			("operator_employee", _("Employee")),
			("workstation", _("Workstation")),
		)
		if not job.get(fieldname)
	]
	if job.status != "Completed":
		frappe.throw(_("Only completed Production Jobs can be backfilled."))
	if not (flt(job.good_qty) or flt(job.actual_qty_meter)):
		missing.append(_("Actual Good Output"))
	if missing:
		frappe.throw(_("Cannot backfill Production Lot; missing: {0}.").format(", ".join(missing)))
	result = record_completed_job(job)
	result["production_job"] = job.name
	return result
