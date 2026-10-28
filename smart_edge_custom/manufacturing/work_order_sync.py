from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


MATERIAL_PLAN_TYPE = "Required Material"
TERMINAL_WORK_ORDER_STATUSES = {"Cancelled", "Stopped"}
QUANTITY_TOLERANCE = 0.000001


def get_work_order_progress(work_order):
	"""Return manufacturing progress backed by submitted ERPNext transactions."""
	work_order = (
		frappe.get_doc("Work Order", work_order) if isinstance(work_order, str) else work_order
	)
	qty = flt(work_order.qty)
	produced_qty = flt(work_order.produced_qty)
	manufacture_entries = frappe.get_all(
		"Stock Entry",
		filters={
			"work_order": work_order.name,
			"purpose": "Manufacture",
			"docstatus": 1,
		},
		fields=["name", "fg_completed_qty"],
		order_by="posting_date asc, posting_time asc, creation asc",
		limit_page_length=1000,
	)
	posted_qty = sum(flt(row.fg_completed_qty) for row in manufacture_entries)
	verified_qty = min(produced_qty, posted_qty)
	cancelled = cint(work_order.docstatus) == 2 or work_order.status in TERMINAL_WORK_ORDER_STATUSES
	completed = bool(
		cint(work_order.docstatus) == 1
		and work_order.status == "Completed"
		and qty > 0
		and produced_qty + QUANTITY_TOLERANCE >= qty
		and posted_qty + QUANTITY_TOLERANCE >= qty
	)
	partial = bool(not cancelled and not completed and verified_qty > QUANTITY_TOLERANCE)
	return frappe._dict(
		{
			"work_order": work_order.name,
			"work_order_status": work_order.status,
			"docstatus": cint(work_order.docstatus),
			"qty": qty,
			"produced_qty": produced_qty,
			"posted_qty": posted_qty,
			"verified_qty": verified_qty,
			"remaining_qty": max(qty - verified_qty, 0),
			"completed": completed,
			"partial": partial,
			"cancelled": cancelled,
			"manufacture_stock_entry": manufacture_entries[-1].name if manufacture_entries else None,
		}
	)


def get_entry_target_status(entry, progress, job=None, completion_supported=True):
	if progress.cancelled:
		return "Cancelled"
	if progress.completed and completion_supported:
		return "Completed"
	if progress.completed and not completion_supported:
		return entry.get("status")
	job_status = job.get("status") if job else None
	if job_status == "Paused":
		return "On Hold"
	if job_status == "In Progress":
		return "In Progress"
	if progress.partial:
		return "On Hold"
	return "Planned" if entry.get("planned_date") else "Pending"


def operation_completion_is_supported(work_order, entry):
	operations = work_order.get("operations") or []
	if len(operations) <= 1:
		return True
	job_cards = frappe.get_all(
		"Job Card",
		filters={
			"work_order": work_order.name,
			"operation": entry.operation,
			"docstatus": 1,
		},
		fields=["name", "status", "for_quantity", "total_completed_qty"],
		limit_page_length=100,
	)
	return any(
		row.status == "Completed"
		and flt(row.total_completed_qty) + QUANTITY_TOLERANCE >= flt(row.for_quantity)
		for row in job_cards
	)


def get_authoritative_job_for_sync(jobs):
	active = [row for row in jobs if row.status not in {"Completed", "Cancelled"}]
	if len(active) == 1:
		return active[0]
	if len(active) > 1:
		return None
	return jobs[0] if jobs else None


