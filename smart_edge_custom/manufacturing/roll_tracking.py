"""Controlled roll identity, QC traceability, rework, and packing allocation services.

Roll records are operational traceability records only.  Existing QC, stock, batch,
and Production Job APIs remain the authorities for physical quantities and posting.
"""
import json

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from smart_edge_custom.manufacturing.quantity import round_meter_quantity

ROLL = "SmartEdge Roll"
HISTORY = "SmartEdge Roll Operation History"


def _rows(value):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw(_("Roll details must be valid JSON."))
	return value or []


def _meter(value):
	if isinstance(value, str):
		value = value.replace(",", "").strip()
	return round_meter_quantity(value)


def _is_extrusion(entry):
	return str(entry.get("planning_category") or "").lower() == "extrusion" or "extr" in str(entry.get("operation") or "").lower()


def _append_history(doc, event_type, previous_status, new_status, previous_qty, new_qty, **kwargs):
	doc.append("operation_history", {
		"event_datetime": now_datetime(), "event_type": event_type,
		"operation": kwargs.get("operation") or doc.source_operation,
		"operation_job": kwargs.get("operation_job") or doc.source_production_job,
		"operation_plan_entry": kwargs.get("operation_plan_entry") or doc.source_plan_entry,
		"previous_status": previous_status, "new_status": new_status,
		"previous_meter_quantity": previous_qty, "new_meter_quantity": new_qty,
		"meter_loss": kwargs.get("meter_loss", max(flt(previous_qty) - flt(new_qty), 0)),
		"related_quality_inspection": kwargs.get("related_quality_inspection"),
		"employee": kwargs.get("employee"), "reason": kwargs.get("reason"),
		"remarks": kwargs.get("remarks"),
	})


def _save_roll(doc):
	doc.flags.smartedge_roll_internal = True
	doc.save(ignore_permissions=True)


def _source_batch(lot):
	if not lot:
		return None
	meta = frappe.get_meta("Batch", cached=True)
	return frappe.db.get_value("Batch", {"custom_operation_lot": lot}, "name") if meta.has_field("custom_operation_lot") else None


def create_rolls_for_extrusion(job, entry, roll_rows):
	"""Create permanent rolls after the enclosing job completion succeeds.

	The source-job/sequence unique check makes retries idempotent.  This function
	never runs before ``complete_job`` has committed its normal validations and
	stock/lot lifecycle.
	"""
	if not _is_extrusion(entry) or not roll_rows:
		return []
	existing = frappe.get_all(ROLL, filters={"source_production_job": job.name}, fields=["name", "roll_sequence"])
	by_sequence = {int(row.roll_sequence): row.name for row in existing}
	if existing and len(existing) == len(roll_rows):
		return [row.name for row in sorted(existing, key=lambda item: int(item.roll_sequence))]
	if existing:
		frappe.throw(_("Roll records for Production Job {0} are incomplete; refusing to create duplicates.").format(job.name))
	generator_item = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "finished_item")
	lot = job.get("output_production_lot")
	batch = _source_batch(lot)
	created = []
	for row in roll_rows:
		sequence = int(row["roll_no"])
		if sequence in by_sequence:
			created.append(by_sequence[sequence])
			continue
		doc = frappe.new_doc(ROLL)
		doc.naming_series = "SE-ROLL-.#####"
		doc.finished_item = generator_item
		doc.source_production_job = job.name
		doc.source_plan_entry = entry.name
		doc.source_work_order = entry.get("work_order")
		doc.source_operation = entry.operation
		doc.source_production_lot = lot
		doc.source_batch = batch
		doc.roll_sequence = sequence
		doc.original_meter_quantity = round_meter_quantity(row["meter_quantity"])
		doc.current_meter_quantity = doc.original_meter_quantity
		doc.qc_status = "Pending"
		doc.process_status = "Available for QC"
		doc.packing_status = "Not Available"
		_append_history(doc, "Created", "", "Available for QC", 0, doc.current_meter_quantity)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return created


def get_rolls_for_job(production_job, eligible_only=False):
	filters = {"source_production_job": production_job}
	if eligible_only:
		filters.update({"qc_status": "Passed", "process_status": "Available for Packing", "packing_status": "Available"})
	return frappe.get_all(ROLL, filters=filters, fields="*", order_by="roll_sequence asc")


def get_rolls_for_qc_source(source_production_job=None, source_production_lot=None):
	"""Resolve rolls from the QC source job, with a production-lot fallback.

	QC inspections normally carry the completed Extrusion job.  Older or
	intermediate QC activation paths may retain only the source Production Lot;
	the lot fallback keeps the lookup tied to the physical output without
	loosening the roll-level decision validation.
	"""
	rows = get_rolls_for_job(source_production_job) if source_production_job else []
	if not rows and source_production_lot:
		rows = frappe.get_all(
			ROLL,
			filters={"source_production_lot": source_production_lot},
			fields="*",
			order_by="roll_sequence asc",
		)
	return rows


