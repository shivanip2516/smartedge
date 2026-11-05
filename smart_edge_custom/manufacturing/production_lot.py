import hashlib
import json
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime

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


def is_qc_operation(entry):
	if entry.get("operation_row") and cint(frappe.db.get_value("Final EB BOM Operation", entry.operation_row, "is_qc")):
		return True
	return entry.get("planning_category") == "QC" or bool(re.search(r"\bQC\b|quality", str(entry.get("operation") or ""), re.IGNORECASE))


def get_operation_lot_mode(entry):
	if entry.get("plan_type") == "Required Material":
		return "Stock Batch"
	if entry.get("plan_type") == "Final EB":
		return "New Production Lot"
	return "Preserve Incoming Lot" if is_qc_operation(entry) else "New Production Lot"


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
	if re.search(r"\bQC\b|quality", operation, re.IGNORECASE):
		return "QC"
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


def get_operation_lot_name(prefix, production_job, sequence):
	"""Identify a non-stock operation lot without allocating an ERPNext Batch."""
	identity = f"{prefix}-{production_job}-{cint(sequence):02d}"
	if len(identity) <= 140:
		return identity
	digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
	return f"{identity[:115]}-{digest}"


def record_completed_job(job, allow_create=True):
	"""Create/link one non-stock lot and append idempotent WIP movements."""
	if isinstance(job, str):
		job = frappe.get_doc("SmartEdge Production Job", job)
	if job.status != "Completed":
		frappe.throw(_("Production Lot tracking requires a completed Production Job."))
	frappe.db.sql("select name from `tabSmartEdge Production Job` where name=%s for update", (job.name,))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	mode = get_operation_lot_mode(entry)
	if mode == "Stock Batch":
		return {"mode": mode, "production_lot": None}
	if entry.final_eb_bom_generator != job.final_eb_bom_generator or entry.operation != job.operation:
		frappe.throw(_("Production Job must match its plan's Finished Goods production and operation."))

	production = frappe.db.get_value(
		"Final EB BOM Generator",
		job.final_eb_bom_generator,
		["finished_item", "finished_item_name", "stock_uom"],
		as_dict=True,
	)
	if not production or not production.finished_item:
		frappe.throw(_("Finished Item is required before recording a Production Lot."))
	input_lot = get_input_production_lot(entry)
	# Preserve recorded historical QC reuse; never rewrite completed ledger history.
	if is_qc_operation(entry) and input_lot and job.get("output_production_lot") == input_lot:
		if not allow_create or frappe.db.exists(LEDGER_DOCTYPE, {
			"production_job": job.name, "movement_type": "QC Release", "status": ACTIVE,
		}):
			mode = "Preserve Incoming Lot"
		else:
			# A job started with the older QC hold behavior has no completed output yet.
			link_job_lots(job, input_lot, None)

	if mode == "Preserve Incoming Lot":
		owned = frappe.db.get_value(LOT_DOCTYPE, {"production_job": job.name}, "name")
		if owned:
			return {"mode": "Historical Operation Lot", "production_lot": owned}
		if not allow_create:
			return {"mode": mode, "production_lot": None, "backfill_required": True}
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

	existing = frappe.db.get_value(LOT_DOCTYPE, {"production_job": job.name}, "name", for_update=True)
	if job.get("output_production_lot") and job.output_production_lot != existing:
		frappe.throw(_("Production Job {0} has an inconsistent output Production Lot reference.").format(job.name))
	if existing:
		link_job_lots(job, input_lot, existing)
		ensure_output_movement(job, entry, production, existing)
		record_release_movement(job, entry, production, existing)
		from smart_edge_custom.manufacturing.operation_batch import ensure_operation_batch
		operation_batches = ensure_operation_batch(existing, allow_create=False)
		if allow_create or frappe.db.exists("SmartEdge Unusable Scrap Lot", {"production_job": job.name}):
			from smart_edge_custom.manufacturing.unusable_scrap import prepare_unusable_scrap
			prepare_unusable_scrap(job, frappe.get_doc(LOT_DOCTYPE, existing), operation_batches[0] if operation_batches else None)
		from smart_edge_custom.manufacturing.scrap_recovery import register_usable_scrap
		register_usable_scrap(job, frappe.get_doc(LOT_DOCTYPE, existing), operation_batches[0] if operation_batches else None)
		return {"mode": mode, "production_lot": existing, "operation_batches": operation_batches}

	if not allow_create:
		frappe.throw(_("Completed Production Job {0} has no operation batch. Use the controlled backfill workflow.").format(job.name))
	lot = _create_completed_operation_lot(job, entry, production, input_lot)
	produced_qty = lot.produced_quantity
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
		if is_qc_operation(entry) and frappe.db.exists(LEDGER_DOCTYPE, make_movement_key("qc-hold", job.name, input_lot)):
			record_movement(
				"QC Release", ("qc-input-release", job.name, input_lot), job, entry, production,
				quantity=input_qty, uom=production.stock_uom, source_lot=input_lot, destination_lot=input_lot,
				source_operation=job.operation, destination_operation=job.operation,
			)
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
	from smart_edge_custom.manufacturing.unusable_scrap import prepare_unusable_scrap
	unusable_scrap_lot = prepare_unusable_scrap(job, lot)
	reconcile_final_production(job.final_eb_bom_generator)
	from smart_edge_custom.manufacturing.operation_batch import ensure_operation_batch
	operation_batches = ensure_operation_batch(lot, allow_create=True)
	if unusable_scrap_lot and operation_batches:
		unusable = frappe.get_doc("SmartEdge Unusable Scrap Lot", unusable_scrap_lot)
		if not unusable.source_batch and not unusable.storage_stock_entry:
			unusable.db_set("source_batch", operation_batches[0], update_modified=False)
	from smart_edge_custom.manufacturing.scrap_recovery import register_usable_scrap
	register_usable_scrap(job, lot, operation_batches[0] if operation_batches else None)
	return {"mode": mode, "production_lot": lot.name, "operation_batches": operation_batches}