def sync_work_order_to_planning(work_order, dry_run=False, refresh_readiness=True, publish=True):
	"""Idempotently reconcile linked planning records from verified ERPNext progress."""
	if getattr(frappe.flags, "smartedge_board_completion", False):
		return {"work_order": getattr(work_order, "name", work_order), "skipped": True, "changes": []}
	work_order = (
		frappe.get_doc("Work Order", work_order) if isinstance(work_order, str) else work_order
	)
	if not work_order or not work_order.name:
		return {"work_order": None, "changes": []}
	progress = get_work_order_progress(work_order)
	entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"work_order": work_order.name},
		fields=[
			"name",
			"status",
			"plan_type",
			"operation",
			"planned_date",
			"final_eb_bom_generator",
			"manufacture_stock_entry",
			"weight_per_batch",
		],
		order_by="creation asc",
		limit_page_length=100,
	)
	if not entries:
		return {"work_order": work_order.name, "progress": progress, "changes": []}
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": ["in", [entry.name for entry in entries]]},
		fields=[
			"name",
			"plan_entry",
			"status",
			"completion_type",
			"actual_qty_meter",
			"actual_qty_kg",
			"good_qty",
			"manufacturing_stock_entry",
			"stock_posted_qty",
		],
		order_by="modified desc",
		limit_page_length=500,
	)
	jobs_by_entry = defaultdict(list)
	for job in jobs:
		jobs_by_entry[job.plan_entry].append(job)

	changes = []
	productions = set()
	for entry in entries:
		entry_jobs = jobs_by_entry.get(entry.name, [])
		job = get_authoritative_job_for_sync(entry_jobs)
		completion_supported = operation_completion_is_supported(work_order, entry)
		target = get_entry_target_status(entry, progress, job, completion_supported)
		entry_updates = {}
		if entry.status != target:
			entry_updates["status"] = target
		if (
			progress.manufacture_stock_entry
			and not entry.manufacture_stock_entry
			and progress.verified_qty > QUANTITY_TOLERANCE
		):
			entry_updates["manufacture_stock_entry"] = progress.manufacture_stock_entry
		if entry_updates:
			changes.append(
				{
					"doctype": "SmartEdge Production Plan Entry",
					"name": entry.name,
					"updates": entry_updates,
				}
			)
			if not dry_run:
				lock_plan_entry(entry.name)
				apply_updates_if_changed("SmartEdge Production Plan Entry", entry.name, entry_updates)

		# A Required Material plan represents the single selected manufacturing
		# operation. For multi-operation Work Orders, Job Cards must confirm that
		# operation before its SmartEdge job can be completed.
		if entry.plan_type != MATERIAL_PLAN_TYPE:
			continue
		productions.add(entry.final_eb_bom_generator)
		for linked_job in entry_jobs:
			job_target = None
			job_updates = {}
			if (
				progress.verified_qty > QUANTITY_TOLERANCE
				and linked_job.status not in {"Completed", "Cancelled"}
			):
				batch_count = (
					progress.verified_qty / flt(entry.weight_per_batch)
					if flt(entry.weight_per_batch) > QUANTITY_TOLERANCE
					else 0
				)
				for fieldname, value in {
					"actual_qty_meter": batch_count,
					"actual_qty_kg": progress.verified_qty,
					"good_qty": progress.verified_qty,
					"manufacturing_stock_entry": progress.manufacture_stock_entry,
					"stock_posted_qty": progress.verified_qty,
				}.items():
					if linked_job.get(fieldname) != value:
						job_updates[fieldname] = value
			if progress.cancelled and linked_job.status != "Cancelled":
				job_target = "Cancelled"
			elif progress.completed and completion_supported and linked_job.status not in {
				"Completed",
				"Cancelled",
			}:
				job_target = "Completed"
				job_updates["completion_type"] = "Full Completion"
				job_updates["remaining_qty_meter"] = 0
				job_updates["remaining_qty_kg"] = 0
			if job_target:
				job_updates["status"] = job_target
			if job_updates:
				changes.append(
					{
						"doctype": "SmartEdge Production Job",
						"name": linked_job.name,
						"updates": job_updates,
					}
				)
				if not dry_run:
					apply_updates_if_changed("SmartEdge Production Job", linked_job.name, job_updates)

	if changes and not dry_run:
		if refresh_readiness:
			from smart_edge_custom.manufacturing.material_production import refresh_material_readiness

			for production in sorted(production for production in productions if production):
				refresh_material_readiness(production)
		if publish:
			frappe.publish_realtime(
				"smartedge_planning_updated",
				{"work_order": work_order.name, "plan_entries": [entry.name for entry in entries]},
				after_commit=True,
			)
	return {"work_order": work_order.name, "progress": progress, "changes": changes}


def apply_updates_if_changed(doctype, name, updates):
	if not updates:
		return
	current = frappe.db.get_value(doctype, name, list(updates), as_dict=True) or {}
	changed = {fieldname: value for fieldname, value in updates.items() if current.get(fieldname) != value}
	if changed:
		frappe.db.set_value(doctype, name, changed, update_modified=True)


def lock_plan_entry(plan_entry):
	frappe.db.sql(
		"select name from `tabSmartEdge Production Plan Entry` where name=%s for update",
		(plan_entry,),
	)


def reconcile_board_work_orders(week_start, week_end):
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"work_order": ["is", "set"], "status": ["!=", "Cancelled"]},
		fields=["work_order", "planned_date"],
		limit_page_length=1000,
	)
	names = {
		row.work_order
		for row in rows
		if not row.planned_date or getdate(week_start) <= getdate(row.planned_date) <= getdate(week_end)
	}
	return [
		sync_work_order_to_planning(name, refresh_readiness=True)
		for name in sorted(name for name in names if name)
	]


@frappe.whitelist()
def reconcile_work_order_planning(work_order=None, dry_run=1):
	frappe.has_permission("SmartEdge Production Plan Entry", "write", throw=True)
	dry_run = bool(cint(dry_run))
	if work_order:
		names = [work_order]
	else:
		names = frappe.get_all(
			"SmartEdge Production Plan Entry",
			filters={"work_order": ["is", "set"]},
			pluck="work_order",
			limit_page_length=5000,
		)
	return [
		sync_work_order_to_planning(name, dry_run=dry_run)
		for name in sorted(set(name for name in names if name))
	]