@frappe.whitelist()
def get_eligible_rolls(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	# A QC checkpoint may sit between Extrusion and Packing.  Walk the release
	# chain to the operation that actually owns the permanent rolls.
	plan_entries = []
	current = entry
	for _ in range(4):
		parent = current.get("released_from_plan_entry")
		if not parent:
			break
		plan_entries.append(parent)
		current = frappe.get_doc("SmartEdge Production Plan Entry", parent)
	if not plan_entries:
		return []
	return frappe.get_all(ROLL, filters={
		"source_plan_entry": ["in", plan_entries],
		"qc_status": "Passed", "process_status": "Available for Packing", "packing_status": "Available",
	}, fields=["name", "finished_item", "current_meter_quantity", "source_production_job", "source_production_lot"], order_by="roll_sequence asc")


@frappe.whitelist()
def reserve_rolls_for_packing(plan_entry, roll_ids):
	"""Reserve selected rolls on the new master records; no existing schema changes."""
	from smart_edge_custom.manufacturing.packing import _require_packing_role
	_require_packing_role()
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	ids = list(dict.fromkeys(_rows(roll_ids)))
	if not ids:
		frappe.throw(_("Select at least one eligible roll."))
	reserved = []
	for name in ids:
		frappe.db.sql("select name from `tabSmartEdge Roll` where name=%s for update", (name,))
		doc = frappe.get_doc(ROLL, name)
		if doc.qc_status != "Passed" or doc.process_status != "Available for Packing" or doc.packing_status != "Available":
			frappe.throw(_("Roll {0} is no longer available for Packing.").format(name))
		previous = doc.process_status
		doc.reserved_for_plan_entry = entry.name
		doc.process_status = "Reserved for Packing"
		doc.packing_status = "Reserved"
		_append_history(doc, "Reserved for Packing", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity, operation_plan_entry=entry.name)
		_save_roll(doc)
		reserved.append(name)
	return {"plan_entry": entry.name, "rolls": reserved, "meter_quantity": sum(flt(frappe.db.get_value(ROLL, name, "current_meter_quantity")) for name in reserved)}


@frappe.whitelist()
def release_roll_reservations(plan_entry):
	for name in frappe.get_all(ROLL, filters={"reserved_for_plan_entry": plan_entry, "process_status": "Reserved for Packing"}, pluck="name"):
		doc = frappe.get_doc(ROLL, name)
		previous = doc.process_status
		doc.reserved_for_plan_entry = None
		doc.process_status = "Available for Packing"
		doc.packing_status = "Available"
		_append_history(doc, "Released", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity)
		_save_roll(doc)


def bind_roll_reservations_to_job(plan_entry, production_job):
	for name in frappe.get_all(ROLL, filters={"reserved_for_plan_entry": plan_entry, "process_status": "Reserved for Packing"}, pluck="name"):
		doc = frappe.get_doc(ROLL, name)
		doc.reserved_for_production_job = production_job
		_save_roll(doc)


def mark_rolls_packed_for_job(production_job):
	"""Finalize only rolls reserved for this Packing job after normal completion."""
	for name in frappe.get_all(ROLL, filters={"reserved_for_production_job": production_job, "process_status": "Reserved for Packing"}, pluck="name"):
		doc = frappe.get_doc(ROLL, name)
		previous = doc.process_status
		doc.process_status = "Packed"
		doc.packing_status = "Packed"
		doc.packing_job = production_job
		_append_history(doc, "Packing Completed", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity, operation_job=production_job)
		_save_roll(doc)


def set_roll_qc_decision(roll, decision, quality_inspection=None, employee=None, reason=None):
	"""Apply only the roll traceability state; caller must first submit aggregate QC."""
	doc = frappe.get_doc(ROLL, roll)
	decision = str(decision or "").strip()
	if decision not in {"Passed", "Rework Required", "Rejected"}:
		frappe.throw(_("Invalid roll QC decision for {0}.").format(roll))
	if doc.process_status in {"Packed", "Reserved for Packing"}:
		frappe.throw(_("Roll {0} cannot receive another QC decision in its current state.").format(roll))
	previous = doc.process_status
	doc.qc_status = decision
	doc.process_status = "Available for Packing" if decision == "Passed" else ("In Rework" if decision == "Rework Required" else "Rejected")
	doc.packing_status = "Available" if decision == "Passed" else "Not Available"
	_append_history(doc, "QC Decision", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity, related_quality_inspection=quality_inspection, employee=employee, reason=reason)
	_save_roll(doc)
	return doc.name


@frappe.whitelist()
def record_rework_completion(roll, rework_production_job, current_meter_quantity, reason=None):
	"""Link a completed existing rework job to a roll and require re-QC.

	The existing Production Job remains responsible for stock, Kg, scrap, and
	genealogy.  This service only records the validated resulting roll quantity.
	"""
	doc = frappe.get_doc(ROLL, roll)
	job = frappe.get_doc("SmartEdge Production Job", rework_production_job)
	job.check_permission("write")
	if doc.qc_status != "Rework Required" or doc.process_status != "In Rework":
		frappe.throw(_("Roll {0} is not awaiting Rework.").format(roll))
	if job.status != "Completed":
		frappe.throw(_("Rework Production Job must be completed before updating the roll."))
	new_qty = _meter(current_meter_quantity)
	if new_qty <= 0 or new_qty > flt(doc.original_meter_quantity):
		frappe.throw(_("Reworked roll quantity must be positive and cannot exceed the original quantity."))
	previous_qty = flt(doc.current_meter_quantity)
	previous_status = doc.process_status
	doc.current_meter_quantity = new_qty
	doc.qc_status = "Awaiting Re-QC"
	doc.process_status = "Awaiting Re-QC"
	doc.packing_status = "Not Available"
	_append_history(doc, "Rework Completed", previous_status, doc.process_status, previous_qty, new_qty, meter_loss=max(previous_qty-new_qty, 0), operation_job=job.name, operation_plan_entry=job.plan_entry, reason=reason)
	_save_roll(doc)
	return {"roll": doc.name, "original_meter_quantity": doc.original_meter_quantity, "current_meter_quantity": new_qty, "meter_loss": max(previous_qty-new_qty, 0), "status": doc.process_status}