def _create_completed_operation_lot(job, entry, production, input_lot):
	produced_qty = flt(job.good_qty) or flt(job.actual_qty_meter)
	if produced_qty <= 0:
		frappe.throw(_("Actual good output is required before creating a Production Lot."))
	lot = frappe.new_doc(LOT_DOCTYPE)
	lot.naming_prefix = get_operation_lot_prefix(entry)
	lot.is_stock_inventory_batch = 0
	lot.finished_item = production.finished_item
	lot.finished_item_name = frappe.db.get_value("Item", production.finished_item, "item_name") or production.finished_item
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
	lot.planned_quantity = flt(entry.production_quantity)
	lot.actual_output_quantity = flt(job.actual_qty_meter)
	lot.quality_inspection = job.get("quality_inspection") or frappe.db.get_value(
		"Quality Inspection", {"reference_name": job.name, "docstatus": ["!=", 2]}, "name", order_by="creation desc"
	)
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
	return lot


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
	preserve_incoming = get_operation_lot_mode(entry) == "Preserve Incoming Lot"
	if not preserve_incoming and not is_qc_operation(entry):
		return None
	input_lot = get_input_production_lot(entry)
	if not input_lot:
		if not preserve_incoming:
			return None
		frappe.throw(_("QC operation {0} has no released incoming Production Lot.").format(entry.operation))
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		job.final_eb_bom_generator,
		["finished_item", "stock_uom"],
		as_dict=True,
	)
	link_job_lots(job, input_lot, input_lot if preserve_incoming else None)
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
		fields=["name", "status", "operation", "actual_qty_meter", "input_production_lot", "output_production_lot", "manufacturing_stock_entry"],
		limit_page_length=len(job_names),
	)
	by_job = {job.name: job for job in jobs}
	trace_jobs = set(job_names) | {row.get("released_from_production_job") for row in rows if row.get("released_from_production_job")}
	lots = {lot.production_job: lot for lot in frappe.get_all(
		LOT_DOCTYPE, filters={"production_job": ["in", list(trace_jobs)]},
		fields=["name", "production_job", "finished_item", "produced_quantity", "planned_quantity", "actual_output_quantity", "quantity_uom", "status"],
		limit_page_length=len(trace_jobs),
	)}
	lot_names = {lot.name for lot in lots.values()}
	balances = get_lot_balances(lot_names)
	physical_by_lot = {}
	if lot_names and frappe.get_meta("Batch").has_field("custom_operation_lot"):
		physical_batches = frappe.get_all("Batch", filters={
			"custom_operation_lot": ["in", list(lot_names)], "custom_batch_purpose": "Physical Stock",
		}, fields=["name", "custom_operation_lot"])
		batch_to_lot = {batch.name: batch.custom_operation_lot for batch in physical_batches}
		stock_movements = frappe.get_all("SmartEdge Batch Genealogy Movement", filters={
			"batch_no": ["in", list(batch_to_lot)], "source_status": ACTIVE,
		}, fields=["batch_no", "direction", "quantity", "warehouse", "stock_entry"]) if batch_to_lot else []
		for movement in stock_movements:
			data = physical_by_lot.setdefault(batch_to_lot[movement.batch_no], {
				"batch": movement.batch_no, "posted": 0.0, "transferred": 0.0,
				"warehouse": None, "stock_entry": None,
			})
			if movement.direction == "Output":
				data["posted"] += flt(movement.quantity)
				data["warehouse"] = movement.warehouse
				data["stock_entry"] = movement.stock_entry
			else:
				data["transferred"] += flt(movement.quantity)
	finished_batches = defaultdict(list)
	if lot_names:
		links = frappe.get_all(
			LEDGER_DOCTYPE,
			filters={"status": ACTIVE, "movement_type": "Lot Genealogy Link",
				"source_production_lot": ["in", list(lot_names)],
				"destination_stock_batch": ["is", "set"], "reference_doctype": "Stock Entry"},
			fields=["source_production_lot", "destination_stock_batch"],
		)
		for link in links:
			if link.destination_stock_batch not in finished_batches[link.source_production_lot]:
				finished_batches[link.source_production_lot].append(link.destination_stock_batch)
	for row in rows:
		job = by_job.get(row.get("production_job"))
		if not job:
			continue
		previous = lots.get(row.get("released_from_production_job"))
		row.previous_operation_lot = previous.name if previous else job.input_production_lot
		lot = lots.get(job.name) if job.status == "Completed" else None
		row.operation_lot_missing = job.status == "Completed" and row.get("plan_type") == "Final EB" and not lot
		if not lot:
			continue
		lot_name = lot.name
		balance = balances.get(lot_name) or {}
		row.production_lot = lot_name
		row.operation_trace_id = lot_name
		row.production_lot_status = lot.status
		row.lot_produced_quantity = flt(lot.produced_quantity)
		row.lot_planned_quantity = flt(lot.planned_quantity)
		row.lot_actual_output_quantity = flt(job.actual_qty_meter)
		row.related_finished_goods_batches = finished_batches.get(lot_name, [])
		row.lot_available_quantity = flt(balance.get("available"))
		row.lot_transferred_quantity = flt((balance.get("transferred") or {}).get(job.operation))
		row.lot_remaining_quantity = flt((balance.get("stages") or {}).get(job.operation))
		row.lot_uom = lot.quantity_uom
		physical = physical_by_lot.get(lot_name)
		if physical:
			from smart_edge_custom.manufacturing.planning import get_required_conversion_factor
			stock_uom = frappe.db.get_value("Item", lot.finished_item, "stock_uom")
			factor = get_required_conversion_factor(lot.finished_item, "Meter", stock_uom)
			row.physical_stock_entry = job.manufacturing_stock_entry or physical["stock_entry"]
			row.physical_batch = physical["batch"]
			row.physical_wip_warehouse = physical["warehouse"]
			row.physical_posted_quantity = physical["posted"] / factor
			row.physical_transferred_quantity = physical["transferred"] / factor
			stock_available = max((physical["posted"] - physical["transferred"]) / factor, 0)
			row.physical_available_quantity = min(stock_available, flt(balance.get("available")))


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
	link_final_stock_batches(final_lots, stock_entry, production)
	linked = link_actual_batches_to_first_lot(production_name, all_lots, stock_entry, production)
	return {"transferred": transferred, "linked_batches": linked}



