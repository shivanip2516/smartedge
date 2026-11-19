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
	plan_entries = _packing_roll_source_entries(entry)
	if not plan_entries:
		return []
	return frappe.get_all(ROLL, filters={
		"source_plan_entry": ["in", plan_entries],
		"qc_status": "Passed", "process_status": "Available for Packing", "packing_status": "Available",
	}, fields=["name", "finished_item", "current_meter_quantity", "source_production_job", "source_production_lot"], order_by="roll_sequence asc")


def _packing_roll_source_entries(entry):
	"""Return the upstream plan entries that own rolls released to Packing."""
	plan_entries = []
	current = entry
	for _ in range(4):
		parent = current.get("released_from_plan_entry")
		if not parent:
			break
		plan_entries.append(parent)
		current = frappe.get_doc("SmartEdge Production Plan Entry", parent)
	return plan_entries


@frappe.whitelist()
def get_packing_roll_context(plan_entry):
	"""Return available rolls plus rolls already reserved by this Packing plan."""
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("read")
	source_entries = _packing_roll_source_entries(entry)
	if not source_entries:
		return {"eligible_rolls": [], "reserved_rolls": []}
	fields = ["name", "finished_item", "current_meter_quantity", "source_production_job", "source_production_lot", "roll_sequence"]
	base_filters = {"source_plan_entry": ["in", source_entries], "qc_status": "Passed"}
	eligible = frappe.get_all(
		ROLL,
		filters={**base_filters, "process_status": "Available for Packing", "packing_status": "Available"},
		fields=fields,
		order_by="roll_sequence asc",
	)
	reserved = frappe.get_all(
		ROLL,
		filters={**base_filters, "reserved_for_plan_entry": entry.name, "process_status": "Reserved for Packing", "packing_status": "Reserved"},
		fields=fields,
		order_by="roll_sequence asc",
	)
	return {"eligible_rolls": eligible, "reserved_rolls": reserved}


def validate_packing_roll_selection(plan_entry, roll_ids):
	"""Validate a whole-roll selection without changing its allocation state."""
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	ids = list(dict.fromkeys(_rows(roll_ids)))
	if not ids:
		frappe.throw(_("Select at least one eligible roll for Packing."))
	source_entries = _packing_roll_source_entries(entry)
	if not source_entries:
		frappe.throw(_("No QC-released rolls are available for this Packing operation."))
	rows = frappe.get_all(
		ROLL,
		filters={"name": ["in", ids], "source_plan_entry": ["in", source_entries]},
		fields=["name", "current_meter_quantity", "qc_status", "process_status", "packing_status", "reserved_for_plan_entry"],
	)
	by_name = {row.name: row for row in rows}
	if len(by_name) != len(ids):
		frappe.throw(_("One or more selected rolls do not belong to this Packing source."))
	for name in ids:
		row = by_name[name]
		if row.qc_status != "Passed":
			frappe.throw(_("Roll {0} is not QC approved for Packing.").format(name))
		if row.reserved_for_plan_entry != entry.name and (
			row.process_status != "Available for Packing" or row.packing_status != "Available"
		):
			frappe.throw(_("Roll {0} is no longer available for Packing.").format(name))
	meter_quantity = sum(flt(by_name[name].current_meter_quantity) for name in ids)
	return {"roll_ids": ids, "meter_quantity": meter_quantity}


@frappe.whitelist()
def reserve_rolls_for_packing(plan_entry, roll_ids):
	"""Reserve selected rolls on the new master records; no existing schema changes."""
	from smart_edge_custom.manufacturing.packing import _require_packing_role
	_require_packing_role()
	return _reserve_rolls_for_packing(plan_entry, roll_ids)


def _reserve_rolls_for_packing(plan_entry, roll_ids):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	ids = validate_packing_roll_selection(entry.name, roll_ids)["roll_ids"]
	current = frappe.get_all(ROLL, filters={"reserved_for_plan_entry": entry.name, "process_status": "Reserved for Packing"}, pluck="name")
	all_locked = sorted(set(current) | set(ids))
	for name in all_locked:
		frappe.db.sql("select name from `tabSmartEdge Roll` where name=%s for update", (name,))
	for name in current:
		if name in ids:
			continue
		doc = frappe.get_doc(ROLL, name)
		previous = doc.process_status
		doc.reserved_for_plan_entry = None
		doc.process_status = "Available for Packing"
		doc.packing_status = "Available"
		_append_history(doc, "Released", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity, operation_plan_entry=entry.name)
		_save_roll(doc)
	reserved = []
	for name in ids:
		doc = frappe.get_doc(ROLL, name)
		if doc.reserved_for_plan_entry == entry.name:
			reserved.append(name)
			continue
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


