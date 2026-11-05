"""Mandatory in-process quality gates for SmartEdge route operations.

The standard Quality Inspection document remains the auditable inspection
record.  The custom fields installed by the Phase 4 patch carry route, lot and
quantity information that ERPNext's purchase/delivery inspection workflow does
not model.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, flt, format_datetime, now_datetime
from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection

QC_ROLE = "Quality Manager"
SOURCE_QC_CONTEXT_FIELDS = {
	"quality_inspection",
	"qc_status",
	"qc_display_status",
	"qc_notification_warning",
	"eligible_qty",
	"accepted_qty",
	"rejected_qty",
	"rework_qty",
	"qc_employee",
	"inspection_datetime",
	"can_review",
}
ACTIVE_STATUSES = ("Pending", "Under Review")
FINAL_STATUSES = ("Accepted", "Rejected", "Rework Required")
EPSILON = 0.000001


class SmartEdgeQualityInspection(QualityInspection):
	"""Retain ERPNext behavior except its child-row assumption for our job link."""

	def set_child_row_reference(self):
		if self.reference_type == "SmartEdge Production Job":
			return
		return super().set_child_row_reference()

	def update_qc_reference(self, remove_reference=False):
		if self.reference_type == "SmartEdge Production Job":
			return
		return super().update_qc_reference(remove_reference=remove_reference)

	def on_cancel(self):
		if self.get("custom_smartedge_qc_key"):
			frappe.throw(_("SmartEdge route QC inspections cannot be cancelled; preserve the audit history."))
		return super().on_cancel()

	def before_submit(self):
		if self.get("custom_smartedge_qc_key") and not self.flags.get("smartedge_qc_api"):
			frappe.throw(_("Use SmartEdge Submit QC Decision; route inspections remain controlled audit records."))
		return super().before_submit()

	def on_trash(self):
		if self.get("custom_smartedge_qc_key"):
			frappe.throw(_("SmartEdge route QC inspections cannot be deleted; preserve the audit history."))
		return super().on_trash()


def is_qc_entry(entry):
	from smart_edge_custom.manufacturing.production_lot import is_qc_operation

	return bool(entry and is_qc_operation(entry))


def require_qc_authority():
	if frappe.session.user == "Administrator" or QC_ROLE in frappe.get_roles():
		return
	frappe.throw(_("Only users with the Quality Manager role may perform this QC action."), frappe.PermissionError)


def get_current_employee(user=None):
	user = user or frappe.session.user
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")


def validate_qc_employee(employee):
	if not employee:
		frappe.throw(_("QC Employee is required. Link an active Employee to the current user or select one."))
	row = frappe.db.get_value("Employee", employee, ["name", "status", "user_id"], as_dict=True)
	if not row or row.status != "Active":
		frappe.throw(_("Select an active QC Employee."))
	if frappe.session.user != "Administrator" and row.user_id and row.user_id != frappe.session.user:
		frappe.throw(_("QC Employee must be the Employee linked to the logged-in user."))
	return employee


def _source_details(qc_entry, source_job=None):
	from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry

	previous = get_previous_mandatory_entry(qc_entry)
	if not previous:
		frappe.throw(_("QC checkpoint {0} has no manufacturing predecessor.").format(qc_entry.operation))
	source_job = source_job or qc_entry.get("released_from_production_job")
	if not source_job:
		source_job = frappe.db.get_value(
			"SmartEdge Production Job", {"plan_entry": previous.name, "status": "Completed"}, "name"
		)
	if not source_job:
		frappe.throw(_("Complete the preceding manufacturing operation before activating QC."))
	job = frappe.get_doc("SmartEdge Production Job", source_job)
	lot = job.get("output_production_lot") or frappe.db.get_value(
		"SmartEdge Production Lot", {"production_job": job.name}, "name"
	)
	batch = None
	if lot and frappe.get_meta("Batch", cached=True).has_field("custom_operation_lot"):
		batch = frappe.db.get_value("Batch", {"custom_operation_lot": lot}, "name")
	return previous, job, lot, batch


def _next_key(plan_entry):
	count = frappe.db.count("Quality Inspection", {"custom_qc_plan_entry": plan_entry})
	return f"smartedge-qc:{plan_entry}:{count + 1}"


def _save_api(doc, updates):
	doc.update(updates)
	doc.flags.smartedge_qc_api = True
	doc.save(ignore_permissions=True)
	return doc


def _lock_inspection(name):
	frappe.db.get_value("Quality Inspection", name, "name", for_update=True)
	return frappe.get_doc("Quality Inspection", name)


def _new_inspection(qc_entry, eligible_qty, source_job=None, source_qc=None):
	previous, job, lot, batch = _source_details(qc_entry, source_job)
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		qc_entry.final_eb_bom_generator,
		["finished_item", "company"],
		as_dict=True,
	)
	doc = frappe.new_doc("Quality Inspection")
	doc.inspection_type = "In Process"
	doc.reference_type = "SmartEdge Production Job"
	doc.reference_name = job.name
	doc.item_code = production.finished_item
	doc.company = production.company
	doc.batch_no = batch
	doc.sample_size = flt(eligible_qty)
	doc.inspected_by = frappe.session.user
	doc.manual_inspection = 1
	# ERPNext requires a valid standard status even while the SmartEdge QC
	# lifecycle is still Pending.  Keep the custom field authoritative for the
	# workflow and use the valid draft value accepted by the core DocType.
	doc.status = "Accepted"
	doc.custom_smartedge_qc_key = _next_key(qc_entry.name)
	doc.custom_final_eb_bom_generator = qc_entry.final_eb_bom_generator
	doc.custom_qc_plan_entry = qc_entry.name
	doc.custom_source_production_job = job.name
	doc.custom_source_production_lot = lot
	doc.custom_source_batch = batch
	doc.custom_qc_operation = qc_entry.operation
	doc.custom_smartedge_qc_status = "Pending"
	doc.custom_eligible_qty = flt(eligible_qty)
	doc.custom_source_qc = source_qc
	doc.insert(ignore_permissions=True)
	if lot:
		frappe.db.set_value("SmartEdge Production Lot", lot, "quality_inspection", doc.name, update_modified=False)
	_notify_quality_managers(doc, qc_entry, job, production)
	return doc


def ensure_pending_inspection(plan_entry, source_job=None):
	qc_entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry) if isinstance(plan_entry, str) else plan_entry
	if not is_qc_entry(qc_entry):
		return None
	existing = frappe.get_all(
		"Quality Inspection",
		filters={"custom_qc_plan_entry": qc_entry.name, "custom_smartedge_qc_status": ["in", ACTIVE_STATUSES]},
		fields=["name", "custom_smartedge_qc_status"],
		order_by="creation asc",
		limit_page_length=1,
	)
	if existing:
		return frappe.get_doc("Quality Inspection", existing[0].name)
	checkpoint_qty = flt(qc_entry.get("released_qty_meter"))
	if checkpoint_qty <= 0:
		return None
	root_inspected = _root_inspected_quantity(qc_entry.name)
	remaining = max(checkpoint_qty - root_inspected, 0)
	if remaining <= EPSILON:
		return None
	return _new_inspection(qc_entry, remaining, source_job=source_job)


def activate_qc_after_completion(job):
	"""Create the one pending checkpoint reached by a completed operation."""
	from smart_edge_custom.manufacturing.planning import get_next_mandatory_entry

	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	next_entry = get_next_mandatory_entry(entry)
	if next_entry and is_qc_entry(next_entry):
		return ensure_pending_inspection(next_entry, source_job=job.name)
	return None


@frappe.whitelist()
def begin_review(quality_inspection, qc_employee=None):
	require_qc_authority()
	doc = _lock_inspection(quality_inspection)
	_validate_smartedge_inspection(doc)
	if doc.custom_smartedge_qc_status == "Under Review":
		return inspection_context(doc)
	if doc.custom_smartedge_qc_status != "Pending":
		frappe.throw(_("Only a pending QC inspection can begin review."))
	employee = validate_qc_employee(qc_employee or get_current_employee())
	_save_api(doc, {
		"custom_smartedge_qc_status": "Under Review",
		"custom_qc_employee": employee,
		"custom_inspection_datetime": now_datetime(),
		"inspected_by": frappe.session.user,
	})
	return inspection_context(doc)


def validate_quantities(eligible, inspected, accepted, rejected, rework):
	values = {
		_("Inspected Quantity"): flt(inspected),
		_("Accepted Quantity"): flt(accepted),
		_("Rejected Quantity"): flt(rejected),
		_("Rework Quantity"): flt(rework),
	}
	for label, value in values.items():
		if value < 0:
			frappe.throw(_("{0} cannot be negative.").format(label))
	if values[_("Inspected Quantity")] <= 0:
		frappe.throw(_("Inspected Quantity must be greater than zero."))
	classified = values[_("Accepted Quantity")] + values[_("Rejected Quantity")] + values[_("Rework Quantity")]
	if abs(classified - values[_("Inspected Quantity")]) > EPSILON:
		frappe.throw(_("Inspected Quantity must equal Accepted + Rejected + Rework Quantity."))
	if values[_("Inspected Quantity")] > flt(eligible) + EPSILON:
		frappe.throw(_("Inspected Quantity cannot exceed the eligible quantity {0}.").format(flt(eligible)))
	return values


def _decision_status(accepted, rejected, rework):
	if flt(rework) > EPSILON:
		return "Rework Required"
	if flt(accepted) > EPSILON:
		return "Accepted"
	if flt(rejected) > EPSILON:
		return "Rejected"
	frappe.throw(_("Classify the inspected quantity as accepted, rejected, or rework."))


@frappe.whitelist()
def submit_decision(
	quality_inspection,
	inspected_qty,
	accepted_qty=0,
	rejected_qty=0,
	rework_qty=0,
	width=None,
	thickness=None,
	color_match=None,
	design_match=None,
	surface_appearance=None,
	print_quality=None,
	reason=None,
	remarks=None,
	qc_employee=None,
):
	require_qc_authority()
	doc = _lock_inspection(quality_inspection)
	_validate_smartedge_inspection(doc)
	if doc.custom_smartedge_qc_status != "Under Review":
		frappe.throw(_("QC must be Under Review before a decision is submitted."))
	employee = validate_qc_employee(qc_employee or doc.custom_qc_employee or get_current_employee())
	values = validate_quantities(doc.custom_eligible_qty, inspected_qty, accepted_qty, rejected_qty, rework_qty)
	if (values[_("Rejected Quantity")] > EPSILON or values[_("Rework Quantity")] > EPSILON) and not str(reason or "").strip():
		frappe.throw(_("Rejection / Rework Reason is required."))
	status = _decision_status(accepted_qty, rejected_qty, rework_qty)
	updates = {
		"custom_smartedge_qc_status": status,
		"custom_qc_employee": employee,
		"custom_inspection_datetime": now_datetime(),
		"custom_width": flt(width) if width not in (None, "") else None,
		"custom_thickness": flt(thickness) if thickness not in (None, "") else None,
		"custom_color_match": color_match,
		"custom_design_match": design_match,
		"custom_surface_appearance": surface_appearance,
		"custom_print_quality": print_quality,
		"custom_inspected_qty": values[_("Inspected Quantity")],
		"custom_accepted_qty": values[_("Accepted Quantity")],
		"custom_rejected_qty": values[_("Rejected Quantity")],
		"custom_rework_qty": values[_("Rework Quantity")],
		"custom_rejection_rework_reason": reason,
		"custom_qc_remarks": remarks,
		"sample_size": values[_("Inspected Quantity")],
		"status": "Rejected" if status == "Rejected" else "Accepted",
		"remarks": remarks,
	}
	_save_api(doc, updates)
	# Finalized SmartEdge decisions must use ERPNext's normal submission
	# lifecycle so the linked Quality Inspection is no longer left in Draft.
	# The API flag is narrowly scoped to this validated decision path; direct
	# manual submission remains blocked by SmartEdgeQualityInspection.
	if doc.docstatus == 0:
		doc.flags.smartedge_qc_api = True
		doc.submit()
	_close_pending_assignments(doc.name)
	if doc.custom_source_qc:
		source = _lock_inspection(doc.custom_source_qc)
		_save_api(source, {"custom_rework_resolved": 1})
	_sync_qc_release(doc.custom_qc_plan_entry)
	remaining = max(flt(doc.custom_eligible_qty) - values[_("Inspected Quantity")], 0)
	if remaining > EPSILON:
		_new_inspection(
			frappe.get_doc("SmartEdge Production Plan Entry", doc.custom_qc_plan_entry),
			remaining,
			source_job=doc.custom_source_production_job,
			source_qc=doc.custom_source_qc,
		)
	_finalize_qc_job_if_resolved(doc.custom_qc_plan_entry, quality_inspection)
	return get_qc_context(doc.custom_qc_plan_entry)


@frappe.whitelist()
def resubmit_rework(quality_inspection):
	require_qc_authority()
	doc = _lock_inspection(quality_inspection)
	_validate_smartedge_inspection(doc)
	if doc.custom_smartedge_qc_status != "Rework Required" or doc.custom_rework_resolved:
		frappe.throw(_("Only unresolved rework can be resubmitted for inspection."))
	if doc.custom_rework_resubmitted_to:
		return inspection_context(frappe.get_doc("Quality Inspection", doc.custom_rework_resubmitted_to))
	new_doc = _new_inspection(
		frappe.get_doc("SmartEdge Production Plan Entry", doc.custom_qc_plan_entry),
		doc.custom_rework_qty,
		source_job=doc.custom_source_production_job,
		source_qc=doc.name,
	)
	_save_api(doc, {"custom_rework_resubmitted_to": new_doc.name})
	return inspection_context(new_doc)


def _validate_smartedge_inspection(doc):
	if not doc.get("custom_qc_plan_entry") or not doc.get("custom_smartedge_qc_key"):
		frappe.throw(_("This Quality Inspection is not a SmartEdge route QC checkpoint."))


def _inspection_rows(plan_entry):
	return frappe.get_all(
		"Quality Inspection",
		filters={"custom_qc_plan_entry": plan_entry},
		fields=[
			"name", "custom_smartedge_qc_status", "custom_eligible_qty", "custom_inspected_qty",
			"custom_accepted_qty", "custom_rejected_qty", "custom_rework_qty", "custom_source_qc",
			"custom_rework_resolved", "custom_qc_employee", "custom_source_production_lot",
			"custom_source_production_job", "custom_source_batch", "custom_inspection_datetime",
			"docstatus", "status", "modified",
		],
		order_by="creation asc",
		limit_page_length=500,
	)


def _root_inspected_quantity(plan_entry):
	return sum(
		flt(row.custom_inspected_qty)
		for row in _inspection_rows(plan_entry)
		if not row.custom_source_qc and row.custom_smartedge_qc_status in FINAL_STATUSES
	)


def aggregate(plan_entry):
	rows = _inspection_rows(plan_entry)
	accepted = sum(flt(row.custom_accepted_qty) for row in rows if row.custom_smartedge_qc_status in FINAL_STATUSES)
	rejected = sum(flt(row.custom_rejected_qty) for row in rows if row.custom_smartedge_qc_status in FINAL_STATUSES)
	rework = sum(
		flt(row.custom_rework_qty)
		for row in rows
		if row.custom_smartedge_qc_status == "Rework Required" and not row.custom_rework_resolved
	)
	active = [row for row in rows if row.custom_smartedge_qc_status in ACTIVE_STATUSES]
	latest = sorted(rows, key=lambda row: row.modified or "")[-1] if rows else None
	return frappe._dict({
		"rows": rows,
		"accepted_qty": accepted,
		"rejected_qty": rejected,
		"unresolved_rework_qty": rework,
		"root_inspected_qty": sum(
			flt(row.custom_inspected_qty) for row in rows
			if not row.custom_source_qc and row.custom_smartedge_qc_status in FINAL_STATUSES
		),
		"active": active,
		"latest": latest,
	})


def _sync_qc_release(plan_entry):
	from smart_edge_custom.manufacturing.planning import get_next_mandatory_entry, update_generator_planning_status

	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	data = aggregate(plan_entry)
	accepted = flt(data.accepted_qty)
	qc_job = frappe.db.get_value(
		"SmartEdge Production Job", {"plan_entry": entry.name, "status": ["!=", "Cancelled"]}, "name"
	)
	next_entry = get_next_mandatory_entry(entry)
	input_kg = flt(entry.get("released_qty_kg"))
	input_meter = flt(entry.get("released_qty_meter"))
	if next_entry:
		next_entry.released_from_plan_entry = entry.name
		next_entry.released_from_production_job = qc_job
		next_entry.released_qty_meter = accepted
		next_entry.released_qty_kg = input_kg * accepted / input_meter if input_meter else 0
		if accepted > EPSILON and next_entry.status in {"Pending", "Planned", "On Hold"}:
			next_entry.status = "Ready"
		next_entry.save(ignore_permissions=True)
		update_generator_planning_status(next_entry.final_eb_bom_generator)
	if qc_job:
		frappe.db.set_value("SmartEdge Production Job", qc_job, {
			"released_qty_meter": accepted,
			"released_qty_kg": (input_kg * accepted / input_meter if input_meter else 0),
			"released_to_plan_entry": next_entry.name if next_entry else None,
		}, update_modified=False)


def _finalize_qc_job_if_resolved(plan_entry, quality_inspection):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	data = aggregate(plan_entry)
	checkpoint_qty = flt(entry.get("released_qty_meter"))
	if data.active or data.unresolved_rework_qty > EPSILON or data.root_inspected_qty + EPSILON < checkpoint_qty:
		return
	job_name = frappe.db.get_value(
		"SmartEdge Production Job", {"plan_entry": plan_entry, "status": ["in", ["In Progress", "Paused"]]}, "name"
	)
	if not job_name:
		return
	job = frappe.get_doc("SmartEdge Production Job", job_name)
	job.actual_qty_meter = checkpoint_qty
	job.actual_qty_kg = flt(entry.get("released_qty_kg"))
	job.good_qty = data.accepted_qty
	job.rejected_qty = data.rejected_qty
	job.rework_qty = 0
	if job.meta.has_field("quality_inspection"):
		job.quality_inspection = quality_inspection
	previous_flag = getattr(frappe.flags, "smartedge_qc_decision", False)
	frappe.flags.smartedge_qc_decision = True
	try:
		job.complete_job("Full Completion")
	finally:
		frappe.flags.smartedge_qc_decision = previous_flag


def validate_downstream_quantity(entry, quantity):
	previous = None
	from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry

	previous = get_previous_mandatory_entry(entry)
	if not previous or not is_qc_entry(previous):
		return
	accepted = flt(aggregate(previous.name).accepted_qty)
	if flt(quantity) > accepted + EPSILON:
		frappe.throw(_("QC accepted quantity is {0}; downstream processing of {1} is not allowed.").format(accepted, flt(quantity)))


def validate_final_qc_eligibility(production, finished_qty):
	entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"final_eb_bom_generator": production, "status": ["!=", "Cancelled"]},
		fields=["name", "operation", "operation_row", "planning_category"],
		order_by="sequence asc",
	)
	for entry in entries:
		if is_qc_entry(entry) and flt(aggregate(entry.name).accepted_qty) + EPSILON < flt(finished_qty):
			frappe.throw(_("Final stock posting is blocked: {0} has accepted only {1} of {2}.").format(
				entry.operation, flt(aggregate(entry.name).accepted_qty), flt(finished_qty)
			))


def inspection_context(doc):
	return {
		"quality_inspection": doc.name,
		"qc_status": doc.custom_smartedge_qc_status,
		"eligible_qty": flt(doc.custom_eligible_qty),
		"qc_employee": doc.custom_qc_employee,
		"production_lot": doc.custom_source_production_lot,
		"batch": doc.custom_source_batch,
	}


@frappe.whitelist()
def get_qc_context(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("read")
	data = aggregate(plan_entry)
	active = data.active[0] if data.active else None
	latest = data.latest
	unresolved_rework = next((
		row for row in reversed(data.rows)
		if row.custom_smartedge_qc_status == "Rework Required" and not row.custom_rework_resolved
	), None)
	current = active or unresolved_rework or latest
	production = frappe.db.get_value(
		"Final EB BOM Generator", entry.final_eb_bom_generator,
		["finished_item", "finished_item_name"], as_dict=True,
	) or frappe._dict()
	# A QC checkpoint is not actionable until the preceding manufacturing
	# operation has completed and released output.  Do not surface a generic
	# "QC Pending" state while that operation is still running; doing so makes
	# the predecessor card appear blocked before it has produced anything.
	output_released = bool(
		flt(entry.get("released_qty_meter")) > EPSILON
		or flt(entry.get("released_qty_kg")) > EPSILON
	)
	display_status = get_qc_display_status(current)
	qc_status = current.custom_smartedge_qc_status if current else "Pending"
	if not current and not output_released:
		display_status = "Awaiting Manufacturing Output"
		qc_status = "Blocked"
	can_view_inspection = bool(current and frappe.has_permission("Quality Inspection", "read"))
	notification_warning = None
	if display_status == "QC Pending" and not get_qc_recipient_users():
		notification_warning = _(
			"No enabled user with the Quality Manager role is configured for QC notifications."
		)
	return {
		"is_qc": is_qc_entry(entry),
		"qc_required": True,
		"quality_inspection": current.name if can_view_inspection else None,
		"qc_status": qc_status,
		"qc_display_status": display_status,
		"qc_notification_warning": notification_warning,
		"eligible_qty": flt(active.custom_eligible_qty) if active else max(
			flt(entry.get("released_qty_meter")) - flt(data.root_inspected_qty), 0
		),
		"accepted_qty": flt(data.accepted_qty),
		"rejected_qty": flt(data.rejected_qty),
		"rework_qty": flt(data.unresolved_rework_qty),
		"production": entry.final_eb_bom_generator,
		"finished_item": production.finished_item,
		"finished_item_name": production.finished_item_name,
		"production_job": current.custom_source_production_job if current else None,
		"operation": entry.operation,
		"workstation": entry.workstation,
		"production_lot": current.custom_source_production_lot if current else None,
		"batch": current.custom_source_batch if current else None,
		"qc_employee": current.custom_qc_employee if current and can_view_inspection else None,
		"inspection_datetime": current.custom_inspection_datetime if current and can_view_inspection else None,
		"can_review": frappe.session.user == "Administrator" or QC_ROLE in frappe.get_roles(),
	}


def attach_qc_details(rows):
	rows_by_name = {row.name: row for row in rows}
	qc_rows = [row for row in rows if is_qc_entry(row)]
	for row in qc_rows:
		context = get_qc_context(row.name)
		for key, value in context.items():
			row[key] = value
		row.qc_checkpoint_operation = row.operation
		previous = None
		from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry

		previous = get_previous_mandatory_entry(row)
		source_row = rows_by_name.get(previous.name) if previous else None
		# Only attach the checkpoint summary to its source operation after the
		# source has completed.  Before completion the QC operation may already
		# be planned, but there is no eligible output to inspect yet.
		source_completed = bool(
			source_row
			and (
				source_row.get("status") == "Completed"
				or source_row.get("job_status") == "Completed"
			)
		)
		if source_completed:
			for key, value in context.items():
				if key in SOURCE_QC_CONTEXT_FIELDS:
					source_row[key] = value
			source_row.is_qc = False
			source_row.qc_required = True
			source_row.qc_checkpoint_operation = row.operation
	for row in rows:
		if is_qc_entry(row):
			continue
		from smart_edge_custom.manufacturing.planning import get_previous_mandatory_entry
		previous = get_previous_mandatory_entry(row)
		if previous and is_qc_entry(previous) and flt(row.get("released_qty_meter")) <= EPSILON:
			row.qc_blocked = 1
			row.qc_block_reason = _("QC approval is pending for the previous manufacturing operation. Complete and accept QC before proceeding.")


def get_qc_display_status(inspection=None):
	if not inspection:
		return "QC Pending"
	custom_status = inspection.get("custom_smartedge_qc_status")
	if custom_status == "Accepted":
		return "QC Approved"
	if custom_status == "Rejected":
		return "QC Rejected"
	if custom_status in {"Pending", "Under Review", "Rework Required"}:
		return "QC Pending"
	if inspection.get("docstatus") == 1 and inspection.get("status") == "Accepted":
		return "QC Approved"
	if inspection.get("docstatus") == 1 and inspection.get("status") == "Rejected":
		return "QC Rejected"
	return "QC Pending"


def get_qc_recipient_users():
	cached = getattr(frappe.flags, "smartedge_qc_recipients", None)
	if cached is not None:
		return cached
	users = set(frappe.get_all("Has Role", filters={"role": QC_ROLE, "parenttype": "User"}, pluck="parent"))
	users.discard("Guest")
	users = sorted(user for user in users if frappe.db.get_value("User", user, "enabled"))
	frappe.flags.smartedge_qc_recipients = users
	return users


def _notify_quality_managers(doc, entry, job, production):
	users = get_qc_recipient_users()
	if not users:
		return
	item = production.finished_item
	subject = _("QC Inspection Pending — {0} — {1}").format(job.name, item)
	completion = format_datetime(job.actual_end) if job.actual_end else "-"
	message = _(
		"A production operation has been completed and is awaiting Quality Control inspection."
	)
	message += "<br><br>" + "<br>".join((
		f"<b>{escape_html(_('Production Job'))}:</b> {escape_html(job.name)}",
		f"<b>{escape_html(_('Finished Item'))}:</b> {escape_html(item)}",
		f"<b>{escape_html(_('Operation'))}:</b> {escape_html(job.operation)}",
		f"<b>{escape_html(_('Workstation'))}:</b> {escape_html(job.workstation or '-')}",
		f"<b>{escape_html(_('Completed Quantity'))}:</b> {flt(doc.custom_eligible_qty)}",
		f"<b>{escape_html(_('Completion Date and Time'))}:</b> {escape_html(completion)}",
		f"<b>{escape_html(_('QC Status'))}:</b> {escape_html(_('Pending'))}",
	))
	message += "<br><br>" + escape_html(_("Please review and complete the required Quality Inspection."))
	for user in users:
		filters = {
			"for_user": user,
			"type": "Alert",
			"document_type": "Quality Inspection",
			"document_name": doc.name,
		}
		if frappe.db.exists("Notification Log", filters):
			continue
		notification = frappe.new_doc("Notification Log")
		notification.subject = subject
		notification.for_user = user
		notification.type = "Alert"
		notification.email_content = message
		notification.document_type = "Quality Inspection"
		notification.document_name = doc.name
		notification.from_user = frappe.session.user
		notification.insert(ignore_permissions=True)


def _close_pending_assignments(quality_inspection):
	for name in frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "Quality Inspection",
			"reference_name": quality_inspection,
			"status": "Open",
		},
		pluck="name",
	):
		frappe.db.set_value("ToDo", name, "status", "Closed", update_modified=False)


@frappe.whitelist()
def get_job_qc_visibility(production_job):
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("read")
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	from smart_edge_custom.manufacturing.planning import get_next_mandatory_entry

	qc_entry = entry if is_qc_entry(entry) else get_next_mandatory_entry(entry)
	if not qc_entry or not is_qc_entry(qc_entry):
		return {"qc_required": False}
	context = get_qc_context(qc_entry.name)
	return {
		"qc_required": True,
		"qc_display_status": context.get("qc_display_status"),
		"quality_inspection": context.get("quality_inspection"),
		"production_job": job.name,
		"operation": job.operation,
		"workstation": job.workstation,
		"completed_quantity": flt(job.good_qty) or flt(job.actual_qty_meter),
		"notification_warning": context.get("qc_notification_warning"),
	}


def validate_quality_inspection(doc, method=None):
	"""Protect SmartEdge linkage and quantities when a record is edited directly."""
	if not doc.get("custom_smartedge_qc_key"):
		return
	if not doc.get("custom_qc_plan_entry") or not is_qc_entry(
		frappe.get_doc("SmartEdge Production Plan Entry", doc.custom_qc_plan_entry)
	):
		frappe.throw(_("SmartEdge QC must reference a valid QC plan entry."))
	protected_fields = (
		"reference_type", "reference_name", "item_code", "batch_no", "status",
		"custom_final_eb_bom_generator", "custom_qc_plan_entry", "custom_source_production_job",
		"custom_source_production_lot", "custom_source_batch", "custom_qc_operation",
		"custom_smartedge_qc_status", "custom_qc_employee", "custom_inspection_datetime",
		"custom_width", "custom_thickness", "custom_color_match", "custom_design_match",
		"custom_surface_appearance", "custom_print_quality", "custom_eligible_qty",
		"custom_inspected_qty", "custom_accepted_qty", "custom_rejected_qty", "custom_rework_qty",
		"custom_rejection_rework_reason", "custom_qc_remarks", "custom_source_qc",
		"custom_rework_resolved", "custom_rework_resubmitted_to",
	)
	if not doc.is_new() and not doc.flags.get("smartedge_qc_api") and any(
		doc.has_value_changed(fieldname) for fieldname in protected_fields
	):
		frappe.throw(_("Use the SmartEdge QC review and decision actions to update this inspection."))