def link_final_stock_batches(lots, stock_entry, production):
	"""Attach only real submitted output batches; never invent a split allocation."""
	outputs = frappe.get_all(
		"SmartEdge Batch Genealogy Movement",
		filters={"stock_entry": stock_entry, "direction": "Output", "source_status": ACTIVE, "item_code": production.finished_item},
		fields=["batch_no", "quantity", "uom"],
	)
	for lot in lots:
		job = frappe.get_doc("SmartEdge Production Job", lot.production_job)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		for output in outputs:
			record_movement(
				"Lot Genealogy Link", ("stock-batch-output", stock_entry, lot.name, output.batch_no),
				job, entry, production, quantity=output.quantity, uom=output.uom,
				source_lot=lot.name, destination_batch=output.batch_no, source_operation=lot.operation,
				reference_doctype="Stock Entry", reference_name=stock_entry,
				remarks=_("Event-level link to the actual Finished Goods batch received by this Stock Entry. Quantity is the stock-event output total, not an inferred per-lot split."),
			)

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
		"input_batches": get_input_material_batch_details(roots) if direction == "Backward" else [],
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


def _recorded_trace_input(lot):
	"""Resolve operation ancestry from saved release references, without replaying WIP."""
	source_job = frappe.db.get_value("SmartEdge Production Plan Entry", lot.get("plan_entry"), "released_from_production_job") if lot.get("plan_entry") else None
	owned = frappe.db.get_value(LOT_DOCTYPE, {"production_job": source_job}, "name") if source_job else None
	return owned or lot.get("input_production_lot")


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
			"name", "finished_item", "finished_item_name", "operation", "planning_category", "plan_entry", "input_production_lot",
			"production_job", "workstation", "employee", "actual_start", "actual_end",
			"input_quantity", "planned_quantity", "actual_output_quantity", "produced_quantity", "quantity_uom", "good_quantity",
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
	if lot and direction == "Backward":
		previous = _recorded_trace_input(lot)
		if previous:
			if previous != lot.input_production_lot:
				related_lots = [name for name in related_lots if name != lot.input_production_lot]
			related_lots.append(previous)
	elif lot and direction == "Forward":
		entries = frappe.get_all("SmartEdge Production Plan Entry", filters={"released_from_production_job": lot.production_job}, pluck="name")
		if entries:
			related_lots.extend(frappe.get_all(LOT_DOCTYPE, filters={"plan_entry": ["in", entries]}, pluck="name"))
		kept = []
		for name in dict.fromkeys(related_lots):
			child = frappe.db.get_value(LOT_DOCTYPE, name, ["plan_entry", "input_production_lot"], as_dict=True) or {}
			previous = _recorded_trace_input(child)
			if child.get("input_production_lot") != lot_name or not previous or previous == lot_name:
				kept.append(name)
		related_lots = kept
	from smart_edge_custom.manufacturing.batch_genealogy import get_genealogy, safe_link

	physical_history = [
		get_genealogy(batch, direction=direction, include_operation_lots=False)["root"]
		for batch in dict.fromkeys(stock_batches) if safe_link("Batch", batch)
	]
	return {
		**dict(lot or {}),
		"production_lot": lot_name,
		"stock_batch_genealogy": physical_history,
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
def backfill_completed_job(production_job, dry_run=False):
	"""Explicit, reviewable repair; historical QC adds metadata, never WIP or stock."""
	frappe.only_for(("System Manager", "Manufacturing Manager"))
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("write")
	if not cint(dry_run):
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
	if flt(job.actual_qty_meter) <= 0 or (flt(job.good_qty) or flt(job.actual_qty_meter)) <= 0:
		missing.append(_("Actual Good Output"))
	if missing:
		frappe.throw(_("Cannot backfill Production Lot; missing: {0}.").format(", ".join(missing)))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	if entry.plan_type != "Final EB" or entry.operation != job.operation or entry.final_eb_bom_generator != job.final_eb_bom_generator:
		frappe.throw(_("Backfill requires a matching Finished Goods plan and completed operation."))
	if get_datetime(job.actual_end) < get_datetime(job.actual_start) or flt(entry.production_quantity) <= 0 or cint(entry.sequence) <= 0:
		frappe.throw(_("Backfill requires valid recorded completion times, planned quantity, and operation sequence."))
	operation = frappe.db.get_value("Final EB BOM Operation", entry.operation_row,
		["parent", "operation", "sequence"], as_dict=True) if entry.operation_row else None
	if not operation or operation.parent != job.final_eb_bom_generator or operation.operation != job.operation or cint(operation.sequence) != cint(entry.sequence):
		frappe.throw(_("Backfill operation must match the saved Finished Goods operation and sequence."))
	production = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator,
		["finished_item", "stock_uom"], as_dict=True)
	if not production or not production.finished_item or not frappe.db.exists("Item", production.finished_item):
		frappe.throw(_("A valid Finished Goods Item is required for backfill."))
	existing = frappe.db.get_value(LOT_DOCTYPE, {"production_job": job.name}, "name", for_update=not cint(dry_run))
	input_lot = job.get("input_production_lot")
	historical_qc = bool(is_qc_operation(entry) and input_lot and job.output_production_lot == input_lot)
	if historical_qc and not existing:
		source = frappe.db.get_value(LOT_DOCTYPE, input_lot, ["finished_item", "production_job"], as_dict=True)
		evidence = frappe.db.get_value(LEDGER_DOCTYPE, {
			"production_job": job.name, "movement_type": "QC Release", "status": ACTIVE,
			"source_production_lot": input_lot, "destination_plan_entry": job.released_to_plan_entry,
		}, ["name", "quantity"], as_dict=True)
		if (not source or source.finished_item != production.finished_item
			or source.production_job != entry.get("released_from_production_job")
			or not evidence or flt(evidence.quantity) <= 0
			or abs(flt(evidence.quantity) - flt(job.released_qty_meter)) > 0.000001):
			frappe.throw(_("Historical QC backfill requires matching recorded predecessor and QC release evidence."))
	preview = {"production_job": job.name, "finished_item": production.finished_item,
		"operation": job.operation, "sequence": entry.sequence,
		"production_lot": existing or get_operation_lot_name(get_operation_lot_prefix(entry), job.name, entry.sequence),
		"action": "Reuse" if existing else ("Create historical QC trace record" if historical_qc else "Backfill operation lot"),
		"planned_quantity": entry.production_quantity, "actual_output_quantity": job.actual_qty_meter,
		"actual_end": job.actual_end, "previous_operation_lot": input_lot}
	if cint(dry_run) or existing:
		return preview
	savepoint = "lot_backfill_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(savepoint)
	try:
		if historical_qc:
			# Its output was already released/consumed using the old shared lot.
			# Preserve every execution pointer, ledger balance and stock document.
			lot = _create_completed_operation_lot(job, entry, production, input_lot)
			preview["production_lot"] = lot.name
		else:
			preview.update(record_completed_job(job))
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	frappe.db.release_savepoint(savepoint)
	return preview


def get_input_material_batch_details(nodes):
	"""Display actual prepared-material consumption evidenced by submitted stock movements."""
	from smart_edge_custom.manufacturing.batch_genealogy import safe_link

	result = {}
	for node in nodes:
		for movement in node.get("movements") or []:
			batch = movement.get("source_stock_batch")
			if not batch or not safe_link("Batch", batch):
				continue
			item = frappe.db.get_value("Batch", batch, "item")
			details = frappe.db.get_value("Item", item, ["item_name", "item_group"], as_dict=True) or {}
			if details.get("item_group") in {"Compounds", "Base Colour"}:
				key = (movement.get("reference_name"), batch)
				result[key] = {"stock_entry": movement.get("reference_name"), "batch_no": batch, "item_code": item, "item_name": details.get("item_name"),
					"item_group": details.get("item_group"), "quantity": movement.get("quantity"), "uom": movement.get("uom")}
		for row in get_input_material_batch_details(node.get("children") or []):
			result[(row.get("stock_entry"), row["batch_no"])] = row
	return list(result.values())