@frappe.whitelist()
def create_rework_production_job(roll, quality_inspection, rework_plan_entry, operator_employee=None, workstation=None):
	"""Create a normal non-stock Production Job for one rework roll.

	Rework execution is deliberately represented by the existing plan-entry and
	Production Job documents.  The job must be attached to an explicitly
	configured Rework operation; this function does not create or mutate a route
	snapshot and never posts stock.
	"""
	doc = frappe.get_doc(ROLL, roll)
	if doc.qc_status != "Rework Required" or doc.process_status != "In Rework":
		frappe.throw(_("Roll {0} is not awaiting Rework.").format(roll))

	inspection = frappe.get_doc("Quality Inspection", quality_inspection)
	if inspection.get("custom_smartedge_qc_status") != "Rework Required":
		frappe.throw(_("Quality Inspection {0} is not an unresolved rework inspection.").format(quality_inspection))
	if inspection.get("custom_source_production_job") != doc.source_production_job:
		frappe.throw(_("Quality Inspection {0} does not belong to roll {1}.").format(quality_inspection, roll))

	entry = frappe.get_doc("SmartEdge Production Plan Entry", rework_plan_entry)
	if entry.final_eb_bom_generator != frappe.db.get_value("SmartEdge Production Job", doc.source_production_job, "final_eb_bom_generator"):
		frappe.throw(_("Rework operation {0} does not belong to the roll's production.").format(rework_plan_entry))
	if entry.status in {"Completed", "Cancelled", "In Progress"}:
		frappe.throw(_("Rework operation {0} is not available for a new execution.").format(entry.operation))
	if "rework" not in str(entry.operation or "").lower():
		frappe.throw(_("Rework must use a configured Rework operation."))
	if entry.get("stock_movement_type") != "Non-Stock Operation":
		frappe.throw(_("Rework operation {0} must be non-stock.").format(entry.operation))
	if not entry.workstation:
		frappe.throw(_("Configure a workstation for Rework before starting it."))

	if not frappe.get_meta("SmartEdge Production Job", cached=True).has_field("rework_roll"):
		frappe.throw(_("Rework job fields are not synchronized on this site."))
	active = frappe.get_all(
		"SmartEdge Production Job",
		filters={"rework_roll": roll, "status": ["not in", ["Completed", "Cancelled"]]},
		fields=["name"],
		limit_page_length=2,
	)
	if active:
		frappe.throw(_("Active Rework Production Job {0} already exists for roll {1}.").format(active[0].name, roll))

	from smart_edge_custom.manufacturing.planning import start_production_job
	result = start_production_job(
		entry.name,
		operator_employee=operator_employee,
		workstation=workstation or entry.workstation,
	)
	job = frappe.get_doc("SmartEdge Production Job", result["production_job"])
	job.rework_roll = roll
	job.rework_quality_inspection = quality_inspection
	job.input_production_lot = doc.source_production_lot
	job.actual_qty_meter = flt(doc.current_meter_quantity)
	job.good_qty = flt(doc.current_meter_quantity)
	job.planned_qty_meter = flt(doc.current_meter_quantity)
	job.save(ignore_permissions=True)
	return {"production_job": job.name, "plan_entry": entry.name, "roll": roll, "quality_inspection": quality_inspection, "status": job.status}


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


def link_packed_rolls_to_finished_batch(stock_entry, production_name=None):
	"""Link packed rolls to the submitted FG Batch evidenced by one Stock Entry.

	The source Batch remains the Extrusion traceability Batch.  This optional field
	is populated only after a submitted receipt and only for rolls owned by Packing
	jobs linked to that receipt.  Sites without the migrated field remain fully
	compatible until the schema is synchronized.
	"""
	if not frappe.db.exists("Stock Entry", stock_entry):
		frappe.throw(_("Stock Entry {0} does not exist.").format(stock_entry))
	if frappe.db.get_value("Stock Entry", stock_entry, "docstatus") != 1:
		frappe.throw(_("Finished Goods Batch links require a submitted Stock Entry."))
	if not frappe.get_meta(ROLL).has_field("final_fg_batch"):
		return {"linked": 0, "field_missing": 1}

	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements
	stock_doc = frappe.get_doc("Stock Entry", stock_entry)
	outputs = [
		row for row in extract_batch_movements(stock_doc)
		if row.get("direction") == "Output" and row.get("batch_no")
	]
	batch_names = list(dict.fromkeys(row["batch_no"] for row in outputs))
	if len(batch_names) != 1:
		frappe.throw(_("Submitted Stock Entry {0} must identify exactly one finished-goods Batch.").format(stock_entry))
	batch_name = batch_names[0]
	batch = frappe.db.get_value("Batch", batch_name, ["item", "reference_name"], as_dict=True)
	if not batch:
		frappe.throw(_("Finished Goods Batch {0} does not exist.").format(batch_name))
	if production_name:
		production_item = frappe.db.get_value("Final EB BOM Generator", production_name, "finished_item")
		if production_item and production_item != batch.item:
			frappe.throw(_("Finished Goods Batch {0} does not belong to production {1}.").format(batch_name, production_name))

	job_filters = {"manufacturing_stock_entry": stock_entry, "status": "Completed"}
	if production_name:
		job_filters["final_eb_bom_generator"] = production_name
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters=job_filters,
		fields=["name", "operation", "final_eb_bom_generator"],
		limit_page_length=500,
	)
	linked = 0
	for job in jobs:
		if "pack" not in str(job.operation or "").lower():
			continue
		rolls = frappe.get_all(
			ROLL,
			filters={
				"packing_job": job.name,
				"qc_status": "Passed",
				"process_status": "Packed",
				"packing_status": "Packed",
			},
			fields=["name", "finished_item", "final_fg_batch"],
			limit_page_length=500,
		)
		for roll in rolls:
			if roll.finished_item != batch.item:
				frappe.throw(_("Roll {0} does not belong to finished item {1}.").format(roll.name, batch.item))
			if roll.final_fg_batch and roll.final_fg_batch != batch_name:
				frappe.throw(_("Roll {0} is already linked to another Finished Goods Batch.").format(roll.name))
			if not roll.final_fg_batch:
				frappe.db.set_value(ROLL, roll.name, "final_fg_batch", batch_name, update_modified=False)
			linked += 1
	return {"linked": linked, "batch": batch_name, "field_missing": 0}


def set_roll_qc_decision(roll, decision, quality_inspection=None, employee=None, reason=None, remarks=None):
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
	_append_history(doc, "QC Decision", previous, doc.process_status, doc.current_meter_quantity, doc.current_meter_quantity, related_quality_inspection=quality_inspection, employee=employee, reason=reason, remarks=remarks)
	_save_roll(doc)
	return doc.name


def reconcile_quantity_qc_decision(
	quality_inspection,
	source_production_job,
	source_production_lot,
	inspected_qty,
	accepted_qty,
	rejected_qty,
	rework_qty,
	employee=None,
	reason=None,
	remarks=None,
):
	"""Reconcile one quantity-level reinspection to its single rework roll.

	This is deliberately narrower than a general roll split.  When the complete
	reinspection quantity maps to exactly one unresolved rework roll and contains
	no remaining rework, the accepted portion can safely become the current
	quantity of that same Roll ID.  The original quantity and genealogy remain
	unchanged; the rejected loss is recorded in the existing history child table.
	Ambiguous or multi-roll quantities are left to the roll-wise workflow.
	"""
	if not source_production_job or flt(rework_qty) > 0:
		return None
	inspected = _meter(inspected_qty)
	accepted = _meter(accepted_qty)
	rejected = _meter(rejected_qty)
	if inspected <= 0 or abs(accepted + rejected - inspected) > 0.000001:
		return None

	rolls = get_rolls_for_qc_source(source_production_job, source_production_lot)
	for row in rolls:
		doc = frappe.get_doc(ROLL, row.name)
		history = getattr(doc, "operation_history", None)
		if history is None:
			history = doc.get("operation_history") or []
		if any(item.get("related_quality_inspection") == quality_inspection for item in history):
			return doc.name

	candidates = [
		row for row in rolls
		if row.qc_status == "Rework Required"
		and row.process_status == "In Rework"
		and abs(flt(row.current_meter_quantity) - inspected) <= 0.000001
	]
	if len(candidates) != 1:
		return None
	doc = frappe.get_doc(ROLL, candidates[0].name)

	previous_status = doc.process_status
	doc.current_meter_quantity = accepted if accepted > 0 else 0
	if accepted > 0:
		doc.qc_status = "Passed"
		doc.process_status = "Available for Packing"
		doc.packing_status = "Available"
	else:
		doc.qc_status = "Rejected"
		doc.process_status = "Rejected"
		doc.packing_status = "Not Available"
	history_remarks = json.dumps(
		{
			"accepted_meter_quantity": accepted,
			"rejected_meter_quantity": rejected,
			"rework_meter_quantity": flt(rework_qty),
			"remarks": remarks,
		},
		sort_keys=True,
	)
	_append_history(
		doc,
		"Re-QC Decision",
		previous_status,
		doc.process_status,
		flt(candidates[0].current_meter_quantity),
		doc.current_meter_quantity,
		meter_loss=rejected,
		related_quality_inspection=quality_inspection,
		employee=employee,
		reason=reason,
		remarks=history_remarks,
	)
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
	if getattr(getattr(job, "meta", None), "has_field", lambda _field: False)("rework_roll") and job.get("rework_roll") != roll:
		frappe.throw(_("Rework Production Job {0} is linked to another roll.").format(rework_production_job))
	if getattr(job, "final_eb_bom_generator", None) and job.final_eb_bom_generator != frappe.db.get_value("SmartEdge Production Job", doc.source_production_job, "final_eb_bom_generator"):
		frappe.throw(_("Rework Production Job {0} does not belong to roll {1}'s production.").format(rework_production_job, roll))
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
