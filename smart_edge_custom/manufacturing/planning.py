from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import math
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_time, getdate, now_datetime, nowdate
from erpnext.stock.doctype.item.item import get_uom_conv_factor

from smart_edge_custom.manufacturing.fifo_batch_selection import (
	get_fifo_batch_allocations,
	get_kg_to_stock_factor,
)
from smart_edge_custom.manufacturing.final_bom_generator import (
	get_generator_lifecycle_status,
	get_operation_planning_category,
	get_prepared_component_batch_weight,
)
from smart_edge_custom.manufacturing.machine_capacity import (
	ACTIVE_PLAN_STATUSES,
	ACTIVE_WORK_ORDER_STATUSES,
	attach_board_capacity_details,
	get_capacity_availability,
	get_schedule_capacity_projection,
	validate_workstation_time_window,
)
from smart_edge_custom.manufacturing.quantity import round_meter_quantity, whole_meter_not_exceeding
from smart_edge_custom.manufacturing.workstation_capacity import (
	get_capacity,
	lock_workstation,
)

PLANNING_STATUSES = ("Pending", "Ready", "Planned", "In Progress", "Completed", "On Hold", "Cancelled")

CATEGORY_COLORS = {
	"Extrusion": "blue",
	"Printing": "purple",
	"Mixing": "orange",
	"Packing": "teal",
	"QC": "green",
	"Slitting": "indigo",
	"Other": "gray",
}
MATERIAL_PLAN_TYPE = "Required Material"
MATERIAL_DISPLAY_ORDER = {"Compound": 1, "Base Colour": 2}
ACCOUNTED_OPERATION_STATUSES = {"Planned", "In Progress", "On Hold", "Completed"}


def planning_board_qr_verification_enabled():
	"""Feature flag for the Planning Board QR gate only; default is disabled."""
	return cint(frappe.conf.get("enable_planning_board_qr_verification", 0)) == 1
READY_OPERATION_STATUS = "Ready"
CAPACITY_CONFIRMATION_TTL_SECONDS = 300
CAPACITY_CONFIRMATION_CACHE_PREFIX = "smartedge:capacity-confirmation:"


@frappe.whitelist()
def get_weekly_planning_board(
	week_start=None,
	category=None,
	workstation=None,
	operation=None,
	status=None,
	finished_product=None,
	size=None,
	priority=None,
	customer=None,
	search=None,
):
	frappe.has_permission("SmartEdge Production Plan Entry", "read", throw=True)
	from smart_edge_custom.manufacturing.operator_access import is_planning_supervisor

	can_manage_planning = is_planning_supervisor()
	week_start = get_week_start(week_start)
	week_end = week_start + timedelta(days=6)
	from smart_edge_custom.manufacturing.work_order_sync import reconcile_board_work_orders

	reconciled = reconcile_board_work_orders(week_start, week_end) if can_manage_planning else []
	entries = get_plan_entries(
		week_start,
		week_end,
		include_related_operations=True,
		category=category,
		workstation=workstation,
		operation=operation,
		status=status,
		finished_product=finished_product,
		size=size,
		priority=priority,
		customer=customer,
		search=search,
	)
	partial_planning = get_partial_planning_summary(
		{row.final_eb_bom_generator for row in entries if row.get("plan_type") != MATERIAL_PLAN_TYPE},
		visible_entries=None if can_manage_planning else entries,
	)
	board = make_empty_board(week_start)
	board["work_order_warnings"] = [result for result in (reconciled or []) if result.get("missing")]
	board["all_operations"] = []
	board["can_manage_planning"] = can_manage_planning
	board["operator_restricted"] = not can_manage_planning

	for row in entries:
		card = make_operation_card(row)
		if row.get("plan_type") != MATERIAL_PLAN_TYPE:
			card["partial_planning"] = partial_planning.get(row.final_eb_bom_generator)
		board["all_operations"].append(card)
		if row.planned_date:
			date_key = str(row.planned_date)
			if date_key in board["days"]:
				board["days"][date_key]["operations"].append(card)
		else:
			if get_effective_entry_status(row) == "Completed":
				continue
			board["pending"].append(card)

	for day in board["days"].values():
		day["groups"] = group_operations(day["operations"])
		day["production_count"] = len({card["production"] for card in day["operations"]})
		day["operation_count"] = len(day["operations"])

	board["pending_count"] = len(board["pending"])
	board["week_start"] = str(week_start)
	board["week_end"] = str(week_end)
	site_now = now_datetime()
	board["site_today"] = str(getdate(site_now))
	next_site_midnight = datetime.combine(getdate(site_now) + timedelta(days=1), time.min)
	board["date_refresh_after_seconds"] = max(
		int((next_site_midnight - site_now).total_seconds()) + 2,
		1,
	)
	board["filters"] = {
		"categories": list(CATEGORY_COLORS),
		"workstations": (
			frappe.get_all("Workstation", filters={"disabled": 0}, pluck="name", order_by="name")
			if can_manage_planning else sorted({card.get("workstation") for card in board["all_operations"] if card.get("workstation")})
		),
		"operations": (
			frappe.get_all("Operation", pluck="name", order_by="name")
			if can_manage_planning else sorted({card.get("operation") for card in board["all_operations"] if card.get("operation")})
		),
		"statuses": PLANNING_STATUSES,
		"priorities": ("Urgent", "High", "Normal", "Low"),
	}
	return board


def get_week_start(value=None):
	date_value = getdate(value or nowdate())
	return date_value - timedelta(days=date_value.weekday())


def get_plan_entries(week_start, week_end, include_related_operations=False, **filters):
	entry_filters = {"status": ["!=", "Cancelled"]}

	fields = [
		"name",
		"creation",
		"final_eb_bom_generator",
		"operation_row",
		"operation",
		"workstation",
		"planning_category",
		"planned_date",
		"planned_start_datetime",
		"planned_end_datetime",
		"sequence",
		"production_quantity",
		"planned_meter",
		"planned_kg",
		"status",
		"priority",
		"remarks",
		"plan_type",
		"material_type",
		"material_item",
		"bom_no",
		"work_order",
		"planned_batches",
		"weight_per_batch",
		"quantity_uom",
		"source_warehouse",
		"wip_warehouse",
		"stock_movement_type",
		"target_warehouse",
		"planned_employee",
		"planned_shift",
		"material_transfer_stock_entry",
		"manufacture_stock_entry",
	]
	entry_meta = frappe.get_meta("SmartEdge Production Plan Entry", cached=True)
	for fieldname in (
		"released_qty_meter",
		"released_qty_kg",
		"released_from_plan_entry",
		"released_from_production_job",
	):
		if entry_meta.has_field(fieldname):
			fields.append(fieldname)
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters=entry_filters,
		fields=fields,
		order_by="planned_date asc, sequence asc, modified desc",
		limit_page_length=1000,
	)
	if not rows:
		return []
	generators = get_generator_map([row.final_eb_bom_generator for row in rows])
	rows = filter_authorized_plan_entries(rows, generators)
	if not rows:
		return []
	assign_planning_execution_keys(rows)
	released_productions = get_released_productions(rows)
	visible_execution_keys = {
		get_planning_execution_key(row)
		for row in rows
		if not is_unreleased_material_entry(row, released_productions)
		if row_matches_board_filters(row, filters)
		if (not row.planned_date and row.status != "Completed")
		or (row.planned_date and week_start <= getdate(row.planned_date) <= week_end)
	}

	result = []
	for row in rows:
		if is_unreleased_material_entry(row, released_productions):
			continue
		generator = generators.get(row.final_eb_bom_generator)
		if not generator:
			continue
		if include_related_operations:
			if get_planning_execution_key(row) not in visible_execution_keys:
				continue
		elif row.planned_date and not (week_start <= getdate(row.planned_date) <= week_end):
			continue
		elif not row_matches_board_filters(row, filters):
			continue
		card_item = generator.finished_item
		if filters.get("finished_product") and card_item != filters["finished_product"]:
			continue
		if filters.get("size") and str(generator.custom_size or "") != str(filters["size"]):
			continue
		if filters.get("customer") and str(generator.custom_customer_order_code or "") != str(filters["customer"]):
			continue
		if filters.get("search") and not matches_search(row, generator, filters["search"]):
			continue
		for key, value in generator.items():
			if key not in {"name", "status", "production_quantity"}:
				row[key] = value
		result.append(row)
	attach_job_details(result)
	from smart_edge_custom.manufacturing.production_lot import attach_lot_details

	attach_lot_details(result)
	from smart_edge_custom.manufacturing.quality_control import attach_qc_details

	attach_qc_details(result)
	attach_work_order_progress(result)
	planned_employee_names = get_employee_name_map(row.get("planned_employee") for row in result)
	for row in result:
		row.planned_employee_name = planned_employee_names.get(row.get("planned_employee"))
	attach_board_capacity_details(result)
	return result


def filter_authorized_plan_entries(rows, generators):
	"""Filter before enrichment so unauthorized operations never enter the board payload."""
	from smart_edge_custom.manufacturing.operator_access import (
		get_company_restrictions,
		get_user_employees,
		is_planning_supervisor,
	)

	user = frappe.session.user
	supervisor = is_planning_supervisor(user)
	employees = get_user_employees(user)
	company_restrictions = get_company_restrictions(user)
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": ["in", [row.name for row in rows]], "status": ["!=", "Cancelled"]},
		fields=["plan_entry", "operator_employee", "modified"],
		order_by="modified desc",
		limit_page_length=5000,
	)
	job_operator = {}
	for job in jobs:
		job_operator.setdefault(job.plan_entry, job.operator_employee)
	visible = []
	for row in rows:
		generator = generators.get(row.final_eb_bom_generator)
		if not generator or (company_restrictions and generator.company not in company_restrictions):
			continue
		if supervisor:
			visible.append(row)
			continue
		assigned_employee = job_operator.get(row.name) or row.get("planned_employee")
		if assigned_employee and assigned_employee in employees:
			visible.append(row)
	return visible


def row_matches_board_filters(row, filters):
	return not any(
		(
			filters.get("category") and row.get("planning_category") != filters["category"],
			filters.get("workstation") and row.get("workstation") != filters["workstation"],
			filters.get("operation") and row.get("operation") != filters["operation"],
			filters.get("status") and row.get("status") != filters["status"],
			filters.get("priority") and row.get("priority") != filters["priority"],
		)
	)


def get_planning_execution_key(row):
	if row.get("plan_type") == MATERIAL_PLAN_TYPE and str(row.get("execution_key") or "").startswith(
		"production:"
	):
		return ("Final EB", row.get("execution_key"))
	if row.get("plan_type") == MATERIAL_PLAN_TYPE:
		return ("Required Material", row.get("work_order") or row.name)
	return ("Final EB", row.get("execution_key") or row.get("final_eb_bom_generator"))


def assign_planning_execution_keys(rows):
	by_production = defaultdict(list)
	material_rows = defaultdict(list)
	for row in rows:
		if row.get("plan_type") == MATERIAL_PLAN_TYPE:
			row.execution_key = f"material:{row.get('work_order') or row.name}"
			material_rows[row.final_eb_bom_generator].append(row)
		else:
			by_production[row.final_eb_bom_generator].append(row)
	for production, production_rows in by_production.items():
		current_key = None
		last_sequence = None
		for row in sorted(
			production_rows,
			key=lambda item: (get_datetime(item.creation), cint(item.sequence), item.name),
		):
			sequence = cint(row.sequence)
			if current_key is None or (last_sequence is not None and sequence <= last_sequence):
				current_key = f"production:{production}:{row.name}"
			row.execution_key = current_key
			last_sequence = sequence

		current_rows = [row for row in production_rows if row.execution_key == current_key]
		materials = sorted(
			material_rows.get(production) or [],
			key=lambda row: (
				MATERIAL_DISPLAY_ORDER.get(row.get("material_type"), 99),
				str(row.get("creation") or ""),
				row.name,
			),
		)
		for display_sequence, row in enumerate(materials, 1):
			row.execution_key = current_key
			row.board_sequence = display_sequence
		material_count = len(materials)
		for row in current_rows:
			row.board_sequence = material_count + cint(row.sequence)

		# Additional roll-based Packing plans are separate executions, but they
		# belong to the same production card as the route that released them.  Use
		# the linked upstream plan's execution key instead of treating a later
		# repeated sequence as a new production run.
		rows_by_name = {row.name: row for row in production_rows}
		for row in production_rows:
			if str(row.get("planning_category") or "").lower() != "packing":
				continue
			parent = rows_by_name.get(row.get("released_from_plan_entry"))
			visited = set()
			while parent and not parent.get("execution_key") and parent.name not in visited:
				visited.add(parent.name)
				parent = rows_by_name.get(parent.get("released_from_plan_entry"))
			if parent and parent.get("execution_key"):
				row.execution_key = parent.execution_key

		# A follow-up Packing plan can repeat the final route sequence.  The
		# sequence scan above temporarily creates a new execution key for that
		# row, which must not strand the material operations on a second card.
		# When the Packing row resolves back to an earlier route execution, attach
		# the material rows to that same canonical parent key as well.
		packing_parent_keys = {
			row.execution_key
			for row in production_rows
			if str(row.get("planning_category") or "").lower() == "packing"
			and row.get("execution_key")
		}
		if packing_parent_keys and current_key not in packing_parent_keys:
			canonical_key = sorted(packing_parent_keys)[0]
			for row in materials:
				row.execution_key = canonical_key


def get_released_productions(rows):
	return {
		row.final_eb_bom_generator
		for row in rows
		if row.get("plan_type") != MATERIAL_PLAN_TYPE and row.final_eb_bom_generator
	}


def is_unreleased_material_entry(row, released_productions):
	return bool(
		row.get("plan_type") == MATERIAL_PLAN_TYPE
		and row.final_eb_bom_generator
		and row.final_eb_bom_generator not in released_productions
	)


def attach_work_order_progress(rows):
	work_orders = list({row.get("work_order") for row in rows if row.get("work_order")})
	if not work_orders:
		return
	work_order_rows = frappe.get_all(
		"Work Order",
		filters={"name": ["in", work_orders]},
		fields=["name", "status", "docstatus", "qty", "produced_qty"],
		limit_page_length=len(work_orders),
	)
	manufactured = defaultdict(float)
	for stock_entry in frappe.get_all(
		"Stock Entry",
		filters={
			"work_order": ["in", work_orders],
			"purpose": "Manufacture",
			"docstatus": 1,
		},
		fields=["work_order", "fg_completed_qty"],
		limit_page_length=5000,
	):
		manufactured[stock_entry.work_order] += flt(stock_entry.fg_completed_qty)
	by_name = {row.name: row for row in work_order_rows}
	for row in rows:
		work_order = by_name.get(row.get("work_order"))
		if not work_order:
			if row.get("work_order") and row.get("plan_type") == MATERIAL_PLAN_TYPE:
				from smart_edge_custom.manufacturing.work_order_sync import missing_work_order_message
				row.work_order_missing = True
				row.work_order_warning = missing_work_order_message(row.work_order, row.name)
			continue
		verified_qty = min(flt(work_order.produced_qty), manufactured[work_order.name])
		row.work_order_status = work_order.status
		row.work_order_qty = flt(work_order.qty)
		row.work_order_produced_qty = verified_qty
		row.work_order_remaining_qty = max(flt(work_order.qty) - verified_qty, 0)
		row.work_order_is_partial = bool(
			verified_qty > 0 and verified_qty + 0.000001 < flt(work_order.qty)
		)
		weight = flt(row.get("weight_per_batch"))
		if weight:
			row.work_order_completed_batches = int((verified_qty + 0.000001) // weight)
			row.work_order_remaining_batches = max(cint(row.get("planned_batches")) - row.work_order_completed_batches, 0)


def attach_job_details(rows):
	if not rows:
		return
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovery_uom, get_unusable_scrap_uom

	job_rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": ["in", [row.name for row in rows]], "status": ["!=", "Cancelled"]},
		fields=get_job_detail_fields(),
		order_by="modified desc",
		limit_page_length=1000,
	)
	by_entry = defaultdict(list)
	for job in job_rows:
		by_entry[job.plan_entry].append(job)
	employee_names = get_employee_name_map(job.operator_employee for job in job_rows)
	recovery_uoms = {}
	unusable_scrap_uoms = {}
	for row in rows:
		job = get_authoritative_job(by_entry.get(row.name, []))
		if not job:
			continue
		row.production_job = job.name
		row.job_status = job.status
		row.job_workstation = job.workstation
		row.operator_employee = job.operator_employee
		row.operator_name = employee_names.get(job.operator_employee) or job.operator_name
		row.shift = job.shift
		row.job_actual_start = job.actual_start
		row.job_actual_end = job.actual_end
		row.job_productive_elapsed_seconds = get_productive_elapsed_seconds(job)
		row.job_completion_type = job.get("completion_type")
		row.job_actual_qty_meter = job.get("actual_qty_meter")
		row.job_actual_qty_kg = job.get("actual_qty_kg")
		row.job_good_qty = job.get("good_qty")
		row.job_rejected_qty = job.get("rejected_qty")
		row.job_rework_qty = job.get("rework_qty")
		row.job_usable_scrap = job.get("usable_scrap")
		row.job_unusable_scrap = job.get("unusable_scrap")
		source_item = row.get("finished_item") or row.get("material_item")
		if source_item not in recovery_uoms:
			recovery_uoms[source_item] = get_recovery_uom(source_item)
			unusable_scrap_uoms[source_item] = get_unusable_scrap_uom(source_item)
		row.scrap_uom = recovery_uoms[source_item]
		row.unusable_scrap_uom = unusable_scrap_uoms[source_item]
		row.job_remaining_qty_meter = job.get("remaining_qty_meter")
		row.job_remaining_qty_kg = job.get("remaining_qty_kg")
	attach_packing_operation_progress(rows)
	attach_open_downtime_details(rows)


def attach_packing_operation_progress(rows):
	"""Attach a display-only aggregate status for roll-based Packing entries."""
	packing_rows = [row for row in rows if is_packing_plan_entry(row)]
	if not packing_rows:
		return
	from smart_edge_custom.manufacturing.roll_tracking import _packing_roll_source_entries

	for row in packing_rows:
		source_entries = _packing_roll_source_entries(row)
		if not source_entries:
			continue
		rolls = frappe.get_all(
			"SmartEdge Roll",
			filters={"source_plan_entry": ["in", source_entries]},
			fields=["name", "current_meter_quantity", "qc_status", "process_status", "packing_status"],
		)
		if not rolls:
			continue
		packed = [
			roll for roll in rolls
			if roll.process_status == "Packed" or roll.packing_status == "Packed"
		]
		available = [
			roll for roll in rolls
			if roll.qc_status == "Passed"
			and roll.process_status == "Available for Packing"
			and roll.packing_status == "Available"
		]
		final = [
			roll for roll in rolls
			if roll in packed or roll.qc_status == "Rejected" or roll.process_status == "Rejected"
		]
		row.packing_packed_qty_meter = sum(flt(roll.current_meter_quantity) for roll in packed)
		row.packing_available_qty_meter = sum(flt(roll.current_meter_quantity) for roll in available)
		row.packing_roll_count = len(rolls)
		row.packing_packed_roll_count = len(packed)
		row.packing_remaining_roll_count = len(rolls) - len(final)
		# Only the completed parent/child execution is an operation summary.  A
		# newly created follow-up plan must retain its own Ready/Planned/In Progress
		# state until that execution is actually run.
		if row.status != "Completed" and row.get("job_status") != "Completed":
			continue
		if packed and len(final) < len(rolls):
			row.packing_operation_status = "Partially Completed"
		elif len(final) == len(rolls):
			row.packing_operation_status = "Completed"
		elif row.status == "Completed":
			# A completed individual job cannot make an unresolved roll flow
			# appear complete when no roll was actually finalized by Packing.
			row.packing_operation_status = "Ready"


@frappe.whitelist()
def create_remaining_packing_plan(plan_entry):
	"""Create a separate Packing plan for rolls released after a prior job.

	The completed plan and its Production Job are intentionally left untouched.
	Roll eligibility remains authoritative, so already packed, reserved, rejected,
	and rework-pending rolls cannot be planned again.
	"""
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	from smart_edge_custom.manufacturing.roll_tracking import get_eligible_rolls

	require_planning_supervisor(_("plan remaining Packing rolls"))
	parent = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	parent.check_permission("read")
	if str(parent.get("planning_category") or "").lower() != "packing":
		frappe.throw(_("Only Packing operations can create a remaining Packing plan."))
	if parent.status in {"Cancelled", "Pending", "Ready", "Planned", "In Progress", "On Hold"}:
		frappe.throw(_("Use the existing Packing plan until its assigned rolls are completed."))

	active = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": parent.final_eb_bom_generator,
			"operation_row": parent.operation_row,
			"planning_category": "Packing",
			"status": ["in", ["Pending", "Ready", "Planned", "In Progress", "On Hold"]],
		},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if active:
		return {"plan_entry": active[0].name, "created": False}

	rolls = get_eligible_rolls(parent.name)
	if not rolls:
		frappe.throw(_("No newly QC-approved rolls are available for Packing."))
	available_meter = sum(flt(roll.current_meter_quantity) for roll in rolls)
	if available_meter <= 0:
		frappe.throw(_("No positive quantity is available on the newly QC-approved rolls."))

	new_entry = frappe.new_doc("SmartEdge Production Plan Entry")
	copy_fields = (
		"final_eb_bom_generator", "plan_type", "material_type", "material_item", "bom_no",
		"work_order", "operation", "planning_category", "sequence", "quantity_uom",
		"weight_per_batch", "source_warehouse", "wip_warehouse", "stock_movement_type",
		"target_warehouse", "created_from", "workstation", "planned_employee", "planned_shift",
		"priority", "operation_row", "required_by",
	)
	for fieldname in copy_fields:
		if parent.get(fieldname) is not None:
			new_entry.set(fieldname, parent.get(fieldname))
	new_entry.production_quantity = parent.production_quantity
	new_entry.planned_meter = available_meter
	new_entry.released_qty_meter = available_meter
	new_entry.planned_kg = (
		flt(parent.planned_kg) * available_meter / flt(parent.planned_meter)
		if flt(parent.planned_meter) else flt(parent.planned_kg)
	)
	new_entry.released_qty_kg = (
		flt(parent.released_qty_kg) * available_meter / flt(parent.released_qty_meter)
		if flt(parent.released_qty_meter) else 0
	)
	new_entry.released_from_plan_entry = parent.released_from_plan_entry
	new_entry.released_from_production_job = parent.released_from_production_job
	new_entry.status = "Ready"
	new_entry.insert()
	update_generator_planning_status(new_entry.final_eb_bom_generator)
	return {"plan_entry": new_entry.name, "created": True, "available_meter": available_meter}


def get_job_detail_fields():
	fields = [
		"name",
		"plan_entry",
		"status",
		"workstation",
		"operator_employee",
		"operator_name",
		"shift",
		"actual_start",
		"actual_end",
	]
	meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	for fieldname in (
		"completion_type",
		"actual_qty_meter",
		"actual_qty_kg",
		"good_qty",
		"rejected_qty",
		"rework_qty",
		"usable_scrap",
		"unusable_scrap",
		"remaining_qty_meter",
		"remaining_qty_kg",
	):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	if meta.has_field("productive_elapsed_seconds"):
		fields.append("productive_elapsed_seconds")
	if meta.has_field("current_timer_start"):
		fields.append("current_timer_start")
	return fields


def get_productive_elapsed_seconds(job):
	seconds = flt(job.get("productive_elapsed_seconds"))
	if job.status == "In Progress":
		timer_start = job.get("current_timer_start") or (job.get("actual_start") if not seconds else None)
		if timer_start:
			seconds += max((now_datetime() - get_datetime(timer_start)).total_seconds(), 0)
	return int(seconds)


def get_effective_entry_status(row):
	if row.get("packing_operation_status"):
		return row.get("packing_operation_status")
	if row.get("job_status") == "Draft":
		return row.status
	if (
		row.get("plan_type") == "Required Material"
		and row.get("job_status") == "Completed"
		and row.status != "Completed"
	):
		return row.status
	if row.get("job_status") == "Completed" and row.get("job_completion_type") == "Partial Completion":
		return row.status
	return row.get("job_status") or row.status


def get_employee_name_map(employees):
	employee_names = [employee for employee in set(employees) if employee]
	if not employee_names:
		return {}
	return {
		row.name: row.employee_name
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", employee_names]},
			fields=["name", "employee_name"],
			limit_page_length=len(employee_names),
		)
	}


def get_employee_display_name(employee, fallback=None):
	if not employee:
		return fallback
	return frappe.db.get_value("Employee", employee, "employee_name") or fallback


def attach_open_downtime_details(rows):
	job_names = [row.get("production_job") for row in rows if row.get("production_job")]
	if not job_names or not frappe.db.exists("DocType", "SmartEdge Production Downtime"):
		return
	downtime_rows = frappe.get_all(
		"SmartEdge Production Downtime",
		filters={"parent": ["in", job_names], "parenttype": "SmartEdge Production Job", "end_time": ["is", "not set"]},
		fields=["parent", "start_time", "downtime_reason", "remarks", "employee"],
		order_by="idx desc",
		limit_page_length=1000,
	)
	by_job = {}
	for downtime in downtime_rows:
		by_job.setdefault(downtime.parent, downtime)
	for row in rows:
		downtime = by_job.get(row.get("production_job"))
		if not downtime:
			continue
		row.downtime_start = downtime.start_time
		row.downtime_reason = downtime.downtime_reason
		row.downtime_remarks = downtime.remarks
		row.downtime_employee = downtime.employee
		row.downtime_elapsed_seconds = max((now_datetime() - get_datetime(downtime.start_time)).total_seconds(), 0)


def get_authoritative_job(jobs):
	active_jobs = [job for job in jobs if job.status not in {"Completed", "Cancelled"}]
	if len(active_jobs) == 1:
		return active_jobs[0]
	if len(active_jobs) > 1:
		return None
	return jobs[0] if jobs else None


def get_generator_map(names):
	names = list({name for name in names if name})
	if not names:
		return {}
	fields = [
		"name",
		"company",
		"finished_item",
		"finished_item_name",
		"custom_size",
		"custom_customer_order_code",
		"production_quantity",
		"stock_uom",
		"total_eb_weight_kg",
		"compound_item",
		"compound_total_required",
		"base_colour_item",
		"shade_total_required",
		"printing_type",
		"printing_route",
		"extrusion_type",
		"slitting_requirement",
		"selected_route",
		"status",
		"sales_order",
		"sales_order_item",
		"customer",
		"required_delivery_date",
	]
	rows = frappe.get_all("Final EB BOM Generator", filters={"name": ["in", names]}, fields=fields)
	item_names = dict(frappe.get_all("Item", filters={"name": ["in", list({row.finished_item for row in rows})]}, fields=["name", "item_name"], as_list=True)) if rows else {}
	for row in rows:
		row.finished_item_name = item_names.get(row.finished_item) or row.finished_item
	return {row.name: row for row in rows}


def matches_search(row, generator, search):
	text = " ".join(
		str(value or "")
		for value in (
			row.name,
			row.final_eb_bom_generator,
			row.operation,
			row.workstation,
			row.get("material_type"),
			row.get("material_item"),
			generator.finished_item,
			generator.finished_item_name,
			generator.custom_customer_order_code,
			generator.get("sales_order"),
			generator.get("customer"),
		)
	).lower()
	return str(search).lower() in text


def make_empty_board(week_start):
	days = {}
	for offset in range(7):
		date_value = week_start + timedelta(days=offset)
		days[str(date_value)] = {
			"date": str(date_value),
			"label": date_value.strftime("%A"),
			"short_label": date_value.strftime("%a").upper(),
			"display_date": f"{date_value.strftime('%b')} {date_value.day}",
			"operations": [],
			"groups": [],
			"production_count": 0,
			"operation_count": 0,
		}
	return {"pending": [], "days": days}


def make_operation_card(row):
	category = row.planning_category or "Other"
	actual_workstation = row.get("job_workstation")
	display_workstation = get_card_workstation(row)
	planned_quantity = (
		flt(row.get("planned_kg") or row.production_quantity)
		if row.get("plan_type") == MATERIAL_PLAN_TYPE
		else flt(row.production_quantity)
	)
	card = {
		"id": row.name,
		"plan_type": row.get("plan_type") or "Final EB",
		"planned_quantity": planned_quantity,
		"execution_key": row.get("execution_key"),
		"production": row.final_eb_bom_generator,
		"company": row.get("company"),
		"operation_row": row.operation_row,
		"operation": row.operation,
		"operation_sequence": row.get("board_sequence") or row.sequence,
		"workstation": display_workstation,
		"planned_workstation": row.workstation,
		"actual_workstation": actual_workstation,
		"planned_date": str(row.planned_date) if row.planned_date else None,
		"planned_start": str(row.planned_start_datetime) if row.planned_start_datetime else None,
		"planned_end": str(row.planned_end_datetime) if row.planned_end_datetime else None,
		"duration_hours": get_duration_hours(row),
		"status": row.status,
		"category": category,
		"category_color": CATEGORY_COLORS.get(category, "gray"),
		"finished_item": row.finished_item,
		"item_name": row.finished_item_name,
		"qty": row.planned_meter or row.production_quantity,
		"uom": row.stock_uom,
		"released_qty_meter": flt(row.get("released_qty_meter")),
		"released_qty_kg": flt(row.get("released_qty_kg")),
		"released_from_plan_entry": row.get("released_from_plan_entry"),
		"released_from_production_job": row.get("released_from_production_job"),
		"size": row.custom_size,
		"planned_weight_kg": row.planned_kg or row.total_eb_weight_kg,
		"printing_type": row.printing_type,
		"printing_route": row.printing_route,
		"extrusion_type": row.extrusion_type,
		"slitting_requirement": row.slitting_requirement,
		"compound": row.compound_item,
		"base_colour": row.base_colour_item,
		"priority": row.priority or "Normal",
		"customer_code": row.custom_customer_order_code,
		"sales_order": row.get("sales_order"),
		"sales_order_item": row.get("sales_order_item"),
		"customer": row.get("customer"),
		"required_delivery_date": str(row.get("required_delivery_date")) if row.get("required_delivery_date") else None,
		"production_job": row.get("production_job"),
		"job_status": row.get("job_status"),
		"packing_operation_status": row.get("packing_operation_status"),
		"packing_packed_qty_meter": flt(row.get("packing_packed_qty_meter")),
		"packing_available_qty_meter": flt(row.get("packing_available_qty_meter")),
		"packing_roll_count": cint(row.get("packing_roll_count")),
		"packing_packed_roll_count": cint(row.get("packing_packed_roll_count")),
		"packing_remaining_roll_count": cint(row.get("packing_remaining_roll_count")),
		"operator_employee": row.get("operator_employee") or row.get("planned_employee"),
		"operator_name": row.get("operator_name") or row.get("planned_employee_name"),
		"shift": row.get("shift") or row.get("planned_shift"),
		"job_actual_start": str(row.get("job_actual_start")) if row.get("job_actual_start") else None,
		"job_actual_end": str(row.get("job_actual_end")) if row.get("job_actual_end") else None,
		"job_productive_elapsed_seconds": cint(row.get("job_productive_elapsed_seconds") or 0),
		"job_completion_type": row.get("job_completion_type"),
		"job_actual_qty_meter": flt(row.get("job_actual_qty_meter")),
		"job_actual_qty_kg": flt(row.get("job_actual_qty_kg")),
		"job_good_qty": flt(row.get("job_good_qty")),
		"job_rejected_qty": flt(row.get("job_rejected_qty")),
		"job_rework_qty": flt(row.get("job_rework_qty")),
		"job_usable_scrap": flt(row.get("job_usable_scrap")),
		"job_unusable_scrap": flt(row.get("job_unusable_scrap")),
		"scrap_uom": row.get("scrap_uom"),
		"unusable_scrap_uom": row.get("unusable_scrap_uom"),
		"job_remaining_qty_meter": flt(row.get("job_remaining_qty_meter")),
		"job_remaining_qty_kg": flt(row.get("job_remaining_qty_kg")),
		"production_lot": row.get("production_lot"),
		"operation_lot_missing": bool(row.get("operation_lot_missing")),
		"work_order_missing": bool(row.get("work_order_missing")),
		"work_order_warning": row.get("work_order_warning"),
		"operation_trace_id": row.get("operation_trace_id"),
		"previous_operation_lot": row.get("previous_operation_lot"),
		"related_finished_goods_batches": row.get("related_finished_goods_batches") or [],
		"actual_operation_sequence": row.sequence,
		"production_lot_status": row.get("production_lot_status"),
		"lot_produced_quantity": flt(row.get("lot_produced_quantity")),
		"lot_planned_quantity": flt(row.get("lot_planned_quantity")),
		"lot_actual_output_quantity": flt(row.get("lot_actual_output_quantity")),
		"lot_available_quantity": flt(row.get("lot_available_quantity")),
		"lot_transferred_quantity": flt(row.get("lot_transferred_quantity")),
		"lot_remaining_quantity": flt(row.get("lot_remaining_quantity")),
		"lot_uom": row.get("lot_uom"),
		"stock_movement_type": row.get("stock_movement_type") or "Non-Stock Operation",
		"physical_stock_entry": row.get("physical_stock_entry"),
		"physical_batch": row.get("physical_batch"),
		"physical_wip_warehouse": row.get("physical_wip_warehouse"),
		"physical_posted_quantity": flt(row.get("physical_posted_quantity")),
		"physical_transferred_quantity": flt(row.get("physical_transferred_quantity")),
		"physical_available_quantity": flt(row.get("physical_available_quantity")),
		"material_stock_entry": row.get("material_stock_entry"),
		"material_stock_entry_purpose": row.get("material_stock_entry_purpose"),
		"material_stock_entry_status": row.get("material_stock_entry_status"),
		"material_stock_consumption": row.get("material_stock_consumption") or [],
		"is_qc": bool(row.get("is_qc")),
		"qc_required": bool(row.get("qc_required")),
		"qc_checkpoint_operation": row.get("qc_checkpoint_operation"),
		"quality_inspection": row.get("quality_inspection"),
		"qc_status": row.get("qc_status"),
		"qc_display_status": row.get("qc_display_status"),
		"qc_notification_warning": row.get("qc_notification_warning"),
		"qc_eligible_qty": flt(row.get("eligible_qty")),
		"qc_accepted_qty": flt(row.get("accepted_qty")),
		"qc_rejected_qty": flt(row.get("rejected_qty")),
		"qc_rework_qty": flt(row.get("rework_qty")),
		"qc_can_review": bool(row.get("can_review")),
		"qc_blocked": bool(row.get("qc_blocked")),
		"downtime_start": str(row.get("downtime_start")) if row.get("downtime_start") else None,
		"downtime_reason": row.get("downtime_reason"),
		"downtime_remarks": row.get("downtime_remarks"),
		"downtime_employee": row.get("downtime_employee"),
		"downtime_elapsed_seconds": cint(row.get("downtime_elapsed_seconds") or 0),
	}
	if row.get("plan_type") != MATERIAL_PLAN_TYPE:
		card["machine_capacity"] = row.get("machine_capacity") or {"applicable": 0}
	start_eligibility = get_scheduled_start_eligibility(row)
	card["start_allowed"] = start_eligibility["allowed"]
	card["start_block_reason"] = start_eligibility["message"]
	if row.get("qc_blocked"):
		card["start_allowed"] = False
		card["start_block_reason"] = row.get("qc_block_reason")
	if row.get("plan_type") == "Required Material":
		card.update(
			{
				"plan_type": row.plan_type,
				"material_type": row.material_type,
				"finished_item": row.material_item,
				"item_name": frappe.db.get_value("Item", row.material_item, "item_name") or row.material_item,
				"qty": cint(row.planned_batches),
				"uom": row.quantity_uom,
				"planned_weight_kg": flt(row.get("planned_kg") or row.production_quantity),
				"bom_no": row.bom_no,
				"work_order": row.work_order,
				"planned_batches": cint(row.planned_batches),
				"weight_per_batch": flt(row.weight_per_batch),
				"source_warehouse": row.source_warehouse,
				"wip_warehouse": row.wip_warehouse,
				"target_warehouse": row.target_warehouse,
				"work_order_status": row.get("work_order_status"),
				"work_order_qty": flt(row.get("work_order_qty")),
				"work_order_produced_qty": flt(row.get("work_order_produced_qty")),
				"work_order_remaining_qty": flt(row.get("work_order_remaining_qty")),
				"work_order_is_partial": bool(row.get("work_order_is_partial")),
				"work_order_completed_batches": cint(row.get("work_order_completed_batches")),
				"work_order_remaining_batches": cint(row.get("work_order_remaining_batches")),
			}
		)
	return card


def get_partial_planning_summary(productions, visible_entries=None):
	"""Return route-step planning coverage for production cards without changing state."""
	productions = {production for production in productions if production}
	if not productions:
		return {}

	route_rows = frappe.get_all(
		"Final EB BOM Operation",
		filters={
			"parent": ["in", list(productions)],
			"parentfield": "operation_preview",
			"mandatory": 1,
		},
		fields=["parent", "name", "operation", "idx"],
		order_by="parent asc, idx asc",
		limit_page_length=5000,
	)
	route_by_production = defaultdict(list)
	for row in route_rows:
		row.is_material = False
		route_by_production[row.parent].append(row)

	planned_rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": ["in", list(productions)],
			"status": ["!=", "Cancelled"],
		},
		fields=[
			"name",
			"final_eb_bom_generator",
			"operation_row",
			"operation",
			"plan_type",
			"material_type",
			"status",
			"planned_date",
			"planned_start_datetime",
			"planned_end_datetime",
		],
		limit_page_length=5000,
	)
	if visible_entries is not None:
		visible_entry_names = {row.get("name") for row in visible_entries}
		planned_rows = [row for row in planned_rows if row.get("name") in visible_entry_names]

	# Final EB route rows are the canonical operation list. Required Material
	# rows are also rendered as operations on the production card, so include
	# each material plan exactly once when it is part of the visible card.
	for row in planned_rows:
		if row.get("plan_type") != MATERIAL_PLAN_TYPE:
			continue
		production = row.get("final_eb_bom_generator")
		if not production:
			continue
		operation_label = row.get("operation") or row.get("material_type") or _("Material")
		route_by_production[production].append(
			frappe._dict(name=row.get("name"), operation=operation_label, idx=0, is_material=True)
		)

	if visible_entries is not None:
		visible_route_rows = {
			(row.get("final_eb_bom_generator"), row.get("operation_row"))
			for row in visible_entries
			if row.get("plan_type") != MATERIAL_PLAN_TYPE and row.get("operation_row")
		}
		visible_entry_names = {row.get("name") for row in visible_entries}
		for production in list(route_by_production):
			route_by_production[production] = [
				row
				for row in route_by_production[production]
				if (row.is_material and row.name in visible_entry_names)
				or (not row.is_material and (production, row.name) in visible_route_rows)
			]
			if not route_by_production[production]:
				del route_by_production[production]
	if not route_by_production:
		return {}

	job_rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={
			"plan_entry": ["in", [row.get("name") for row in planned_rows if row.get("name")]],
			"status": ["in", ["In Progress", "Paused", "Completed", "On Hold"]],
		},
		fields=["plan_entry", "status", "actual_start"],
		limit_page_length=5000,
	) if planned_rows else []
	executed_entries = {
		row.plan_entry
		for row in job_rows
		if row.status in {"In Progress", "Paused", "Completed", "On Hold"} or row.actual_start
	}
	planned_by_production = defaultdict(set)
	completed_by_production = defaultdict(set)
	for row in planned_rows:
		operation_key = row.get("operation_row") or (
			row.get("name") if row.get("plan_type") == MATERIAL_PLAN_TYPE else None
		)
		if not operation_key:
			continue
		if row.status in ACCOUNTED_OPERATION_STATUSES or (
			row.status == READY_OPERATION_STATUS
			and any(row.get(fieldname) for fieldname in ("planned_date", "planned_start_datetime", "planned_end_datetime"))
		) or any(row.get(fieldname) for fieldname in ("planned_date", "planned_start_datetime", "planned_end_datetime")) or row.get("name") in executed_entries:
			planned_by_production[row.final_eb_bom_generator].add(operation_key)
		if row.status == "Completed" or (
			row.get("name") in executed_entries
			and any(job.plan_entry == row.get("name") and job.status == "Completed" for job in job_rows)
		):
			completed_by_production[row.final_eb_bom_generator].add(operation_key)

	result = {}
	for production, required_rows in route_by_production.items():
		planned = {row.name for row in required_rows if row.name in planned_by_production.get(production, set())}
		if not planned or len(planned) == len(required_rows):
			continue
		unplanned = [row.operation for row in required_rows if row.name not in planned]
		result[production] = {
			"planned_count": len(planned),
			"completed_count": len(completed_by_production.get(production, set()) & planned),
			"total_count": len(required_rows),
			"unplanned_count": len(unplanned),
			"unplanned_operations": unplanned,
		}
	return result


def get_card_workstation(row):
	status = get_effective_entry_status(row)
	if status in {"In Progress", "Paused", "Completed", "On Hold"} and row.get("job_workstation"):
		return row.get("job_workstation")
	return row.workstation


def get_duration_hours(row):
	if row.planned_start_datetime and row.planned_end_datetime:
		return (get_datetime(row.planned_end_datetime) - get_datetime(row.planned_start_datetime)).total_seconds() / 3600
	return 1


def group_operations(operations):
	grouped = defaultdict(lambda: defaultdict(list))
	for card in operations:
		grouped[card["category"]][card.get("workstation") or _("Unassigned")].append(card)

	groups = []
	for category in sorted(grouped):
		workstations = []
		for workstation in sorted(grouped[category]):
			cards = grouped[category][workstation]
			daily_summary = next(
				(
					card.get("machine_capacity", {}).get("daily_summary")
					for card in cards
					if card.get("machine_capacity", {}).get("daily_summary")
				),
				None,
			)
			capacity = get_workstation_capacity_payload(workstation, cards[0]["planned_date"]) if cards else {}
			if daily_summary:
				capacity.update(
					{
						"capacity_hours": daily_summary.get("net_available_hours", 0),
						"planned_hours": daily_summary.get("allocated_hours", 0),
						"remaining_hours": daily_summary.get("remaining_hours", 0),
						"overallocated_hours": daily_summary.get("overallocated_hours", 0),
						"utilization": daily_summary.get("utilization"),
						"configured": daily_summary.get("configured", 0),
						"message": daily_summary.get("message"),
					}
				)
			workstations.append(
				{
					"workstation": workstation,
					"cards": cards,
					"planned_hours": sum(flt(card["duration_hours"]) for card in cards),
					"capacity": capacity,
				}
			)
		groups.append(
			{
				"category": category,
				"color": CATEGORY_COLORS.get(category, "gray"),
				"count": sum(len(item["cards"]) for item in workstations),
				"workstations": workstations,
			}
		)
	return groups


@frappe.whitelist()
def get_production_plan(production):
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	require_planning_supervisor(_("view complete production route details"))
	frappe.has_permission("Final EB BOM Generator", "read", throw=True)
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("read")
	return {
		"production": doc.name,
		"finished_item": doc.finished_item,
		"item_name": doc.finished_item_name,
		"qty": doc.production_quantity,
		"uom": doc.stock_uom,
		"weight": doc.total_eb_weight_kg,
		"compound": doc.compound_item,
		"compound_required": doc.compound_total_required,
		"base_colour": doc.base_colour_item,
		"shade_required": doc.shade_total_required,
		"route": doc.selected_route,
		"status": doc.status,
		"operations": [
			{
				"id": row.name,
				"operation": row.operation,
				"sequence": row.sequence,
				"workstation": row.workstation,
				"planned_date": None,
				"planned_start": None,
				"planned_end": None,
				"category": row.planning_category,
			}
			for row in doc.operation_preview
		],
	}


@frappe.whitelist()
def get_compatible_workstations(
	operation, operation_row=None, include_default=1, include_routes=1, include_all_active=1
):
	"""Return the active Workstations available to the Planning Board.

	The Planning Board intentionally uses the temporary SmartEdge business rule
	that every active Workstation is selectable for every operation.  Keep the
	operation arguments for API compatibility with existing callers, but do not
	apply route, operation-default, BOM, or Workstation Type restrictions here.
	"""
	frappe.has_permission("Workstation", "read", throw=True)
	if not operation or not frappe.db.exists("Operation", operation):
		return []
	return get_active_workstations()


def filter_enabled_workstations(workstations):
	if not workstations:
		return []
	enabled = set(
		frappe.get_list(
			"Workstation",
			filters={"name": ["in", workstations], "disabled": 0},
			pluck="name",
			limit_page_length=len(workstations),
		)
	)
	return [workstation for workstation in workstations if workstation in enabled]


def get_active_workstations():
	"""Return enabled Workstations visible to the current user in master order."""
	return frappe.get_list(
		"Workstation",
		filters={"disabled": 0},
		pluck="name",
		order_by="name asc",
		limit_page_length=500,
	)


def add_unique(values, value):
	if value and value not in values:
		values.append(value)


def get_working_hours_projection(entry, workstation, planned_date, planned_start, duration_hours):
	result = validate_workstation_time_window(
		workstation,
		planned_date,
		planned_start,
		duration_hours,
		shift=entry.get("planned_shift"),
	)
	return {
		"working_hours_status": result.get("status"),
		"working_hours_message": result.get("message"),
		"working_hours": result.get("working_intervals_display"),
		"working_intervals": result.get("working_intervals"),
		"planned_start": result.get("planned_start"),
		"planned_end": result.get("planned_end"),
	}


def get_workstation_booking_conflict_projection(entry, workstation, planned_date, planned_start, duration_hours):
	start_dt = combine_date_time(planned_date, planned_start)
	end_dt = start_dt + timedelta(hours=flt(duration_hours))
	conflicts = get_overlapping_entries(workstation, start_dt, end_dt, entry.get("name"))
	maximum_capacity = get_capacity(workstation)
	conflict_details = [
		{
			"name": conflict["name"],
			"operation": conflict.get("operation"),
			"status": conflict.get("status"),
			"start": get_datetime(conflict["start"]).strftime("%d-%m-%Y %H:%M:%S"),
			"end": get_datetime(conflict["end"]).strftime("%d-%m-%Y %H:%M:%S"),
			"source": conflict.get("source"),
		}
		for conflict in conflicts
	]
	return {
		"scheduling_conflict": cint(bool(conflicts)),
		"scheduling_conflict_blocked": cint(bool(conflicts)),
		"scheduling_conflict_message": (
			format_workstation_conflict_message(workstation, start_dt, end_dt, conflicts)
			if conflicts
			else None
		),
		"conflicting_operations": conflict_details,
		"conflicting_operation": (
			conflict_details[0]["name"] if conflict_details else None
		),
		"conflicting_time_interval": (
			f"{conflict_details[0]['start']} – {conflict_details[0]['end']}"
			if conflict_details
			else None
		),
		"workstation_capacity": maximum_capacity,
		"planned_start_datetime": start_dt.strftime("%d-%m-%Y %H:%M:%S"),
		"planned_end_datetime": end_dt.strftime("%d-%m-%Y %H:%M:%S"),
	}


@frappe.whitelist()
def check_schedule_capacity(
	plan_entry,
	planned_date,
	workstation=None,
	planned_start="09:00",
	duration_hours=None,
	override_capacity=0,
	roll_ids=None,
):
	"""Return a read-only projection and a scoped token when overload confirmation is allowed."""
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	require_planning_supervisor(_("plan production operations"))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	validate_plan_entry(entry)
	# The Plan Entry is authoritative when the board omits the optional
	# workstation argument.  An explicitly supplied workstation is still
	# validated below and cannot silently replace the route assignment.
	workstation = workstation or entry.get("workstation")
	if not workstation:
		frappe.throw(_("Select a Workstation for {0} before checking capacity.").format(entry.operation or entry.name))
	validate_workstation_reassignment_allowed(entry, workstation)
	validate_selected_workstation(entry, workstation)
	packing_roll_selection = get_packing_roll_selection(entry, roll_ids)
	if packing_roll_selection:
		entry.planned_meter = packing_roll_selection["meter_quantity"]
	planned_start = normalize_schedule_time(planned_start)
	duration_hours = flt(duration_hours) or get_entry_default_duration(entry)
	projection = get_schedule_capacity_projection(
		entry, workstation, planned_date, duration_hours=duration_hours
	)
	projection.update(
		get_working_hours_projection(
			entry, workstation, planned_date, planned_start, duration_hours
		)
	)
	projection.update(
		get_workstation_booking_conflict_projection(
			entry, workstation, planned_date, planned_start, duration_hours
		)
	)
	projection["planned_hours"] = duration_hours
	projection["can_confirm"] = cint(can_confirm_capacity_overload())
	projection["override_requested"] = cint(override_capacity)
	if projection.get("requires_confirmation") and projection["can_confirm"] and cint(override_capacity):
		fingerprint = make_capacity_move_fingerprint(
			entry.name, workstation, planned_date, planned_start, duration_hours
		)
		token = frappe.generate_hash(length=32)
		frappe.cache.set_value(
			f"{CAPACITY_CONFIRMATION_CACHE_PREFIX}{token}",
			{
				"user": frappe.session.user,
				"fingerprint": fingerprint,
				"projection_signature": get_capacity_projection_signature(projection),
			},
			expires_in_sec=CAPACITY_CONFIRMATION_TTL_SECONDS,
		)
		projection["confirmation_token"] = token
	return projection


def can_confirm_capacity_overload():
	roles = set(frappe.get_roles(frappe.session.user))
	return frappe.session.user == "Administrator" or bool(roles & {"Manufacturing Manager", "System Manager"})


def make_capacity_move_fingerprint(plan_entry, workstation, planned_date, planned_start, duration_hours):
	return {
		"plan_entry": plan_entry,
		"workstation": workstation,
		"planned_date": str(getdate(planned_date)),
		"planned_start": normalize_schedule_time(planned_start),
		"duration_hours": round(flt(duration_hours), 6),
	}


def get_capacity_projection_signature(projection):
	values = {
		fieldname: (round(flt(projection.get(fieldname)), 6) if projection.get(fieldname) is not None else None)
		for fieldname in (
			"available_hours",
			"already_planned_hours",
			"new_operation_hours",
			"projected_total_hours",
			"overload_hours",
		)
	}
	values.update(
		{
			"workstation": projection.get("workstation"),
			"planned_date": projection.get("planned_date"),
			"shift": projection.get("shift"),
			"status": projection.get("status"),
		}
	)
	return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def validate_capacity_confirmation(token, fingerprint, projection):
	if not token:
		frappe.throw(
			_("This move overloads the Workstation. Review the latest capacity and confirm it again."),
			title=_("Capacity Confirmation Required"),
		)
	if not can_confirm_capacity_overload():
		frappe.throw(
			_("Only an authorized Manufacturing Manager can confirm a capacity overload."),
			title=_("Capacity Confirmation Not Allowed"),
		)
	cache_key = f"{CAPACITY_CONFIRMATION_CACHE_PREFIX}{token}"
	confirmation = frappe.cache.get_value(cache_key)
	if (
		not confirmation
		or confirmation.get("user") != frappe.session.user
		or confirmation.get("fingerprint") != fingerprint
		or confirmation.get("projection_signature") != get_capacity_projection_signature(projection)
	):
		frappe.throw(
			_("Capacity has changed or the confirmation expired. Review and confirm the fresh calculation."),
			title=_("Fresh Capacity Confirmation Required"),
		)
	return cache_key


@frappe.whitelist()
def schedule_operation(
	plan_entry,
	planned_date,
	workstation,
	planned_start="09:00",
	duration_hours=None,
	priority=None,
	override_capacity=0,
	override_sequence=0,
	capacity_confirmation_token=None,
	planned_employee=None,
	roll_ids=None,
):
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	require_planning_supervisor(_("schedule production operations"))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	lock_plan_entry(plan_entry)
	entry.reload()
	workstation_changed = bool(entry.workstation and entry.workstation != workstation)
	operator_changed = bool(planned_employee and entry.get("planned_employee") != planned_employee)
	validate_plan_entry(entry)
	validate_workstation_reassignment_allowed(entry, workstation)
	validate_operator_reassignment_allowed(entry, planned_employee)
	validate_material_plan_released(entry)
	validate_material_scheduling_context(entry)
	validate_selected_workstation(entry, workstation)
	packing_roll_selection = get_packing_roll_selection(entry, roll_ids)
	if packing_roll_selection:
		entry.planned_meter = packing_roll_selection["meter_quantity"]
	for workstation_name in sorted({value for value in (entry.workstation, workstation) if value}):
		lock_workstation(workstation_name)

	planned_start = normalize_schedule_time(planned_start)
	start_dt = combine_date_time(planned_date, planned_start)
	duration_hours = flt(duration_hours) or get_entry_default_duration(entry)
	projection = get_schedule_capacity_projection(
		entry, workstation, planned_date, duration_hours=duration_hours
	)
	projection.update(
		get_working_hours_projection(
			entry, workstation, planned_date, planned_start, duration_hours
		)
	)
	projection.update(
		get_workstation_booking_conflict_projection(
			entry, workstation, planned_date, planned_start, duration_hours
		)
	)
	validation_messages = []
	capacity_status = projection.get("status")
	if capacity_status not in {"ok", "overload", "not_applicable"}:
		validation_messages.append(
			projection.get("message")
			or _("Machine capacity could not be verified for the selected Workstation and operation.")
		)
	if projection.get("status") == "duration_insufficient":
		validation_messages.append(
			projection.get("message")
			or _("Expected Duration is insufficient for the planned quantity.")
		)
	if projection.get("working_hours_status") != "ok":
		validation_messages.append(
			projection.get("working_hours_message")
			or _("The proposed operation is outside the Workstation's configured Working Hours.")
		)
	if projection.get("scheduling_conflict_blocked"):
		validation_messages.append(
			projection.get("scheduling_conflict_message")
			or _("The proposed operation overlaps an existing Workstation booking.")
		)
	if validation_messages:
		frappe.throw(
			"<br>".join(validation_messages),
			title=_("Invalid Workstation Schedule"),
		)
	capacity_override_requested = cint(override_capacity)
	if capacity_override_requested and not can_confirm_capacity_overload():
		frappe.throw(
			_("Only an authorized Manufacturing Manager can override Workstation capacity warnings."),
			title=_("Capacity Override Not Allowed"),
		)
	capacity_override_used = bool(capacity_override_requested)
	end_dt = start_dt + timedelta(hours=duration_hours)
	validate_future_schedule(start_dt, end_dt)

	validate_sequence(
		entry,
		start_dt,
		bool(int(override_sequence or 0)),
		packing_roll_ids=packing_roll_selection["roll_ids"] if packing_roll_selection else None,
	)
	confirmation_cache_key = None
	if projection.get("requires_confirmation"):
		fingerprint = make_capacity_move_fingerprint(
			entry.name, workstation, planned_date, planned_start, duration_hours
		)
		confirmation_cache_key = validate_capacity_confirmation(
			capacity_confirmation_token, fingerprint, projection
		)
	capacity_warning = validate_capacity(
		workstation,
		entry.name,
		start_dt,
		end_dt,
		bool(capacity_override_requested),
		confirmed_machine_overload=bool(confirmation_cache_key),
	)

	entry.workstation = workstation
	entry.planned_date = getdate(planned_date)
	entry.planned_start_datetime = start_dt
	entry.planned_end_datetime = end_dt
	if planned_employee:
		company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
		validate_employee(planned_employee, company)
		entry.planned_employee = planned_employee
	entry.status = "Planned"
	if priority:
		entry.priority = priority
	if packing_roll_selection:
		from smart_edge_custom.manufacturing.roll_tracking import _reserve_rolls_for_packing
		_reserve_rolls_for_packing(entry.name, packing_roll_selection["roll_ids"])
	entry.save()
	if capacity_override_used:
		entry.add_comment(
			"Info",
			text=_(
				"Workstation capacity override used by {0} for {1} on {2}. Projected overload: {3} hours."
			).format(
				frappe.session.user,
				workstation,
				getdate(planned_date),
				flt(projection.get("overload_hours")),
			),
		)
	update_unstarted_production_jobs_for_schedule(
		entry, workstation_changed=workstation_changed, operator_changed=operator_changed
	)
	if confirmation_cache_key:
		frappe.cache.delete_value(confirmation_cache_key)
	update_generator_planning_status(entry.final_eb_bom_generator)
	return {
		"message": _("Operation scheduled."),
		"warning": capacity_warning,
		"capacity_status": projection.get("status"),
		"capacity_message": projection.get("message"),
	}


@frappe.whitelist()
def reschedule_operation(**kwargs):
	return schedule_operation(**kwargs)


@frappe.whitelist()
def change_operator(plan_entry, new_operator, reason=None):
	"""Reassign one operation without changing its schedule or execution history."""
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor

	require_planning_supervisor(_("change operation operators"))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	lock_plan_entry(plan_entry)
	entry.reload()
	if entry.status == "Cancelled":
		frappe.throw(_("Cancelled operations cannot be reassigned."), title=_("Operator Reassignment Not Allowed"))
	company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
	validate_employee(new_operator, company)

	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": entry.name, "status": ["!=", "Cancelled"]},
		fields=["name", "status", "operator_employee", "actual_start"],
		order_by="modified desc",
		limit_page_length=20,
	)
	active_jobs = [job for job in jobs if job.status not in {"Completed", "Cancelled"}]
	completed_job = next((job for job in jobs if job.status == "Completed"), None)
	if entry.status == "Completed" or completed_job:
		frappe.throw(
			_("Completed operations cannot be reassigned because execution history must be preserved."),
			title=_("Operator Reassignment Not Allowed"),
		)
	if len(active_jobs) > 1:
		frappe.throw(_("Multiple active Production Jobs exist for this operation."))

	job = None
	if active_jobs:
		job = frappe.get_doc("SmartEdge Production Job", active_jobs[0].name)
		job.check_permission("write")
		lock_production_job(job.name)
		job.reload()
		if job.status == "Completed":
			frappe.throw(
				_("Completed operations cannot be reassigned because execution history must be preserved."),
				title=_("Operator Reassignment Not Allowed"),
			)

	previous_operator = (job.operator_employee if job else None) or entry.get("planned_employee")
	if previous_operator == new_operator:
		return {
			"message": _("Operation is already assigned to this operator."),
			"plan_entry": entry.name,
			"status": job.status if job else entry.status,
			"operator_employee": new_operator,
		}

	entry.planned_employee = new_operator
	entry.save()
	if job:
		# This updates the current assignment only. actual_start, actual_end,
		# productive time, downtime rows, and completion quantities are untouched.
		job.operator_employee = new_operator
		job.save()
	operator_name = frappe.db.get_value("Employee", new_operator, "employee_name")
	comment = _(
		"Operator changed from {0} to {1} by {2}."
	).format(previous_operator or _("Unassigned"), operator_name or new_operator, frappe.session.user)
	if str(reason or "").strip():
		comment += " " + _("Reason: {0}").format(str(reason).strip())
	entry.add_comment("Info", text=comment)
	update_generator_planning_status(entry.final_eb_bom_generator)
	return {
		"message": _("Operator updated."),
		"plan_entry": entry.name,
		"status": job.status if job else entry.status,
		"operator_employee": new_operator,
		"operator_name": operator_name,
		"previous_operator": previous_operator,
	}


@frappe.whitelist()
def unplan_operation(plan_entry):
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	require_planning_supervisor(_("unplan production operations"))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	if is_packing_plan_entry(entry) and entry.status in {"Pending", "Ready", "Planned"}:
		from smart_edge_custom.manufacturing.roll_tracking import release_roll_reservations
		release_roll_reservations(entry.name)
	entry.planned_date = None
	entry.planned_start_datetime = None
	entry.planned_end_datetime = None
	entry.status = "Pending"
	entry.save()
	update_generator_planning_status(entry.final_eb_bom_generator)
	return {"message": _("Operation removed from schedule.")}


def validate_plan_entry(entry):
	if entry.status in {"Completed", "Cancelled"}:
		frappe.throw(_("Completed or cancelled entries cannot be planned."))


def validate_workstation_reassignment_allowed(entry, workstation):
	"""Reject workstation moves after execution has started, including stale plan states."""
	if not entry.get("workstation") or entry.workstation == workstation:
		return
	if entry.status not in {"Pending", "Planned", "Ready"}:
		frappe.throw(
			_("Workstation can only be changed while the operation is Pending, Planned, or Ready and has not started."),
			title=_("Workstation Reassignment Not Allowed"),
		)
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": entry.name},
		fields=["name", "status", "actual_start"],
		limit_page_length=20,
	)
	started = [
		job
		for job in jobs
		if job.actual_start or job.status in {"In Progress", "Paused", "Completed", "Cancelled", "On Hold"}
	]
	if started:
		frappe.throw(
			_("Workstation cannot be changed because production execution has already started for this operation."),
			title=_("Workstation Reassignment Not Allowed"),
		)


def validate_operator_reassignment_allowed(entry, planned_employee):
	"""Preserve execution ownership after a job has started."""
	if not planned_employee or entry.get("planned_employee") == planned_employee:
		return
	if entry.status not in {"Pending", "Planned", "Ready"}:
		frappe.throw(
			_("Operator can only be changed while the operation is Pending, Planned, or Ready and has not started."),
			title=_("Operator Reassignment Not Allowed"),
		)
	started = frappe.db.exists(
		"SmartEdge Production Job",
		{
			"plan_entry": entry.name,
			"status": ["in", ["In Progress", "Paused", "Completed", "Cancelled", "On Hold"]],
		},
	)
	if started:
		frappe.throw(
			_("Operator cannot be changed because production execution has already started for this operation."),
			title=_("Operator Reassignment Not Allowed"),
		)


def update_unstarted_production_jobs_for_schedule(entry, workstation_changed=False, operator_changed=False):
	"""Keep any pre-created draft job aligned without changing its identity or execution history."""
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": entry.name},
		pluck="name",
		limit_page_length=20,
	)
	for job_name in jobs:
		lock_production_job(job_name)
		job = frappe.get_doc("SmartEdge Production Job", job_name)
		job.reload()
		if workstation_changed and (
			job.actual_start or job.status in {"In Progress", "Paused", "Completed", "Cancelled", "On Hold"}
		):
			frappe.throw(
				_("Workstation cannot be changed because production execution has already started for this operation."),
				title=_("Workstation Reassignment Not Allowed"),
			)
		if job.status != "Draft" or job.actual_start:
			continue
		job.check_permission("write")
		job.workstation = entry.workstation
		if operator_changed:
			job.operator_employee = entry.planned_employee
		job.planned_start = entry.planned_start_datetime
		job.planned_end = entry.planned_end_datetime
		job.save()


def validate_material_plan_released(entry):
	if entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		return
	if not frappe.db.exists(
		"SmartEdge Production Plan Entry",
		{
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"plan_type": ["!=", MATERIAL_PLAN_TYPE],
			"status": ["!=", "Cancelled"],
		},
	):
		frappe.throw(
			_("Send Final EB BOM Generator {0} to planning before scheduling linked material production.").format(
				entry.final_eb_bom_generator
			)
		)


def validate_material_scheduling_context(entry):
	"""Validate the linked manufacturing identity without checking its output stock."""
	if entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		return
	if not entry.get("work_order") or not entry.get("bom_no") or not entry.get("material_item"):
		frappe.throw(_("Linked material Work Order, BOM and production Item are required for scheduling."))
	work_order = frappe.db.get_value(
		"Work Order",
		entry.work_order,
		[
			"docstatus",
			"status",
			"production_item",
			"bom_no",
			"qty",
			"custom_final_eb_bom_generator",
			"custom_material_type",
		],
		as_dict=True,
	)
	if not work_order:
		from smart_edge_custom.manufacturing.work_order_sync import missing_work_order_message
		frappe.throw(missing_work_order_message(entry.work_order, entry.name))
	if work_order.docstatus != 1 or work_order.status in {"Stopped", "Cancelled", "Completed"}:
		frappe.throw(_("Select an active submitted material Work Order before scheduling this operation."))
	if work_order.production_item != entry.material_item or work_order.bom_no != entry.bom_no:
		frappe.throw(_("The linked material Work Order does not match the planning Item and BOM."))
	if flt(work_order.qty) <= 0:
		frappe.throw(_("The linked material Work Order quantity must be greater than zero."))
	if work_order.get("custom_final_eb_bom_generator") not in (None, "", entry.final_eb_bom_generator):
		frappe.throw(_("The linked material Work Order belongs to another Final EB production."))
	if work_order.get("custom_material_type") not in (None, "", entry.material_type):
		frappe.throw(_("The linked material Work Order has a different material type."))


def validate_workstation_compatibility(operation, operation_row, workstation):
	if not workstation:
		frappe.throw(_("Workstation is required."))
	if not operation or not frappe.db.exists("Operation", operation):
		frappe.throw(_("Operation {0} does not exist.").format(operation))
	if not frappe.db.exists("Workstation", workstation):
		frappe.throw(_("Workstation {0} does not exist.").format(workstation))
	workstation_doc = frappe.get_doc("Workstation", workstation)
	workstation_doc.check_permission("read")
	if workstation_doc.get("disabled"):
		frappe.throw(_("Workstation {0} is disabled.").format(workstation))
	return


def combine_date_time(date_value, time_value):
	date_value = getdate(date_value)
	time_value = normalize_schedule_time(time_value)
	return get_datetime(datetime.combine(date_value, get_time(time_value or time(9, 0))))


def normalize_schedule_time(value):
	"""Accept the board's HH:mm value while preserving already supplied seconds."""
	if not isinstance(value, str):
		return value
	value = value.strip()
	if re.fullmatch(r"\d{1,2}:\d{2}", value):
		return f"{value}:00"
	return value


def validate_future_schedule(start_dt, end_dt):
	"""Reject new or rescheduled slots that are not strictly in the future."""
	now = now_datetime().replace(second=0, microsecond=0)
	if start_dt <= now:
		frappe.throw(
			_("Production planning cannot be scheduled for a past date or time."),
			title=_("Invalid Production Planning Schedule"),
		)
	if end_dt <= start_dt:
		frappe.throw(
			_("Invalid Production Planning Schedule")
			+ "<br>"
			+ _("End time must be later than the start time."),
			title=_("Invalid Production Planning Schedule"),
		)


def get_entry_default_duration(entry):
	if entry.operation_row:
		standard_time, time_uom = frappe.db.get_value(
			"Final EB BOM Operation", entry.operation_row, ["standard_time", "time_uom"]
		) or (0, "Minutes")
		if flt(standard_time):
			return flt(standard_time) if time_uom == "Hours" else flt(standard_time) / 60
	return 1


def is_packing_plan_entry(entry):
	return (
		str(entry.get("planning_category") or "").strip().lower() == "packing"
		or "pack" in str(entry.get("operation") or "").strip().lower()
	)


def validate_packing_rolls_for_completion(entry, job, completion_type):
	"""Require the complete accepted quantity before physical Packing posting.

	Packing uses a specialized FG receipt after ``complete_job``.  A partial
	reservation must not be allowed to mark the Job complete and then receipt a
	smaller quantity than the accepted Job output.  This validation is limited to
	Physical WIP parents; legacy non-stock Packing remains unchanged.
	"""
	if not is_packing_plan_entry(entry) or not physical_operation_route_active(entry.final_eb_bom_generator):
		return
	if completion_type != "Full Completion":
		return

	rolls = frappe.get_all(
		"SmartEdge Roll",
		filters={
			"packing_job": job.name,
			"qc_status": "Passed",
			"process_status": "Packed",
			"packing_status": "Packed",
		},
		fields=["name", "current_meter_quantity"],
		limit_page_length=1000,
	)
	if not rolls:
		rolls = frappe.get_all(
			"SmartEdge Roll",
			filters={
				"reserved_for_production_job": job.name,
				"qc_status": "Passed",
				"process_status": "Reserved for Packing",
				"packing_status": "Reserved",
			},
			fields=["name", "current_meter_quantity"],
			limit_page_length=1000,
		)
	if not rolls:
		frappe.throw(
			_("Select and reserve QC-approved whole Rolls before completing Packing Job {0}.").format(job.name),
			title=_("Packing Rolls Required"),
		)
	roll_quantity = sum(flt(row.current_meter_quantity) for row in rolls)
	expected_quantity = flt(job.good_qty) or flt(job.actual_qty_meter)
	if expected_quantity <= 0 or abs(roll_quantity - expected_quantity) > 0.000001:
		frappe.throw(
			_(
				"Packing Job {0} has {1} m of reserved QC-approved Rolls, "
				"but its accepted completion quantity is {2} m. Select the complete Roll set before completing."
			).format(job.name, roll_quantity, expected_quantity),
			title=_("Packing Quantity Does Not Reconcile"),
		)


def get_packing_roll_selection(entry, roll_ids):
	if not is_packing_plan_entry(entry):
		return None
	from smart_edge_custom.manufacturing.roll_tracking import validate_packing_roll_selection

	if not roll_ids:
		roll_ids = frappe.get_all(
			"SmartEdge Roll",
			filters={"reserved_for_plan_entry": entry.name, "process_status": "Reserved for Packing"},
			pluck="name",
		)
	return validate_packing_roll_selection(entry.name, roll_ids)


def validate_sequence(entry, start_dt, override=False, packing_roll_ids=None):
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry

	previous = get_sequence_predecessors(entry)
	for row in previous:
		if row.status == "Completed":
			continue
		# QC is a roll-level checkpoint.  Once this Packing plan has a
		# server-validated selection of passed whole rolls, the unfinished
		# rolls must not hold those selected rolls behind the QC end time.
		if is_packing_plan_entry(entry) and packing_roll_ids and is_qc_entry(row):
			continue
		if not row.planned_end_datetime:
			if not override:
				frappe.throw(
					_("Cannot schedule {0} before preceding operation {1}.").format(entry.operation, row.operation)
				)
			continue
		if get_datetime(row.planned_end_datetime) > start_dt and not override:
			frappe.throw(
				_("Cannot schedule {0} before {1} ends on {2}.").format(
					entry.operation, row.operation, row.planned_end_datetime
				)
			)


def get_sequence_predecessors(entry):
	if entry.get("plan_type") == "Required Material":
		return []
	if entry.operation_row:
		current = frappe.db.get_value(
			"Final EB BOM Operation",
			entry.operation_row,
			["parent", "parentfield", "idx"],
			as_dict=True,
		)
		if current:
			previous_operation_rows = frappe.get_all(
				"Final EB BOM Operation",
				filters={
					"parent": current.parent,
					"parentfield": current.parentfield,
					"idx": ["<", current.idx],
					"mandatory": 1,
				},
				fields=["name"],
				order_by="idx desc",
				limit_page_length=1,
			)
			if previous_operation_rows:
				previous_entry = frappe.get_all(
					"SmartEdge Production Plan Entry",
					filters={
						"final_eb_bom_generator": entry.final_eb_bom_generator,
						"operation_row": previous_operation_rows[0].name,
						"status": ["!=", "Cancelled"],
					},
					fields=["name", "operation", "planned_end_datetime", "sequence", "status"],
					order_by="modified desc",
					limit_page_length=1,
				)
				if previous_entry:
					return previous_entry
	return frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"sequence": ["<", entry.sequence],
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "operation", "planned_end_datetime", "sequence", "status"],
		order_by="sequence desc, modified desc",
		limit_page_length=1,
	)


def validate_capacity(
	workstation,
	plan_entry,
	start_dt,
	end_dt,
	override=False,
	confirmed_machine_overload=False,
):
	lock_workstation(workstation)
	capacity_hours = get_workstation_capacity_hours(workstation, getdate(start_dt))
	planned = get_workstation_planned_minutes(workstation, getdate(start_dt), plan_entry)
	new_minutes = (end_dt - start_dt).total_seconds() / 60
	overlaps = get_overlapping_entries(workstation, start_dt, end_dt, plan_entry)
	if overlaps:
		frappe.throw(
			format_workstation_conflict_message(workstation, start_dt, end_dt, overlaps),
			title=_("Workstation Scheduling Conflict"),
		)
	if (
		capacity_hours
		and planned + new_minutes > capacity_hours * 60
		and not override
		and not confirmed_machine_overload
	):
		over_by = (planned + new_minutes - capacity_hours * 60) / 60
		frappe.throw(_("Capacity exceeded by {0} hours.").format(flt(over_by, 2)))
	if overlaps:
		return _("Parallel Workstation capacity used for overlapping bookings: {0}.").format(
			", ".join(conflict["name"] for conflict in overlaps)
		)
	return None


def get_workstation_capacity_hours(workstation, planned_date):
	if not workstation or workstation == _("Unassigned"):
		return 0
	doc = frappe.get_doc("Workstation", workstation)
	if flt(doc.total_working_hours):
		return flt(doc.total_working_hours)
	if doc.get("working_hours"):
		total = 0
		for row in doc.working_hours:
			if row.start_time and row.end_time:
				start_dt = datetime.combine(planned_date, get_time(row.start_time))
				end_dt = datetime.combine(planned_date, get_time(row.end_time))
				total += max((end_dt - start_dt).total_seconds() / 3600, 0)
		return total
	return 0


def get_workstation_planned_minutes(workstation, planned_date, exclude_entry=None):
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"workstation": workstation,
			"planned_date": planned_date,
			"status": ["in", ACTIVE_PLAN_STATUSES],
		},
		fields=["name", "planned_start_datetime", "planned_end_datetime"],
	)
	total = 0
	for row in rows:
		if row.name == exclude_entry or not row.planned_start_datetime or not row.planned_end_datetime:
			continue
		total += (get_datetime(row.planned_end_datetime) - get_datetime(row.planned_start_datetime)).total_seconds() / 60
	return total


def get_overlapping_entries(workstation, start_dt, end_dt, exclude_entry=None):
	plan_rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"workstation": workstation, "status": ["in", ACTIVE_PLAN_STATUSES]},
		fields=[
			"name",
			"work_order",
			"operation",
			"status",
			"planned_start_datetime",
			"planned_end_datetime",
		],
		limit_page_length=5000,
	)
	overlaps = []
	represented_work_order_operations = set()
	for row in plan_rows:
		if row.work_order and row.operation:
			represented_work_order_operations.add((row.work_order, row.operation))
		if row.name == exclude_entry or not row.planned_start_datetime or not row.planned_end_datetime:
			continue
		existing_start = get_datetime(row.planned_start_datetime)
		existing_end = get_datetime(row.planned_end_datetime)
		if existing_start < end_dt and start_dt < existing_end:
			overlaps.append(
				{
					"name": row.name,
					"operation": row.operation,
					"status": row.status,
					"start": existing_start,
					"end": existing_end,
					"source": "SmartEdge Production Plan Entry",
				}
			)

	operation_rows = frappe.get_all(
		"Work Order Operation",
		filters={"workstation": workstation, "status": ["!=", "Completed"]},
		fields=["name", "parent", "operation", "status", "planned_start_time", "planned_end_time"],
		limit_page_length=5000,
	)
	parents = list({row.parent for row in operation_rows if row.parent})
	active_parents = set()
	if parents:
		active_parents = {
			row.name
			for row in frappe.get_all(
				"Work Order",
				filters={
					"name": ["in", parents],
					"docstatus": 1,
					"status": ["in", ACTIVE_WORK_ORDER_STATUSES],
				},
				fields=["name"],
				limit_page_length=len(parents),
			)
		}
	for row in operation_rows:
		if (
			row.parent not in active_parents
			or (row.parent, row.operation) in represented_work_order_operations
			or not row.planned_start_time
			or not row.planned_end_time
		):
			continue
		existing_start = get_datetime(row.planned_start_time)
		existing_end = get_datetime(row.planned_end_time)
		if existing_start < end_dt and start_dt < existing_end:
			overlaps.append(
				{
					"name": row.name,
					"operation": row.operation,
					"status": row.status,
					"start": existing_start,
					"end": existing_end,
					"source": "Work Order Operation",
				}
			)
	return overlaps


def format_workstation_conflict_message(workstation, start_dt, end_dt, conflicts):
	conflict_lines = []
	for conflict in conflicts[:10]:
		operation = f" / {conflict['operation']}" if conflict.get("operation") else ""
		conflict_lines.append(
			_("{0}{1} from {2} to {3}").format(
				conflict["name"],
				operation,
				get_datetime(conflict["start"]).strftime("%d-%m-%Y %H:%M:%S"),
				get_datetime(conflict["end"]).strftime("%d-%m-%Y %H:%M:%S"),
			)
		)
	if len(conflicts) > len(conflict_lines):
		conflict_lines.append(_("and {0} more booking(s)").format(len(conflicts) - len(conflict_lines)))
	return _(
		"Workstation Scheduling Conflict: {0} is already booked: {1}. "
		"The requested operation from {2} to {3} overlaps this booking."
	).format(
		workstation,
		"; ".join(conflict_lines),
		get_datetime(start_dt).strftime("%d-%m-%Y %H:%M:%S"),
		get_datetime(end_dt).strftime("%d-%m-%Y %H:%M:%S"),
	)


def update_generator_planning_status(production):
	from smart_edge_custom.manufacturing.material_production import refresh_material_readiness

	refresh_material_readiness(production)
	status = get_completed_parent_status(production)
	if status:
		set_generator_status(production, status)
		return
	if get_generator_lifecycle_status(frappe.get_doc("Final EB BOM Generator", production)) == "Completed":
		return

	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": production,
			"plan_type": ["!=", "Required Material"],
			"status": ["!=", "Cancelled"],
		},
		fields=["status"],
	)
	if not rows:
		doc = frappe.get_doc("Final EB BOM Generator", production)
		if doc.get("selected_route") and doc.get("finished_item"):
			set_generator_status(production, "Ready for Planning")
		return
	statuses = [row.status for row in rows]
	if any(status == "In Progress" for status in statuses):
		status = "In Production"
	elif all(status in {"Ready", "Planned", "Completed"} for status in statuses):
		status = "Planned"
	elif any(status in {"Ready", "Planned", "In Progress", "Completed"} for status in statuses):
		status = "Partially Planned"
	else:
		status = "Ready for Planning"
	set_generator_status(production, status)


@frappe.whitelist()
def synchronize_generator_status(production):
	"""Idempotently recalculate and persist one Generator's lifecycle status."""
	update_generator_planning_status(production)
	doc = frappe.get_doc("Final EB BOM Generator", production)
	return {
		"name": doc.name,
		"status": doc.get("status"),
		"production_status": doc.get("production_status"),
	}


def get_completed_parent_status(production):
	if not production or not frappe.db.exists("Final EB BOM Generator", production):
		return None
	if not parent_allows_completed_status(production):
		return None
	mandatory_rows = get_mandatory_route_operation_rows(production)
	if not mandatory_rows:
		return None

	for operation_row in mandatory_rows:
		entries = get_route_plan_entries(production, operation_row.name)
		if not entries or any(entry.status != "Completed" for entry in entries):
			return None
		for entry in entries:
			job = get_completed_job_for_plan_entry(entry)
			if not job or job.status != "Completed":
				return None
			if job.get("completion_type") == "Partial Completion" and (
				flt(job.get("remaining_qty_meter")) or flt(job.get("remaining_qty_kg"))
			):
				return None
	return "Completed"


def parent_allows_completed_status(production):
	current_status = get_generator_lifecycle_status(frappe.get_doc("Final EB BOM Generator", production))
	if current_status == "Cancelled":
		return False
	status_field = frappe.get_meta("Final EB BOM Generator", cached=True).get_field("status")
	return bool(status_field and "Completed" in (status_field.options or "").splitlines())


def get_mandatory_route_operation_rows(production):
	return frappe.get_all(
		"Final EB BOM Operation",
		filters={"parent": production, "parentfield": "operation_preview", "mandatory": 1},
		fields=["name", "operation", "sequence", "idx"],
		order_by="idx asc",
		limit_page_length=500,
	)


def get_route_plan_entry(production, operation_row):
	entries = get_route_plan_entries(production, operation_row)
	return entries[0] if len(entries) == 1 else None


def get_route_plan_entries(production, operation_row):
	entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": production,
			"operation_row": operation_row,
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "status", "operation", "operation_row", "production_job"],
		order_by="modified desc",
		limit_page_length=500,
	)
	return entries


def get_completed_job_for_plan_entry(entry):
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": entry.name, "status": ["!=", "Cancelled"]},
		fields=[
			"name",
			"status",
			"completion_type",
			"remaining_qty_meter",
			"remaining_qty_kg",
			"manufacturing_stock_entry",
		],
		order_by="modified desc",
		limit_page_length=10,
	)
	if not jobs:
		return None
	active_jobs = [job for job in jobs if job.status != "Completed"]
	if active_jobs:
		return None
	if entry.get("production_job"):
		for job in jobs:
			if job.name == entry.production_job:
				return job
		return None
	return jobs[0]


def set_generator_status(production, status):
	current_status = get_generator_lifecycle_status(frappe.get_doc("Final EB BOM Generator", production))
	if current_status == "Cancelled":
		return
	if current_status == status and status != "Completed":
		return
	doc = frappe.get_doc("Final EB BOM Generator", production)
	if status == "Completed":
		create_final_manufacturing_stock_entry(doc)
	# ``production_status`` is the lifecycle source used by server validation,
	# while ``status`` is the visible DocType field. Keep both synchronized so
	# refresh/reload cannot show a stale legacy status.
	doc.db_set("production_status", status, update_modified=False)
	doc.db_set("status", status, update_modified=False)
	from smart_edge_custom.manufacturing.material_production import refresh_material_readiness
	refresh_material_readiness(production)


def create_final_manufacturing_stock_entry(doc):
	if isinstance(doc, str):
		doc = frappe.get_doc("Final EB BOM Generator", doc)
	if not doc.meta.has_field("final_manufacturing_stock_entry"):
		frappe.throw(_("Final Manufacturing Stock Entry reference field is not configured. Please run migration."))
	lock_final_eb_bom_generator(doc.name)
	doc.reload()
	existing_stock_entry = get_active_stock_entry_reference(
		doc.get("final_manufacturing_stock_entry"),
		_("Final EB BOM Generator {0}").format(doc.name),
	)
	if existing_stock_entry:
		if get_completed_parent_status(doc.name) == "Completed":
			final_jobs = get_final_finished_jobs(doc.name)
			incremental_jobs = get_unposted_final_packing_jobs(final_jobs)
			if incremental_jobs:
				return post_incremental_packing_receipts(doc, existing_stock_entry, incremental_jobs)
			validate_final_job_stock_entry_matches_parent(doc.name, final_jobs, existing_stock_entry)
			if all(job.get("manufacturing_stock_entry") == existing_stock_entry for job in final_jobs):
				link_final_manufacturing_stock_entry(
					doc,
					final_jobs,
					existing_stock_entry,
					sum(flt(job.stock_posting_qty) for job in final_jobs),
				)
		show_existing_stock_entry_message(existing_stock_entry)
		return existing_stock_entry
	# Physical Packing posts one submitted Repack per roll.  Treat the complete
	# set of those receipts as the final evidence and never create a second
	# job-level Manufacture/Repack for the same accepted quantity.
	roll_evidence = _physical_packing_roll_evidence(doc)
	if roll_evidence:
		doc.db_set("final_manufacturing_stock_entry", roll_evidence[-1]["stock_entry"], update_modified=False)
		if doc.meta.has_field("final_stock_posted_qty"):
			doc.db_set("final_stock_posted_qty", roll_evidence[-1]["total_qty"], update_modified=False)
		return roll_evidence[-1]["stock_entry"]
	if get_completed_parent_status(doc.name) != "Completed":
		frappe.throw(_("Final manufacturing Stock Entry can only be created after all mandatory operations are completed."))

	final_jobs = get_final_finished_jobs(doc.name)
	existing_stock_entry = get_existing_final_job_stock_entry(doc, final_jobs)
	if existing_stock_entry:
		show_existing_stock_entry_message(existing_stock_entry)
		return existing_stock_entry

	source_warehouse = validate_stock_warehouse(doc.get("source_warehouse"), doc.company, _("Source Warehouse"))
	target_warehouse = validate_stock_warehouse(
		get_finished_goods_warehouse(doc), doc.company, _("Finished Goods Warehouse")
	)
	finished_qty = sum(flt(job.stock_posting_qty) for job in final_jobs)
	if finished_qty <= 0:
		frappe.throw(_("Actual accepted Finished Goods quantity is required before creating Stock Entry."))

	physical_route = physical_operation_route_active(doc.name)
	if physical_route:
		from smart_edge_custom.manufacturing.physical_operation_stock import final_wip_components
		components, component_batch_nos = final_wip_components(doc, final_jobs)
	else:
		components = get_final_stock_components(doc, finished_qty, source_warehouse)
		component_batch_nos = get_final_component_batch_nos(doc, components)
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovered_components
	recovered_components, recovered_batches, recovered_allocations = get_recovered_components(production=doc.name)
	components.extend(recovered_components)
	component_batch_nos.extend(recovered_batches)
	finished_row = get_finished_stock_row(doc, finished_qty, target_warehouse)
	validate_batch_control([finished_row])
	unusable_lots = frappe.get_all(
		"SmartEdge Unusable Scrap Lot",
		filters={"production_job": ["in", [job.name for job in final_jobs]], "status": "Pending Storage"},
		pluck="name",
	)

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.purpose = "Repack" if physical_route else "Manufacture"
	stock_entry_type = stock_entry.purpose
	if stock_entry.meta.has_field("stock_entry_type") and frappe.db.exists("Stock Entry Type", stock_entry_type):
		stock_entry.stock_entry_type = stock_entry_type
	stock_entry.company = doc.company
	stock_entry.from_warehouse = source_warehouse if not physical_route else None
	stock_entry.to_warehouse = target_warehouse
	stock_entry.posting_date = nowdate()
	stock_entry.remarks = _("Final manufacturing Stock Entry for Final EB BOM Generator {0}").format(doc.name)
	if unusable_lots:
		from smart_edge_custom.manufacturing.unusable_scrap import set_storage_metadata
		set_storage_metadata(stock_entry, unusable_lots)
	for component in components:
		stock_entry.append("items", component)
	stock_entry.append("items", finished_row)
	from smart_edge_custom.manufacturing.unusable_scrap import append_storage_output
	unusable_pairs = [append_storage_output(stock_entry, name) for name in unusable_lots]
	stock_entry.insert()
	if physical_route:
		from smart_edge_custom.manufacturing.physical_operation_stock import validate_posted_precision
		validate_posted_precision(stock_entry)
	set_final_component_batch_bundles(stock_entry, component_batch_nos)
	from smart_edge_custom.manufacturing.unusable_scrap import attach_storage_bundle
	for pair in unusable_pairs:
		attach_storage_bundle(stock_entry, pair)
	stock_entry.save()
	stock_entry.submit()
	if unusable_lots:
		from smart_edge_custom.manufacturing.unusable_scrap import mark_stored
		mark_stored(unusable_lots, stock_entry.name)
	from smart_edge_custom.manufacturing.scrap_recovery import mark_recovered_components_consumed
	mark_recovered_components_consumed(recovered_allocations, stock_entry.name)
	link_final_manufacturing_stock_entry(doc, final_jobs, stock_entry.name, finished_qty)
	from smart_edge_custom.manufacturing.batch_genealogy import extract_batch_movements

	posted_entry = frappe.get_doc("Stock Entry", stock_entry.name)
	from smart_edge_custom.manufacturing.roll_tracking import link_packed_rolls_to_finished_batch
	link_packed_rolls_to_finished_batch(stock_entry.name, doc.name)
	finished_batches = dict.fromkeys(
		row["batch_no"] for row in extract_batch_movements(posted_entry)
		if row["direction"] == "Output" and row["item_code"] == doc.finished_item
	)
	frappe.msgprint(
		_("Manufacturing Stock Entry {0} submitted. Finished Goods Batch: {1}.").format(
			frappe.get_desk_link("Stock Entry", stock_entry.name),
			", ".join(frappe.get_desk_link("Batch", batch) for batch in finished_batches),
		), alert=True, indicator="green",
	)
	return stock_entry.name


def _physical_packing_roll_evidence(doc):
	"""Return complete submitted roll receipts for a Physical Packing final job."""
	if not doc or not physical_operation_route_active(doc.name):
		return None
	final_jobs = get_final_finished_jobs(doc.name)
	job_names = [job.name for job in final_jobs]
	if not job_names:
		return None
	rolls = frappe.get_all(
		"SmartEdge Roll",
		filters={"packing_job": ["in", job_names], "process_status": "Packed", "packing_status": "Packed", "qc_status": "Passed"},
		fields=["name", "current_meter_quantity"], limit_page_length=1000,
	)
	if not rolls:
		return None
	entries = []
	for roll in rolls:
		remark = f"SmartEdge physical Packing roll {roll.name}"
		row = frappe.db.get_value("Stock Entry", {"remarks": remark, "docstatus": 1}, ["name"], as_dict=True)
		if not row:
			return None
		entries.append({"stock_entry": row.name, "qty": flt(roll.current_meter_quantity)})
	total_qty = sum(row["qty"] for row in entries)
	expected_qty = sum(flt(job.stock_posting_qty) for job in final_jobs)
	if expected_qty <= 0 or abs(total_qty - expected_qty) > 0.000001:
		return None
	return [{"stock_entry": entries[-1]["stock_entry"], "total_qty": total_qty}]


def get_unposted_final_packing_jobs(final_jobs):
	"""Return completed Packing executions not represented by a stock receipt."""
	result = []
	for job in final_jobs:
		if job.get("manufacturing_stock_entry"):
			continue
		entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
		if "pack" in str(entry.get("planning_category") or "").lower() or "pack" in str(entry.get("operation") or "").lower():
			result.append((job, entry))
	return result


def post_incremental_packing_receipts(doc, original_stock_entry, jobs):
	"""Receive newly packed rolls without repeating the original manufacture.

	The first final receipt remains the manufacture transaction. Later Packing
	executions use a physical-WIP Repack only when the explicit physical route is
	active; the established non-stock route continues to use independent
	Material Receipts into its output Batch.
	"""
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry, extract_batch_movements
	physical_route = physical_operation_route_active(doc.name)
	physical_stock = None
	if physical_route:
		from smart_edge_custom.manufacturing import physical_operation_stock as physical_stock

	original = frappe.get_doc("Stock Entry", original_stock_entry)
	output = next((row for row in original.items if row.get("is_finished_item") and row.item_code == doc.finished_item), None)
	if not output or not output.t_warehouse:
		frappe.throw(_("The original finished-goods Stock Entry has no validated output warehouse."))
	output_movements = [row for row in extract_batch_movements(original)
		if row["direction"] == "Output" and row["item_code"] == doc.finished_item]
	batch_names = list(dict.fromkeys(row["batch_no"] for row in output_movements))
	if len(batch_names) != 1:
		frappe.throw(_("The original finished-goods Stock Entry must identify exactly one finished-goods Batch before an incremental Packing receipt can be posted."))
	batch_name = batch_names[0]
	if frappe.db.get_value("Batch", batch_name, "item") != doc.finished_item:
		frappe.throw(_("Finished-goods Batch {0} does not belong to Item {1}.").format(batch_name, doc.finished_item))

	posted = []
	for job, entry in jobs:
		# The job lock is held by complete_production_job for normal callers; keep
		# this lock for direct/retry callers as well.
		frappe.db.sql("select name from `tabSmartEdge Production Job` where name=%s for update", (job.name,))
		job = frappe.get_doc("SmartEdge Production Job", job.name)
		reference = f"SmartEdge incremental Packing receipt {job.name}"
		existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		if len(existing) > 1:
			frappe.throw(_("Multiple incremental Packing receipts exist for {0}; review them before retrying.").format(job.name))
		if existing:
			if existing[0].docstatus != 1:
				frappe.throw(_("Draft incremental Packing receipt {0} exists; review it before retrying.").format(existing[0].name))
			job.db_set("manufacturing_stock_entry", existing[0].name, update_modified=False)
			from smart_edge_custom.manufacturing.roll_tracking import link_packed_rolls_to_finished_batch
			link_packed_rolls_to_finished_batch(existing[0].name, doc.name)
			if job.meta.has_field("stock_posted_qty"):
				job.db_set("stock_posted_qty", flt(job.good_qty) or flt(job.actual_qty_meter), update_modified=False)
			posted.append(existing[0].name)
			continue
		if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
			frappe.throw(_("The incremental Packing receipt for {0} was cancelled; use the controlled amendment workflow.").format(job.name))

		rolls = frappe.get_all("SmartEdge Roll", filters={
			"packing_job": job.name, "qc_status": "Passed", "process_status": "Packed", "packing_status": "Packed",
		}, fields=["name", "current_meter_quantity"], limit_page_length=500)
		packed_qty = sum(flt(row.current_meter_quantity) for row in rolls)
		job_qty = flt(job.good_qty) or flt(job.actual_qty_meter)
		if not rolls or abs(packed_qty - job_qty) > 0.000001:
			frappe.throw(_("Packing Job {0} has {1} m of packed QC-approved rolls, but its receipt quantity is {2} m.").format(job.name, packed_qty, job_qty))
		if physical_route:
			physical_reference = f"SmartEdge incremental Packing physical Repack {job.name}"
			physical_existing = frappe.get_all("Stock Entry", filters={
				"remarks": physical_reference, "docstatus": ["<", 2],
			}, fields=["name", "docstatus"])
			if len(physical_existing) > 1:
				frappe.throw(_("Multiple physical Packing receipts exist for {0}; review them before retrying.").format(job.name))
			physical_is_new = not physical_existing
			stock_entry_name = physical_stock.post_incremental_packing_repack(
				doc, job, output, batch_name,
			)
			posted.append(stock_entry_name)
			job.db_set("manufacturing_stock_entry", stock_entry_name, update_modified=False)
			if job.meta.has_field("stock_posted_qty"):
				job.db_set("stock_posted_qty", job_qty, update_modified=False)
			lot = job.get("output_production_lot")
			if lot and frappe.db.exists("SmartEdge Production Lot", lot):
				frappe.db.set_value("SmartEdge Production Lot", lot, "stock_entry", stock_entry_name, update_modified=False)
			if physical_is_new and doc.meta.has_field("final_stock_posted_qty"):
				current_posted = flt(frappe.db.get_value("Final EB BOM Generator", doc.name, "final_stock_posted_qty"))
				doc.db_set("final_stock_posted_qty", current_posted + job_qty, update_modified=False)
			capture_stock_entry(stock_entry_name)
			from smart_edge_custom.manufacturing.roll_tracking import link_packed_rolls_to_finished_batch
			link_packed_rolls_to_finished_batch(stock_entry_name, doc.name)
			from smart_edge_custom.manufacturing.production_lot import reconcile_final_production
			reconcile_final_production(doc.name, stock_entry_name)
			continue

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = doc.company
		stock_entry.purpose = "Material Receipt"
		if stock_entry.meta.has_field("stock_entry_type") and frappe.db.exists("Stock Entry Type", "Material Receipt"):
			stock_entry.stock_entry_type = "Material Receipt"
		stock_entry.posting_date = nowdate()
		stock_entry.set_posting_time = 1
		stock_entry.remarks = reference
		row = stock_entry.append("items", {
			"item_code": doc.finished_item,
			"qty": job_qty,
			"uom": output.uom or "Meter",
			"stock_uom": output.stock_uom,
			"conversion_factor": output.conversion_factor,
			"t_warehouse": output.t_warehouse,
			"is_finished_item": 1,
			"allow_zero_valuation_rate": 1,
		})
		if output.get("valuation_rate"):
			row.basic_rate = output.valuation_rate
		stock_entry.insert(ignore_permissions=True)
		from erpnext.stock.serial_batch_bundle import SerialBatchCreation
		bundle = SerialBatchCreation({
			"item_code": row.item_code, "warehouse": row.t_warehouse,
			"posting_date": stock_entry.posting_date, "posting_time": stock_entry.posting_time,
			"voucher_type": stock_entry.doctype, "voucher_no": stock_entry.name,
			"voucher_detail_no": row.name, "qty": flt(row.transfer_qty),
			"type_of_transaction": "Inward", "company": stock_entry.company,
			"do_not_submit": True,
		}).make_serial_and_batch_bundle(batch_nos={batch_name: flt(row.transfer_qty)})
		row.serial_and_batch_bundle = bundle.name
		row.use_serial_batch_fields = 0
		stock_entry.save(ignore_permissions=True)
		stock_entry.submit()
		capture_stock_entry(stock_entry)
		posted.append(stock_entry.name)
		job.db_set("manufacturing_stock_entry", stock_entry.name, update_modified=False)
		from smart_edge_custom.manufacturing.roll_tracking import link_packed_rolls_to_finished_batch
		link_packed_rolls_to_finished_batch(stock_entry.name, doc.name)
		if job.meta.has_field("stock_posted_qty"):
			job.db_set("stock_posted_qty", job_qty, update_modified=False)
		lot = job.get("output_production_lot")
		if lot and frappe.db.exists("SmartEdge Production Lot", lot):
			frappe.db.set_value("SmartEdge Production Lot", lot, "stock_entry", stock_entry.name, update_modified=False)
		if doc.meta.has_field("final_stock_posted_qty"):
			current_posted = flt(frappe.db.get_value("Final EB BOM Generator", doc.name, "final_stock_posted_qty"))
			doc.db_set("final_stock_posted_qty", current_posted + job_qty, update_modified=False)
		from smart_edge_custom.manufacturing.production_lot import reconcile_final_production
		reconcile_final_production(doc.name, stock_entry.name)
	return posted[-1] if len(posted) == 1 else posted


def get_active_stock_entry_reference(stock_entry, source_label):
	if not stock_entry:
		return None
	docstatus = frappe.db.get_value("Stock Entry", stock_entry, "docstatus")
	if docstatus is None:
		frappe.throw(
			_("Linked Manufacturing Stock Entry {0} for {1} is missing. Resolve the reference before reposting.").format(
				stock_entry, source_label
			)
		)
	if cint(docstatus) == 2:
		frappe.throw(
			_("Linked Manufacturing Stock Entry {0} for {1} is cancelled. Use the approved recovery or amendment workflow.").format(
				stock_entry, source_label
			)
		)
	return stock_entry


def get_existing_final_job_stock_entry(doc, final_jobs):
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	if not job_meta.has_field("manufacturing_stock_entry"):
		return None
	existing_references = set()
	unposted_jobs = []
	for job in final_jobs:
		stock_entry = get_active_stock_entry_reference(
			job.get("manufacturing_stock_entry"),
			_("Production Job {0}").format(job.name),
		)
		if stock_entry:
			existing_references.add(stock_entry)
			if (
				job_meta.has_field("stock_posted_qty")
				and flt(job.get("stock_posted_qty")) != flt(job.stock_posting_qty)
			):
				frappe.throw(
					_("Production Job {0} is linked to Manufacturing Stock Entry {1}, but its posted quantity is inconsistent.").format(
						job.name, stock_entry
					)
				)
		else:
			unposted_jobs.append(job.name)
	if not existing_references:
		return None
	if len(existing_references) > 1:
		frappe.throw(
			_("Multiple active Manufacturing Stock Entries are linked to the final production jobs for {0}: {1}").format(
				doc.name, ", ".join(sorted(existing_references))
			)
		)
	if unposted_jobs:
		frappe.throw(
			_("Some final Production Jobs for {0} are already linked to a Manufacturing Stock Entry, but these jobs are not: {1}. Resolve the posting traceability before reposting.").format(
				doc.name, ", ".join(unposted_jobs)
			)
		)
	stock_entry = next(iter(existing_references))
	link_final_manufacturing_stock_entry(doc, final_jobs, stock_entry, sum(flt(job.stock_posting_qty) for job in final_jobs))
	return stock_entry


def validate_final_job_stock_entry_matches_parent(production, final_jobs, parent_stock_entry):
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	if not job_meta.has_field("manufacturing_stock_entry"):
		return
	for job in final_jobs:
		stock_entry = get_active_stock_entry_reference(
			job.get("manufacturing_stock_entry"),
			_("Production Job {0}").format(job.name),
		)
		if stock_entry and stock_entry != parent_stock_entry:
			remarks = frappe.db.get_value("Stock Entry", stock_entry, "remarks") or ""
			if _is_valid_incremental_packing_stock_entry(stock_entry, job.name):
				continue
			frappe.throw(
				_("Production Job {0} is linked to {1}, but {2} is linked to {3}. Resolve the posting traceability before reposting.").format(
					job.name, stock_entry, production, parent_stock_entry
				)
			)


def _is_valid_incremental_packing_stock_entry(stock_entry, job_name):
	"""Allow only approved Packing receipt references for this job."""
	remarks = frappe.db.get_value("Stock Entry", stock_entry, "remarks") or ""
	purpose = frappe.db.get_value("Stock Entry", stock_entry, "purpose")
	if remarks == f"SmartEdge incremental Packing receipt {job_name}":
		return purpose == "Material Receipt"
	if remarks == f"SmartEdge incremental Packing physical Repack {job_name}":
		return purpose == "Repack"
	roll_prefix = "SmartEdge physical Packing roll "
	if remarks.startswith(roll_prefix):
		roll_name = remarks[len(roll_prefix):].strip()
		return purpose == "Repack" and frappe.db.get_value(
			"SmartEdge Roll", roll_name, "packing_job"
		) == job_name
	return False


def link_final_manufacturing_stock_entry(doc, final_jobs, stock_entry, finished_qty):
	stock_entry_name = stock_entry.name if hasattr(stock_entry, "name") else stock_entry
	doc.db_set("final_manufacturing_stock_entry", stock_entry_name, update_modified=False)
	if doc.meta.has_field("final_stock_posted_qty"):
		doc.db_set("final_stock_posted_qty", finished_qty, update_modified=False)
	# The stock link is the authoritative completion event.  Persist the
	# generator status only for this exact linked document, after the submitted
	# final Stock Entry has been validated and recorded.
	if get_generator_lifecycle_status(doc) != "Cancelled" and doc.meta.has_field("production_status"):
		doc.db_set("production_status", "Completed", update_modified=False)
		if doc.meta.has_field("status"):
			doc.db_set("status", "Completed", update_modified=False)
		from smart_edge_custom.manufacturing.material_production import refresh_material_readiness
		refresh_material_readiness(doc.name)
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	job_updates = {}
	if job_meta.has_field("manufacturing_stock_entry"):
		job_updates["manufacturing_stock_entry"] = stock_entry_name
	if job_meta.has_field("stock_posted_qty"):
		job_updates["stock_posted_qty"] = None
	if job_updates:
		for job in final_jobs:
			updates = dict(job_updates)
			if "stock_posted_qty" in updates:
				updates["stock_posted_qty"] = flt(job.stock_posting_qty)
			frappe.db.set_value("SmartEdge Production Job", job.name, updates, update_modified=False)
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry
	from smart_edge_custom.manufacturing.production_lot import reconcile_final_production

	capture_stock_entry(stock_entry_name)
	reconcile_final_production(doc.name, stock_entry_name)


def show_existing_stock_entry_message(stock_entry):
	frappe.msgprint(
		_("Manufacturing Stock Entry already exists: {0}. No duplicate entry was created.").format(
			frappe.get_desk_link("Stock Entry", stock_entry)
		),
		alert=True,
		indicator="blue",
	)


def lock_final_eb_bom_generator(production):
	frappe.db.sql("select name from `tabFinal EB BOM Generator` where name=%s for update", (production,))


def validate_stock_warehouse(warehouse, company, label):
	if not warehouse:
		frappe.throw(_("{0} is required before creating final manufacturing Stock Entry.").format(label))
	fields = ["name", "company", "is_group"]
	if frappe.get_meta("Warehouse", cached=True).has_field("disabled"):
		fields.append("disabled")
	row = frappe.db.get_value("Warehouse", warehouse, fields, as_dict=True)
	if not row:
		frappe.throw(_("{0} {1} does not exist.").format(label, warehouse))
	if row.get("is_group"):
		frappe.throw(_("{0} {1} cannot be a group warehouse.").format(label, warehouse))
	if row.get("disabled"):
		frappe.throw(_("{0} {1} is disabled.").format(label, warehouse))
	if company and row.get("company") and row.company != company:
		frappe.throw(_("{0} {1} does not belong to Company {2}.").format(label, warehouse, company))
	return warehouse


def get_finished_goods_warehouse(doc):
	warehouse = frappe.db.get_value(
		"Item Default",
		{"parent": doc.finished_item, "company": doc.company},
		"default_warehouse",
	)
	if not warehouse:
		warehouse = frappe.db.get_value("Item Default", {"parent": doc.finished_item}, "default_warehouse")
	return warehouse or doc.get("source_warehouse")


def get_final_finished_quantity(production):
	return sum(flt(job.stock_posting_qty) for job in get_final_finished_jobs(production))


def validate_final_completion_stock_entry_setup(job, completion_type):
	if completion_type != "Full Completion" or not job.final_eb_bom_generator or not job.plan_entry:
		return
	doc = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
	parent_stock_entry = get_active_stock_entry_reference(
		doc.get("final_manufacturing_stock_entry"),
		_("Final EB BOM Generator {0}").format(doc.name),
	)
	if parent_stock_entry:
		return
	job_stock_entry = get_active_stock_entry_reference(
		job.get("manufacturing_stock_entry"),
		_("Production Job {0}").format(job.name),
	)
	if job_stock_entry:
		return

	mandatory_rows = get_mandatory_route_operation_rows(doc.name)
	if not mandatory_rows or mandatory_rows[-1].name != frappe.db.get_value(
		"SmartEdge Production Plan Entry", job.plan_entry, "operation_row"
	):
		return
	for operation_row in mandatory_rows[:-1]:
		entry = get_route_plan_entry(doc.name, operation_row.name)
		if not entry or entry.status != "Completed":
			return
		completed_job = get_completed_job_for_plan_entry(entry)
		if not completed_job or completed_job.status != "Completed":
			return

	source_warehouse = validate_stock_warehouse(doc.get("source_warehouse"), doc.company, _("Source Warehouse"))
	target_warehouse = validate_stock_warehouse(
		get_finished_goods_warehouse(doc), doc.company, _("Finished Goods Warehouse")
	)
	finished_qty = get_final_finished_quantity(doc.name) + (flt(job.good_qty) or flt(job.actual_qty_meter))
	if finished_qty <= 0:
		frappe.throw(_("Actual accepted Finished Goods quantity is required before creating Stock Entry."))
	from smart_edge_custom.manufacturing.quality_control import validate_final_qc_eligibility

	validate_final_qc_eligibility(doc.name, finished_qty)
	physical_route = physical_operation_route_active(doc.name)
	from smart_edge_custom.manufacturing.physical_operation_stock import final_wip_components
	if physical_route:
		candidate = frappe._dict(job.as_dict())
		candidate.stock_posting_qty = flt(job.good_qty) or flt(job.actual_qty_meter)
		final_wip_components(doc, [candidate])
	else:
		components = get_final_stock_components(doc, finished_qty, source_warehouse)
		get_final_component_batch_nos(doc, components)
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovered_components
	get_recovered_components(production=doc.name)
	finished_row = get_finished_stock_row(doc, finished_qty, target_warehouse)
	validate_batch_control([finished_row])


def is_final_stock_posting_entry(entry):
	if entry.get("plan_type") == MATERIAL_PLAN_TYPE or not entry.get("operation_row"):
		return False
	mandatory_rows = get_mandatory_route_operation_rows(entry.final_eb_bom_generator)
	return bool(mandatory_rows and mandatory_rows[-1].name == entry.operation_row)


def physical_operation_route_active(production):
	"""Return true only for a locked Physical WIP parent or legacy physical evidence."""
	from smart_edge_custom.manufacturing.physical_operation_stock import production_uses_physical_stock
	if production_uses_physical_stock(production):
		return True
	return False


@frappe.whitelist()
def repair_physical_route_snapshot(plan_entry, apply=False):
	"""Safely repair one unposted Physical WIP route snapshot.

	Existing submitted plans are immutable by default.  ``apply`` is explicit so
	callers can inspect the validation result before changing an affected Ready
	plan; no Stock Entry or historical transaction is rewritten here.
	"""
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	if entry.get("plan_type") != "Final EB":
		frappe.throw(_("Only a Final EB physical route plan entry can be repaired."))
	from smart_edge_custom.manufacturing.final_bom_generator import PHYSICAL_WIP, get_stored_stock_posting_mode
	if get_stored_stock_posting_mode(entry.final_eb_bom_generator) != PHYSICAL_WIP:
		frappe.throw(_("The parent generator is not locked to Physical WIP."))
	if entry.get("status") in {"Completed", "Cancelled"}:
		frappe.throw(_("Plan Entry {0} is already {1} and cannot be amended.").format(entry.name, entry.status))
	job = frappe.db.get_value("SmartEdge Production Job", {"plan_entry": entry.name},
		["name", "status", "manufacturing_stock_entry"], as_dict=True)
	if job and job.manufacturing_stock_entry and frappe.db.get_value("Stock Entry", job.manufacturing_stock_entry, "docstatus") == 1:
		frappe.throw(_("Production Job {0} already has a submitted Stock Entry; review it instead of changing the snapshot.").format(job.name))
	generator = frappe.get_doc("Final EB BOM Generator", entry.final_eb_bom_generator)
	route_row = next((row for row in generator.get("operation_preview") or []
		if row.name == entry.operation_row or (row.operation == entry.operation and row.sequence == entry.sequence)), None)
	if not route_row or route_row.stock_movement_type not in {"Stockable WIP Output", "WIP Transfer / Processing"}:
		frappe.throw(_("The selected route does not explicitly configure {0} as a physical stock handoff.").format(entry.operation))
	previous = get_previous_mandatory_entry(entry)
	result = {"plan_entry": entry.name, "predecessor": previous.name if previous else None,
		"old_movement_type": entry.stock_movement_type, "new_movement_type": route_row.stock_movement_type,
		"old_wip_warehouse": entry.wip_warehouse, "new_wip_warehouse": route_row.wip_warehouse,
		"applied": False}
	if previous:
		previous_job = frappe.db.get_value("SmartEdge Production Job", {"plan_entry": previous.name, "status": "Completed"},
			["name", "output_production_lot", "manufacturing_stock_entry"], as_dict=True)
		if not previous_job or not previous_job.output_production_lot or not previous_job.manufacturing_stock_entry:
			frappe.throw(_("The predecessor input is not a completed, physically posted operation."))
		from smart_edge_custom.manufacturing.physical_operation_stock import physical_input_batch
		batch = physical_input_batch(previous_job.output_production_lot)
		from erpnext.stock.doctype.batch.batch import get_batch_qty
		available = sum(flt(row.get("qty")) for row in (get_batch_qty(batch_no=batch) or []))
		if available <= 0:
			frappe.throw(_("The predecessor Batch {0} has no available stock.").format(batch))
		result.update({"predecessor_batch": batch, "available_qty": available})
	if apply:
		entry.planning_category = route_row.planning_category or entry.planning_category or "Other"
		entry.stock_movement_type = route_row.stock_movement_type
		entry.wip_warehouse = entry.wip_warehouse or route_row.wip_warehouse
		if not entry.wip_warehouse:
			entry.wip_warehouse = _route_wip_warehouse(
				generator, frappe._dict(wip_warehouse=None), entry.stock_movement_type
			)
		if not entry.wip_warehouse:
			frappe.throw(_("A WIP Warehouse is required before repairing the physical route snapshot."))
		validate_stock_warehouse(entry.wip_warehouse, generator.company, _("Physical WIP Warehouse"))
		entry.save(ignore_permissions=True)
		result["applied"] = True
		result["wip_warehouse"] = entry.wip_warehouse
	return result


@frappe.whitelist()
def repair_physical_qc_route_snapshot(plan_entry, apply=False):
	"""Backward-compatible wrapper for the original QC repair endpoint."""
	entry = frappe.db.get_value("SmartEdge Production Plan Entry", plan_entry, ["operation"], as_dict=True)
	if not entry or "qc" not in str(entry.operation or "").lower():
		frappe.throw(_("Only a Final EB QC plan entry can be repaired."))
	return repair_physical_route_snapshot(plan_entry, apply=apply)


def is_first_fg_operation_entry(entry):
	"""Return true for the first mandatory FG operation in the actual BOM route.

	The batch consumption preview is useful before the final stock posting as
	well, but it must only be exposed for a real FG operation (never a material
	production plan or an arbitrary/non-route entry).
	"""
	if entry.get("plan_type") == MATERIAL_PLAN_TYPE or not entry.get("operation_row"):
		return False
	mandatory_rows = get_mandatory_route_operation_rows(entry.final_eb_bom_generator)
	return bool(mandatory_rows and mandatory_rows[0].name == entry.operation_row)


def get_candidate_final_finished_quantity(entry, actual_qty_meter=None, good_qty=None):
	completed_qty = sum(
		flt(row.good_qty) or flt(row.actual_qty_meter)
		for row in frappe.get_all(
			"SmartEdge Production Job",
			filters={"plan_entry": entry.name, "status": "Completed"},
			fields=["good_qty", "actual_qty_meter"],
			limit_page_length=100,
		)
	)
	return completed_qty + (flt(good_qty) or flt(actual_qty_meter))


@frappe.whitelist()
def get_final_batch_consumption_preview(
	plan_entry, actual_qty_meter=None, good_qty=None
):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("view material allocations for"))
	entry.check_permission("read")
	is_final = is_final_stock_posting_entry(entry)
	is_first = is_first_fg_operation_entry(entry)
	if not (is_final or is_first):
		return {"applicable": 0, "components": []}
	doc = frappe.get_doc("Final EB BOM Generator", entry.final_eb_bom_generator)
	doc.check_permission("read")
	# A final manufacturing Stock Entry is the authoritative consumption event.
	# Follow-up executions of the final operation (for example, a remaining
	# Packing plan) must not recalculate the original Compound/Base Colour FIFO
	# requirement after that event has already been posted.
	if is_final:
		existing_stock_entry = get_active_stock_entry_reference(
			doc.get("final_manufacturing_stock_entry"),
			_("Final EB BOM Generator {0}").format(doc.name),
		)
		if existing_stock_entry:
			return {
				"applicable": 1,
				"components": [],
				"message": _(
					"Compound and Base Colour were already consumed by final manufacturing Stock Entry {0}."
				).format(existing_stock_entry),
			}
	if is_final and physical_operation_route_active(entry.final_eb_bom_generator):
		return {"applicable": 1, "components": [], "message": _("Final operation will consume the preceding physical WIP Batch; Compound and Base Colour were posted at the first operation.")}

	if is_final:
		finished_qty = get_candidate_final_finished_quantity(
			entry, actual_qty_meter=actual_qty_meter, good_qty=good_qty
		)
	else:
		# The first-operation dialog previews the quantity being completed now.
		# Do not include completed jobs from later/final operations in its
		# proposed material allocation.
		finished_qty = flt(good_qty) or flt(actual_qty_meter)
	if finished_qty <= 0:
		frappe.throw(_("Actual accepted Finished Goods quantity is required to preview batch consumption."))
	preview = build_final_batch_consumption_preview(doc, finished_qty)
	preview["applicable"] = 1
	return preview


def get_final_finished_jobs(production):
	mandatory_rows = get_mandatory_route_operation_rows(production)
	if not mandatory_rows:
		frappe.throw(_("No mandatory operations found for Final EB BOM Generator {0}.").format(production))
	final_entries = get_route_plan_entries(production, mandatory_rows[-1].name)
	if not final_entries:
		frappe.throw(_("No completed Plan Entry found for final mandatory operation {0}.").format(mandatory_rows[-1].operation))
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	fields = ["name", "good_qty", "actual_qty_meter", "plan_entry", "input_production_lot"]
	for fieldname in ("manufacturing_stock_entry", "stock_posted_qty"):
		if job_meta.has_field(fieldname):
			fields.append(fieldname)
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": ["in", [entry.name for entry in final_entries]], "status": "Completed"},
		fields=fields,
		limit_page_length=100,
	)
	for job in jobs:
		job.stock_posting_qty = flt(job.good_qty) or flt(job.actual_qty_meter)
	return [job for job in jobs if flt(job.stock_posting_qty) > 0]


def get_final_mandatory_plan_entry(production):
	mandatory_rows = get_mandatory_route_operation_rows(production)
	if not mandatory_rows:
		frappe.throw(_("No mandatory operations found for Final EB BOM Generator {0}.").format(production))
	final_row = mandatory_rows[-1]
	entry = get_route_plan_entry(production, final_row.name)
	if not entry:
		frappe.throw(_("No completed Plan Entry found for final mandatory operation {0}.").format(final_row.operation))
	return entry


def get_final_stock_components(doc, finished_qty, source_warehouse):
	components = []
	precision = frappe.get_precision("SmartEdge Production Job", "good_qty")
	planned_output = flt(doc.get("production_quantity"), precision)
	full_output = planned_output > 0 and flt(finished_qty, precision) == planned_output
	for label, item_code, consumption, planned_total in (
		(_("Compound"), doc.get("compound_item"), get_final_component_consumption_per_meter(doc, "compound"), doc.get("compound_total_required")),
		(_("Base Colour / Shade"), doc.get("base_colour_item"), get_final_component_consumption_per_meter(doc, "shade"), doc.get("shade_total_required")),
	):
		if not item_code:
			frappe.throw(_("{0} stock item is required before creating final manufacturing Stock Entry.").format(label))
		if flt(consumption) <= 0:
			frappe.throw(_("{0} consumption per meter must be greater than zero.").format(label))
		item = validate_stock_item(item_code)
		qty = flt(consumption) * flt(finished_qty)
		if full_output and flt(planned_total) > 0:
			# Planned output has finer precision than the accepted completion quantity.
			# Equal output at completion precision consumes the recorded planned total.
			qty = flt(planned_total)
		if qty <= 0:
			frappe.throw(_("{0} consumption quantity must be greater than zero.").format(label))
		components.append(
			{
				"item_code": item_code,
				"s_warehouse": source_warehouse,
				"qty": qty,
				"uom": "Kg",
				"stock_uom": item.stock_uom,
				"conversion_factor": get_required_conversion_factor(item_code, "Kg", item.stock_uom),
			}
		)
	return components


def get_final_component_batch_nos(doc, components):
	batch_nos_by_row = []
	for index, (
		label,
		table_field,
		batch_count_field,
		total_field,
		item_field,
	) in enumerate(
		(
			(
				_("Compound"),
				"compound_batch_allocations",
				"compound_consumption_batches",
				"compound_total_required",
				"compound_item",
			),
			(
				_("Base Colour / Shade"),
				"shade_batch_allocations",
				"shade_consumption_batches",
				"shade_total_required",
				"base_colour_item",
			),
		)
	):
		component = components[index]
		item = validate_stock_item(component["item_code"])
		if not item.has_batch_no:
			batch_nos_by_row.append({})
			continue

		expected_count = cint(doc.get(batch_count_field))
		allocations = get_current_fifo_allocations(
			doc,
			component,
			label,
			table_field,
			expected_count,
			total_field,
			item_field,
			item.stock_uom,
		)
		batch_nos_by_row.append(
			get_revalidated_consumption_batches(component, allocations, label, item.stock_uom)
		)

	return batch_nos_by_row


def get_current_fifo_allocations(
	doc,
	component,
	label,
	table_field,
	expected_count,
	total_field,
	item_field,
	stock_uom,
):
	"""Reuse a valid saved allocation, otherwise recalculate it with the existing FIFO selector."""
	if expected_count <= 0:
		frappe.throw(_("{0} batch count is required before posting stock.").format(label))

	allocations = list(doc.get(table_field) or [])
	if is_saved_fifo_allocation_current(
		component, allocations, expected_count, flt(doc.get(total_field)), stock_uom
	):
		return allocations

	batch_weight = get_prepared_component_batch_weight(
		doc.get(item_field), label
	)
	return [
		frappe._dict(row)
		for row in get_fifo_batch_allocations(
			component["item_code"],
			component["s_warehouse"],
			expected_count,
			batch_weight,
			label,
		)
	]


def is_saved_fifo_allocation_current(
	component, allocations, expected_count, planned_qty, stock_uom
):
	if len(allocations) != expected_count:
		return False
	batch_nos = [row.get("batch_no") for row in allocations if row.get("batch_no")]
	if len(batch_nos) != expected_count or len(set(batch_nos)) != expected_count:
		return False
	if abs(sum(flt(row.get("selected_qty")) for row in allocations) - planned_qty) > 0.000001:
		return False
	if any(
		row.get("item_code") != component["item_code"]
		or row.get("warehouse") != component["s_warehouse"]
		for row in allocations
	):
		return False

	from erpnext.stock.doctype.batch.batch import get_batch_qty

	conversion_factor = get_kg_to_stock_factor(component["item_code"], stock_uom)
	available = {
		row.batch_no: flt(row.qty)
		for row in get_batch_qty(
			item_code=component["item_code"], warehouse=component["s_warehouse"]
		)
		or []
	}
	return all(
		available.get(row.get("batch_no"), 0)
		+ 0.000001
		>= flt(row.get("selected_qty")) * conversion_factor
		for row in allocations
	)


def build_final_batch_consumption_preview(doc, finished_qty):
	source_warehouse = validate_stock_warehouse(
		doc.get("source_warehouse"), doc.company, _("Source Warehouse")
	)
	components = get_final_stock_components(doc, finished_qty, source_warehouse)
	batch_nos_by_row = get_final_component_batch_nos(doc, components)
	result = []
	labels = (_("Compound"), _("Base Colour / Shade"))
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	for component, batch_nos, label in zip(components, batch_nos_by_row, labels):
		item = validate_stock_item(component["item_code"])
		conversion_factor = get_kg_to_stock_factor(component["item_code"], item.stock_uom)
		available = {
			row.batch_no: flt(row.qty)
			for row in get_batch_qty(
				item_code=component["item_code"], warehouse=component["s_warehouse"]
			)
			or []
		}
		result.append(
			{
				"component_type": label,
				"item_code": component["item_code"],
				"item_name": frappe.db.get_value("Item", component["item_code"], "item_name")
				or component["item_code"],
				"uom": item.stock_uom,
				"batches": [
					{
						"batch_no": batch_no,
						"warehouse": component["s_warehouse"],
						"available_qty": available.get(batch_no, 0),
						"quantity_to_consume": flt(qty),
						"uom": item.stock_uom,
					}
					for batch_no, qty in batch_nos.items()
				],
				"total_to_consume": flt(component["qty"]) * conversion_factor,
			}
		)
	return {"finished_qty": flt(finished_qty), "components": result}


def get_revalidated_consumption_batches(component, allocations, label, stock_uom):
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	item_code = component["item_code"]
	warehouse = component["s_warehouse"]
	conversion_factor = get_kg_to_stock_factor(item_code, stock_uom)
	available_rows = get_batch_qty(item_code=item_code, warehouse=warehouse) or []
	available_by_batch = {row.batch_no: flt(row.qty) for row in available_rows}

	for allocation in allocations:
		if allocation.item_code != item_code or allocation.warehouse != warehouse:
			frappe.throw(_("{0} FIFO allocation Item or Warehouse has changed. Refresh the allocation.").format(label))
		selected_stock_qty = flt(allocation.selected_qty) * conversion_factor
		if available_by_batch.get(allocation.batch_no, 0) + 0.000001 < selected_stock_qty:
			frappe.throw(
				_("Selected Batch {0} no longer has sufficient stock for {1}. Refresh the FIFO allocation.").format(
					frappe.bold(allocation.batch_no), label
				)
			)

	remaining_kg = flt(component["qty"])
	batches = {}
	for allocation in allocations:
		if remaining_kg <= 0.000001:
			break
		consume_kg = min(flt(allocation.selected_qty), remaining_kg)
		batches[allocation.batch_no] = consume_kg * conversion_factor
		remaining_kg -= consume_kg

	if remaining_kg > 0.000001:
		frappe.throw(
			_("{0} FIFO allocation is insufficient: required {1} Kg, allocated {2} Kg, shortage {3} Kg. Refresh the allocation.").format(
				label, flt(component["qty"], 9), flt(component["qty"] - remaining_kg, 9), flt(remaining_kg, 9)
			)
		)
	return batches


def set_final_component_batch_bundles(stock_entry, batch_nos_by_row):
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	for index, batch_nos in enumerate(batch_nos_by_row):
		if not batch_nos:
			continue
		row = stock_entry.items[index]
		bundle = SerialBatchCreation(
			{
				"item_code": row.item_code,
				"warehouse": row.s_warehouse,
				"posting_date": stock_entry.posting_date,
				"posting_time": stock_entry.posting_time,
				"voucher_type": stock_entry.doctype,
				"voucher_no": stock_entry.name,
				"voucher_detail_no": row.name,
				"qty": flt(row.transfer_qty) * -1,
				"type_of_transaction": "Outward",
				"company": stock_entry.company,
				"do_not_submit": True,
			}
		).make_serial_and_batch_bundle(batch_nos=batch_nos)
		row.serial_and_batch_bundle = bundle.name
		row.use_serial_batch_fields = 0


def get_final_component_consumption_per_meter(doc, component):
	total_fieldname = f"{component}_total_required"
	per_meter_fieldname = (
		"compound_consumption_per_meter" if component == "compound" else "shade_consumption_per_meter"
	)
	total_quantity = flt(doc.get(total_fieldname))
	production_quantity = flt(doc.get("production_quantity"))
	if total_quantity > 0 and production_quantity > 0:
		return total_quantity / production_quantity
	return flt(doc.get(per_meter_fieldname))


def get_finished_stock_row(doc, finished_qty, target_warehouse):
	if not doc.finished_item:
		frappe.throw(_("Finished EB Item is required before creating final manufacturing Stock Entry."))
	item = validate_stock_item(doc.finished_item)
	if not item.has_batch_no:
		frappe.throw(_("Finished Goods Item {0} must be batch-controlled before final manufacturing stock posting.").format(doc.finished_item))
	production_uom = "Meter"
	return {
		"item_code": doc.finished_item,
		"t_warehouse": target_warehouse,
		"qty": finished_qty,
		"uom": production_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": get_required_conversion_factor(doc.finished_item, production_uom, item.stock_uom),
		"is_finished_item": 1,
	}


def validate_stock_item(item_code):
	item = frappe.db.get_value("Item", item_code, ["name", "stock_uom", "is_stock_item", "disabled", "has_batch_no"], as_dict=True)
	if not item:
		frappe.throw(_("Item {0} does not exist.").format(item_code))
	if not item.is_stock_item:
		frappe.throw(_("Item {0} must be a stock item.").format(item_code))
	if item.disabled:
		frappe.throw(_("Item {0} is disabled.").format(item_code))
	return item


def get_required_conversion_factor(item_code, uom, stock_uom):
	if uom == stock_uom:
		return 1
	if not frappe.db.exists("UOM", uom):
		frappe.throw(_("UOM {0} does not exist.").format(uom))
	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail",
		{"parent": item_code, "uom": uom},
		"conversion_factor",
	)
	if flt(conversion_factor) <= 0:
		frappe.throw(
			_(
				"Cannot create final manufacturing Stock Entry for Item {0}: UOM {1} must convert to stock UOM {2}. "
				"Add an Item UOM Conversion row for UOM {1} on Item {0}, or correct the Item Stock UOM before posting stock. "
				"No Stock Entry was created."
			).format(item_code, uom, stock_uom)
		)
	return flt(conversion_factor)


def validate_batch_control(rows):
	for row in rows:
		has_batch_no = frappe.db.get_value("Item", row.get("item_code"), "has_batch_no")
		if has_batch_no and not row.get("batch_no"):
			if (
				row.get("is_finished_item") and row.get("t_warehouse") and not row.get("s_warehouse")
				and frappe.db.get_value("Item", row.get("item_code"), "create_new_batch")
			):
				# ERPNext creates the inward batch and bundle during stock posting.
				continue
			frappe.throw(
				_("Batch-controlled Item {0} requires explicit batch allocation before automatic Stock Entry posting.").format(
					row.get("item_code")
				)
			)


@frappe.whitelist()
def plan_full_production(production, start_date, start_time="09:00"):
	from smart_edge_custom.manufacturing.operator_access import require_planning_supervisor
	require_planning_supervisor(_("plan a complete production route"))
	frappe.has_permission("SmartEdge Production Plan Entry", "write", throw=True)
	current_start = combine_date_time(start_date, start_time)
	planned = []
	entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"final_eb_bom_generator": production, "status": ["!=", "Cancelled"]},
		fields=["name", "operation", "operation_row", "sequence"],
		order_by="sequence asc",
	)
	for entry in entries:
		workstations = get_compatible_workstations(entry.operation, entry.operation_row)
		if not workstations:
			frappe.throw(_("No compatible Workstation found for Operation {0}.").format(entry.operation))
		duration_hours = get_entry_default_duration(frappe._dict(entry))
		schedule_operation(entry.name, getdate(current_start), workstations[0], current_start.strftime("%H:%M"), duration_hours)
		planned.append(entry.name)
		current_start = current_start + timedelta(hours=duration_hours)
	return {"planned_entries": planned}


@frappe.whitelist()
def get_workstation_capacity(workstation, planned_date):
	frappe.has_permission("Workstation", "read", throw=True)
	planned_date = getdate(planned_date)
	return get_workstation_capacity_payload(workstation, planned_date)


def get_workstation_capacity_payload(workstation, planned_date, capacity=None, planned=None):
	if not workstation or workstation == _("Unassigned") or not planned_date:
		return {
			"capacity_hours": 0,
			"planned_hours": 0,
			"remaining_hours": 0,
			"overallocated_hours": 0,
			"utilization": 0,
			"configured": 0,
		}
	planned_date = getdate(planned_date)
	availability = get_capacity_availability(workstation, planned_date)
	capacity = availability.get("net_hours", 0)
	planned_hours = availability.get("reserved_hours", 0)
	return {
		"workstation": workstation,
		"date": str(planned_date),
		"capacity_hours": capacity,
		"scheduled_hours": availability.get("scheduled_hours", 0),
		"downtime_hours": availability.get("downtime_hours", 0),
		"planned_hours": planned_hours,
		"remaining_hours": availability.get("remaining_hours", 0),
		"overallocated_hours": availability.get("overallocated_hours", 0),
		"utilization": (planned_hours / capacity * 100) if capacity else 0,
		"configured": availability.get("configured", 0),
		"message": availability.get("message"),
	}


@frappe.whitelist()
def get_start_job_context(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("view"))
	entry.check_permission("read")
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		entry.final_eb_bom_generator,
		["company", "finished_item", "finished_item_name"],
		as_dict=True,
	)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
		get_current_employee,
		get_current_shift,
	)

	company = production.company if production else None
	qc_entry = is_qc_entry(entry)
	operator_employee = (entry.get("planned_employee") or get_current_employee(company)) if qc_entry else get_authenticated_employee(company)
	if operator_employee and not can_read_employee(operator_employee):
		operator_employee = None
	pending_quantity = get_remaining_or_planned_quantity(entry)
	context = {
		"production_definition": entry.final_eb_bom_generator,
		"finished_item": (production.finished_item_name or production.finished_item) if production else None,
		"operation": entry.operation,
		"workstation": entry.workstation,
		"compatible_workstations": get_compatible_workstations(entry.operation, entry.operation_row),
		"company": company,
		"planner_user": frappe.session.user,
		"planner_name": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
		"operator_employee": operator_employee,
		"operator_name": frappe.db.get_value("Employee", operator_employee, "employee_name") if operator_employee else None,
		"shift": entry.get("planned_shift") or (get_current_shift(operator_employee) if operator_employee else None),
		"planned_qty_meter": pending_quantity.get("meter"),
		"planned_qty_kg": pending_quantity.get("kg"),
		"planned_start": entry.planned_start_datetime,
		"can_edit_workstation": entry.has_permission("write"),
	}
	if entry.get("plan_type") == "Required Material":
		if entry.workstation and entry.workstation not in context["compatible_workstations"]:
			context["compatible_workstations"].insert(0, entry.workstation)
		context.update(
			{
				"is_material_production": 1,
				"production_item": entry.material_item,
				"finished_item": frappe.db.get_value("Item", entry.material_item, "item_name") or entry.material_item,
				"material_type": entry.material_type,
				"work_order": entry.work_order,
				"bom_no": entry.bom_no,
				"planned_batches": cint(entry.planned_batches),
				"weight_per_batch": flt(entry.weight_per_batch),
				"total_planned_quantity": flt(entry.get("planned_kg") or entry.production_quantity),
				"quantity_uom": entry.quantity_uom,
				"source_warehouse": entry.source_warehouse,
				"wip_warehouse": entry.wip_warehouse,
				"target_warehouse": entry.target_warehouse,
			}
		)
	return context


@frappe.whitelist()
def get_complete_job_context(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("view completion details for"))
	job = get_open_job(plan_entry)
	job.check_permission("read")
	now_value = now_datetime()
	total_downtime_seconds = 0
	for row in job.get("downtime_events") or []:
		if row.start_time and row.end_time:
			total_downtime_seconds += max((get_datetime(row.end_time) - get_datetime(row.start_time)).total_seconds(), 0)
		elif row.start_time:
			total_downtime_seconds += max((now_value - get_datetime(row.start_time)).total_seconds(), 0)
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		job.final_eb_bom_generator,
		[
			"finished_item",
			"finished_item_name",
			"stock_uom",
			"production_quantity",
			"total_eb_weight_kg",
			"weight_per_meter_kg",
		],
		as_dict=True,
	)
	context = {
		"production_job": job.name,
		"operation": job.operation,
		"finished_item": (production.finished_item_name or production.finished_item) if production else None,
		"workstation": job.workstation,
		"operator_employee": job.operator_employee,
		"operator_name": get_employee_display_name(job.operator_employee, job.operator_name),
		# Older jobs may not have copied the plan quantity onto the job. Keep the
		# dialog's planned and suggested actual quantities aligned with the plan
		# entry/FEBG in that case.
		"planned_qty_meter": flt(job.planned_qty_meter)
			or flt(entry.get("planned_meter"))
			or flt(production.production_quantity if production else 0),
		"planned_qty_kg": job.planned_qty_kg,
		"stock_uom": production.stock_uom if production else None,
		"productive_elapsed_seconds": get_productive_elapsed_seconds(job),
		"total_downtime_seconds": int(total_downtime_seconds),
		"actual_qty_meter": job.actual_qty_meter,
		"actual_qty_kg": job.actual_qty_kg,
		"good_qty": job.good_qty,
		"rejected_qty": job.rejected_qty,
		"rework_qty": job.rework_qty,
		"usable_scrap": job.usable_scrap,
		"unusable_scrap": job.unusable_scrap,
		"remarks": job.remarks,
		"status": job.status,
	}
	from smart_edge_custom.manufacturing.scrap_recovery import get_recovery_uom, get_unusable_scrap_uom

	context["scrap_uom"] = get_recovery_uom(production.finished_item if production else None)
	context["unusable_scrap_uom"] = get_unusable_scrap_uom(production.finished_item if production else None)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry
	previous_entry = get_previous_mandatory_entry(entry)
	is_packing_operation = (
		str(entry.get("planning_category") or "").lower() == "packing"
		or "pack" in str(entry.get("operation") or "").lower()
	)
	context["qc_release_applicable"] = bool(is_packing_operation and previous_entry and is_qc_entry(previous_entry))
	context["released_qty_meter"] = (
		flt(entry.get("released_qty_meter")) if context["qc_release_applicable"] else None
	)
	context["released_qty_kg"] = (
		flt(entry.get("released_qty_kg")) if context["qc_release_applicable"] else None
	)
	completion_quantity_context = get_completion_quantity_context(entry, job, production=production)
	context.update(completion_quantity_context)
	context["packing_qr_required"] = False
	context["is_extrusion_operation"] = is_extrusion_operation(entry)
	context["existing_roll_packing_details"] = [
		{"roll_no": cint(row.roll_no), "meter_quantity": flt(row.meter_quantity)}
		for row in (job.get("roll_packing_details") or [])
	]
	if entry.get("plan_type") == "Required Material":
		context.update(
			{
				"is_material_production": 1,
				"material_type": entry.material_type,
				"production_item": entry.material_item,
				"finished_item": frappe.db.get_value("Item", entry.material_item, "item_name") or entry.material_item,
				"work_order": entry.work_order,
				"bom_no": entry.bom_no,
				"planned_batches": flt(job.planned_qty_meter),
				"weight_per_batch": flt(entry.weight_per_batch),
				"quantity_uom": entry.quantity_uom,
				"total_planned_quantity": flt(job.planned_qty_kg),
			}
		)
		context["scrap_uom"] = get_recovery_uom(entry.material_item)
		context["unusable_scrap_uom"] = get_unusable_scrap_uom(entry.material_item)
	elif is_first_fg_operation_entry(entry) or is_final_stock_posting_entry(entry):
		preview_qty = flt(job.good_qty) or flt(job.actual_qty_meter) or flt(job.planned_qty_meter)
		context["batch_consumption_preview"] = get_final_batch_consumption_preview(
			entry.name,
			actual_qty_meter=preview_qty,
			good_qty=preview_qty,
		)
		# Keep the calculation available for stock validation, but hide the
		# optional Planning Board summary unless explicitly re-enabled.
		context["show_batch_consumption"] = cint(frappe.conf.get("enable_batch_consumption_summary", 0)) == 1
	return context


def is_extrusion_operation(entry):
	"""Use the saved operation configuration to identify extrusion output."""
	if str(entry.get("planning_category") or "").strip().lower() == "extrusion":
		return True
	operation_row = entry.get("operation_row")
	if operation_row:
		operation_config = frappe.db.get_value(
			"Final EB BOM Operation",
			operation_row,
			["is_extrusion", "planning_category"],
			as_dict=True,
		)
		if operation_config and (
			cint(operation_config.is_extrusion)
			or str(operation_config.planning_category or "").strip().lower() == "extrusion"
		):
			return True
	operation_name = entry.get("operation")
	if operation_name:
		return str(get_operation_planning_category(operation_name) or "").strip().lower() == "extrusion"
	return False


def validate_roll_packing_details(entry, job, roll_count, roll_packing_details, actual_qty_meter, enforce_accounting=False):
	"""Validate the current completion's roll rows without changing job state."""
	is_extrusion = is_extrusion_operation(entry)
	if not is_extrusion:
		if roll_count not in (None, "", 0, "0") or roll_packing_details not in (None, "", [], "[]"):
			frappe.throw(_("Roll Packing Details are only available for extrusion operations."))
		return []
	if isinstance(roll_packing_details, str):
		try:
			roll_packing_details = json.loads(roll_packing_details)
		except (TypeError, ValueError, json.JSONDecodeError):
			frappe.throw(_("Roll Packing Details must be a valid list."))
	if not isinstance(roll_packing_details, list) or not roll_packing_details:
		frappe.throw(_("Enter at least one roll quantity before completing extrusion."))
	try:
		roll_count_text = str(roll_count).strip()
		if not re.fullmatch(r"[1-9][0-9]*", roll_count_text):
			raise ValueError
		roll_count_value = int(roll_count_text)
	except (TypeError, ValueError, OverflowError):
		frappe.throw(_("Number of Rolls must be a positive whole number."))
	if len(roll_packing_details) != roll_count_value:
		frappe.throw(_("Number of Rolls must match the number of roll quantities."))

	rows = []
	seen_roll_numbers = set()
	total = 0
	for row in roll_packing_details:
		if not isinstance(row, dict) or set(row) - {"roll_no", "meter_quantity", "destination_warehouse"}:
			frappe.throw(_("Each roll entry must contain only Roll No, Meter Quantity and Destination Warehouse."))
		try:
			roll_no_text = str(row.get("roll_no")).strip()
			meter_text = str(row.get("meter_quantity")).strip()
			if not re.fullmatch(r"[1-9][0-9]*", roll_no_text) or not re.fullmatch(r"[1-9][0-9]*", meter_text):
				raise ValueError
			roll_no = int(roll_no_text)
			meter_quantity = int(meter_text)
		except (TypeError, ValueError, OverflowError):
			frappe.throw(_("Each roll must have a positive whole-meter quantity."))
		if roll_no in seen_roll_numbers or roll_no < 1 or roll_no > roll_count_value:
			frappe.throw(_("Roll numbers must be unique and sequential from 1 to {0}.").format(roll_count_value))
		seen_roll_numbers.add(roll_no)
		rows.append({"roll_no": roll_no, "meter_quantity": meter_quantity, "destination_warehouse": row.get("destination_warehouse")})
		total += meter_quantity
	if seen_roll_numbers != set(range(1, roll_count_value + 1)):
		frappe.throw(_("Roll numbers must be sequential from 1 to {0}.").format(roll_count_value))
	if not math.isfinite(float(actual_qty_meter)) or total > int(actual_qty_meter):
		frappe.throw(
			_("Total roll quantity ({0} m) cannot exceed actual completed production quantity ({1} m).").format(
				f"{total:,}", f"{int(actual_qty_meter):,}"
			)
		)
	# The roll masters represent whole physical rolls.  Any balance not assigned
	# to a roll must already be represented by the existing validated output/loss
	# fields on the Production Job; it must never become an implicit scrap roll.
	actual_decimal = Decimal(str(actual_qty_meter))
	good_output = job.get("good_qty")
	validated_loss = max(actual_decimal - Decimal(str(good_output)), Decimal("0")) if good_output not in (None, "") else Decimal("0")
	remaining = actual_decimal - Decimal(total) - validated_loss
	if enforce_accounting and abs(remaining) > Decimal("0.000001"):
		frappe.throw(
			_("Roll quantities leave {0} m unaccounted for. Allocate the meters to rolls or record them through the approved loss/scrap workflow.").format(
				f"{remaining:g}"
			)
		)
	return rows


def get_completion_quantity_context(entry, job, production=None):
	"""Return the item/UOM context used by the Complete Process quantity editor.

	The Production Job continues to store its established output-unit quantities.
	This context only describes the kilogram calculation and the configured scrap
	items; it does not change any persisted values.
	"""
	production_name = get_completion_production_name(entry, job)
	production = production or frappe.db.get_value(
		"Final EB BOM Generator",
		production_name,
		[
			"finished_item",
			"stock_uom",
			"production_quantity",
			"total_eb_weight_kg",
			"weight_per_meter_kg",
		],
		as_dict=True,
	)
	is_material = entry.get("plan_type") == MATERIAL_PLAN_TYPE
	output_item = entry.get("material_item") if is_material else (production.finished_item if production else None)
	output_uom = entry.get("quantity_uom") if is_material else "Meter"
	output_uom = output_uom or (production.stock_uom if production else None)
	output_kg_factor = get_completion_output_kg_factor(entry, job, output_item, output_uom, production)
	# Meter is a valid production-reporting UOM for final EB scrap even when the
	# configured scrap Item has no Item UOM row for Meter.  The authoritative
	# factor is stored on this exact linked FEBG; do not borrow it from another
	# product or production.
	meter_to_kg_factor = None if is_material else flt(production.get("weight_per_meter_kg")) if production else 0
	config = frappe.db.get_value(
		"SmartEdge Recovery Material",
		{"source_item": output_item, "disabled": 0},
		["usable_scrap_item", "unusable_scrap_item"],
		as_dict=True,
	) if output_item and frappe.db.exists("DocType", "SmartEdge Recovery Material") else None

	result = {
		"completion_output_uom": output_uom,
		"completion_output_item": output_item,
		"completion_output_kg_factor": output_kg_factor,
		"completion_meter_to_kg_factor": meter_to_kg_factor,
		"usable_scrap_item": config.usable_scrap_item if config else None,
		"unusable_scrap_item": config.unusable_scrap_item if config else None,
		"usable_scrap_uom_options": get_completion_uom_options(
			config.usable_scrap_item if config else None, meter_to_kg_factor
		),
		"unusable_scrap_uom_options": get_completion_uom_options(
			config.unusable_scrap_item if config else None, meter_to_kg_factor
		),
		"usable_scrap_uom": frappe.db.get_value("Item", config.usable_scrap_item, "stock_uom") if config and config.usable_scrap_item else None,
		"unusable_scrap_uom": frappe.db.get_value("Item", config.unusable_scrap_item, "stock_uom") if config and config.unusable_scrap_item else None,
	}
	# These values are display-only previews. If an old job has quantities but
	# its item has no valid Kg conversion, completion will report that explicitly
	# when a quantity is submitted instead of making a silent assumption.
	for source_field, target_field, item_code in (
		("good_qty", "good_qty_kg", output_item),
		("rejected_qty", "rejected_qty_kg", output_item),
		("rework_qty", "rework_qty_kg", output_item),
	):
		value = flt(job.get(source_field))
		result[target_field] = flt(value * output_kg_factor, 6) if output_kg_factor else 0
	return result


def get_completion_output_kg_factor(entry, job, output_item, output_uom, production=None):
	if entry.get("plan_type") == MATERIAL_PLAN_TYPE:
		return get_completion_item_uom_to_kg_factor(output_item, output_uom) if output_item and output_uom else 0
	production = production or frappe.db.get_value(
		"Final EB BOM Generator",
		get_completion_production_name(entry, job),
		["production_quantity", "total_eb_weight_kg", "weight_per_meter_kg"],
		as_dict=True,
	)
	if production and flt(production.get("weight_per_meter_kg")) > 0:
		return flt(production.get("weight_per_meter_kg"))
	if flt(job.get("actual_qty_meter")) and flt(job.get("actual_qty_kg")):
		return flt(job.actual_qty_kg) / flt(job.actual_qty_meter)
	if production and flt(production.get("production_quantity")) and flt(production.get("total_eb_weight_kg")):
		return flt(production.total_eb_weight_kg) / flt(production.production_quantity)
	return 0


def get_completion_production_name(entry, job):
	"""Resolve the FEBG attached to the actual job, rejecting mismatched context."""
	entry_production = entry.get("final_eb_bom_generator")
	job_production = job.get("final_eb_bom_generator")
	if entry_production and job_production and entry_production != job_production:
		frappe.throw(
			_("Production Plan Entry and Production Job reference different Final EB BOM Generators.")
		)
	return job_production or entry_production


def is_meter_uom(uom):
	return str(uom or "").strip().lower() in {"m", "meter", "meters", "metre", "metres"}


def get_completion_uom_options(item_code, meter_to_kg_factor=None):
	if not item_code or not frappe.db.exists("Item", item_code):
		return []
	item = frappe.db.get_value("Item", item_code, ["stock_uom"], as_dict=True)
	if not item:
		return []
	options = [item.stock_uom]
	options.extend(frappe.get_all("UOM Conversion Detail", filters={"parent": item_code}, pluck="uom"))
	# Include Kg only when this Item can actually convert to it. This avoids
	# presenting a length/count UOM as a weight UOM without item evidence.
	if "Kg" not in options:
		try:
			get_completion_item_uom_to_kg_factor(item_code, "Kg")
			options.append("Kg")
		except Exception:
			pass
	if meter_to_kg_factor is not None and flt(meter_to_kg_factor) > 0 and "Meter" not in options:
		options.append("Meter")
	return list(dict.fromkeys(value for value in options if value))


def get_completion_item_uom_to_stock_factor(item_code, uom):
	item = validate_stock_item(item_code)
	if not uom or uom == item.stock_uom:
		return 1.0
	factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)
	if flt(factor) <= 0:
		factor = get_uom_conv_factor(uom, item.stock_uom)
	if flt(factor) <= 0:
		frappe.throw(
			_("Cannot convert {0} {1} to the stock UOM {2} for Item {3}. Configure an Item UOM conversion.").format(
				uom, item_code, item.stock_uom, item_code
		)
		)
	return flt(factor)


def get_completion_item_uom_to_kg_factor(item_code, uom):
	item = validate_stock_item(item_code)
	if uom == "Kg":
		if item.stock_uom == "Kg":
			return 1.0
		return 1.0 / get_completion_item_uom_to_stock_factor(item_code, "Kg")
	input_to_stock = get_completion_item_uom_to_stock_factor(item_code, uom)
	kg_to_stock = get_completion_item_uom_to_stock_factor(item_code, "Kg")
	if flt(kg_to_stock) <= 0:
		frappe.throw(
			_("Cannot convert Item {0} from {1} to Kg. Configure a valid item-specific Kg conversion.").format(
			item_code, item.stock_uom
		)
		)
	return flt(input_to_stock) / flt(kg_to_stock)


def convert_completion_quantity(item_code, quantity, uom, label, meter_to_kg_factor=None):
	quantity = parse_completion_quantity(quantity, label)
	if not quantity:
		return {"stock_quantity": 0.0, "kg_quantity": 0.0}
	if not item_code:
		frappe.throw(_("Configure a scrap Item before recording {0}.").format(label))
	item = validate_stock_item(item_code)
	if is_meter_uom(uom) and meter_to_kg_factor is not None:
		meter_to_kg_factor = flt(meter_to_kg_factor)
		if meter_to_kg_factor <= 0:
			frappe.throw(
				_("Cannot convert {0} entered in Meter because the linked Final EB BOM Generator has no valid Weight / Meter (Kg) value.").format(label)
			)
		kg_quantity = flt(quantity * meter_to_kg_factor, 6)
		if item.stock_uom == "Kg":
			stock_quantity = kg_quantity
		elif is_meter_uom(item.stock_uom):
			stock_quantity = quantity
		else:
			# The production-specific factor establishes the physical Kg amount;
			# ERPNext still requires a valid Kg -> stock-UOM conversion for posting.
			kg_to_stock = get_completion_item_uom_to_stock_factor(item_code, "Kg")
			stock_quantity = flt(kg_quantity * kg_to_stock, 6)
		return {"stock_quantity": stock_quantity, "kg_quantity": kg_quantity}
	stock_factor = get_completion_item_uom_to_stock_factor(item_code, uom)
	kg_factor = get_completion_item_uom_to_kg_factor(item_code, uom)
	return {
		"stock_quantity": flt(quantity * stock_factor, 6),
		"kg_quantity": flt(quantity * kg_factor, 6),
	}


def calculate_completion_quantity_values(
	context,
	actual_qty_kg=None,
	rejected_qty_kg=None,
	rework_qty_kg=None,
	usable_scrap=None,
	usable_scrap_uom=None,
	unusable_scrap=None,
	unusable_scrap_uom=None,
):
	"""Calculate completion quantities from an already-resolved production context."""
	output_item = context.get("completion_output_item")
	output_uom = context.get("completion_output_uom")
	actual = parse_completion_quantity(actual_qty_kg, _("Actual Completed Quantity (Kg)"))
	rejected = parse_completion_quantity(rejected_qty_kg, _("Rejected Output (Kg)"))
	rework = parse_completion_quantity(rework_qty_kg, _("Rework (Kg)"))
	usable = convert_completion_quantity(
		context.get("usable_scrap_item"),
		usable_scrap,
		usable_scrap_uom,
		_("Usable Scrap"),
		context.get("completion_meter_to_kg_factor"),
	)
	unusable = convert_completion_quantity(
		context.get("unusable_scrap_item"),
		unusable_scrap,
		unusable_scrap_uom,
		_("Unusable Scrap"),
		context.get("completion_meter_to_kg_factor"),
	)
	total = rejected + rework + usable["kg_quantity"] + unusable["kg_quantity"]
	good = flt(actual - total, 6)
	if total > actual + 0.000001:
		frappe.throw(
			_("Rejected Output, Rework, and Scrap cannot exceed Actual Completed Quantity. Good Output would be negative.")
		)
	output_factor = flt(context.get("completion_output_kg_factor"))
	if output_item and output_uom and output_factor <= 0:
		frappe.throw(
			_("Cannot establish the existing Kg conversion for output Item {0} ({1}).").format(
				output_item, output_uom
			)
		)
	good_output = flt(good / output_factor, 6) if output_factor else 0
	rejected_output = flt(rejected / output_factor, 6) if output_factor else 0
	rework_output = flt(rework / output_factor, 6) if output_factor else 0
	if is_meter_uom(output_uom):
		good_output = round_meter_quantity(good_output)
		rejected_output = round_meter_quantity(rejected_output)
		rework_output = round_meter_quantity(rework_output)
	return {
		"actual_qty_kg": actual,
		"rejected_qty_kg": rejected,
		"rework_qty_kg": rework,
		"usable_scrap_kg": usable["kg_quantity"],
		"usable_scrap_stock_qty": usable["stock_quantity"],
		"unusable_scrap_kg": unusable["kg_quantity"],
		"unusable_scrap_stock_qty": unusable["stock_quantity"],
		"good_qty_kg": good,
		"good_qty": good_output,
		"rejected_qty": rejected_output,
		"rework_qty": rework_output,
		"output_uom": output_uom,
	}


def parse_completion_quantity(value, label):
	if value in (None, ""):
		return 0.0
	try:
		value = float(value.replace(",", "") if isinstance(value, str) else value)
	except (TypeError, ValueError, OverflowError):
		frappe.throw(_("{0} must be a valid number.").format(label))
	if value != value or value in (float("inf"), float("-inf")) or value < 0:
		frappe.throw(_("{0} must be a finite number greater than or equal to zero.").format(label))
	return flt(value, 6)


@frappe.whitelist()
def calculate_completion_quantities(
	plan_entry,
	actual_qty_kg=None,
	rejected_qty_kg=None,
	rework_qty_kg=None,
	usable_scrap=None,
	usable_scrap_uom=None,
	unusable_scrap=None,
	unusable_scrap_uom=None,
):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("calculate completion quantities for"))
	entry.check_permission("read")
	job = get_open_job(plan_entry, include_completed=True)
	job.check_permission("read")
	production_name = get_completion_production_name(entry, job)
	production = frappe.db.get_value(
		"Final EB BOM Generator", production_name,
		[
			"finished_item",
			"stock_uom",
			"production_quantity",
			"total_eb_weight_kg",
			"weight_per_meter_kg",
		],
		as_dict=True,
	)
	context = get_completion_quantity_context(entry, job, production=production)
	return calculate_completion_quantity_values(
		context,
		actual_qty_kg=actual_qty_kg,
		rejected_qty_kg=rejected_qty_kg,
		rework_qty_kg=rework_qty_kg,
		usable_scrap=usable_scrap,
		usable_scrap_uom=usable_scrap_uom,
		unusable_scrap=unusable_scrap,
		unusable_scrap_uom=unusable_scrap_uom,
	)


def get_remaining_or_planned_quantity(entry):
	if flt(entry.get("released_qty_meter")) or flt(entry.get("released_qty_kg")):
		return {"meter": entry.get("released_qty_meter"), "kg": entry.get("released_qty_kg")}
	remainder = get_latest_partial_remainder(entry.name)
	return {
		"meter": remainder.get("remaining_qty_meter") if remainder else entry.planned_meter,
		"kg": remainder.get("remaining_qty_kg") if remainder else entry.planned_kg,
	}


def get_latest_partial_remainder(plan_entry):
	meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	required_fields = {"completion_type", "remaining_qty_meter", "remaining_qty_kg"}
	if any(not meta.has_field(fieldname) for fieldname in required_fields):
		return None
	rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": plan_entry, "status": "Completed", "completion_type": "Partial Completion"},
		fields=["remaining_qty_meter", "remaining_qty_kg"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	row = rows[0]
	return row if flt(row.remaining_qty_meter) or flt(row.remaining_qty_kg) else None


def get_previous_mandatory_entry(entry):
	if entry.get("plan_type") == "Required Material":
		return None
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"plan_type": entry.get("plan_type") or "Final EB",
			"sequence": ["<", entry.sequence],
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "operation", "operation_row", "sequence"],
		order_by="sequence desc",
		limit_page_length=20,
	)
	for row in rows:
		if is_mandatory_operation(row.operation_row):
			return row
	return None


def get_next_mandatory_entry(entry):
	if entry.get("plan_type") == "Required Material":
		return None
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"sequence": [">", entry.sequence],
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "operation", "operation_row", "sequence"],
		order_by="sequence asc",
		limit_page_length=20,
	)
	for row in rows:
		if is_mandatory_operation(row.operation_row):
			return frappe.get_doc("SmartEdge Production Plan Entry", row.name)
	return None


def is_mandatory_operation(operation_row):
	if not operation_row:
		return True
	mandatory = frappe.db.get_value("Final EB BOM Operation", operation_row, "mandatory")
	return cint(mandatory) == 1


def release_completed_quantity_to_next_operation(job):
	if not job.plan_entry:
		return
	if job.get("released_to_plan_entry"):
		return
	source_entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry

	if is_qc_entry(source_entry):
		return
	next_entry = get_next_mandatory_entry(source_entry)
	if not next_entry:
		return
	eligible_qty_meter = round_meter_quantity(flt(job.good_qty) or flt(job.actual_qty_meter))
	eligible_qty_kg = flt(job.actual_qty_kg)
	if eligible_qty_meter <= 0 and eligible_qty_kg <= 0:
		return
	if next_entry.get("released_from_production_job"):
		if next_entry.released_from_production_job == job.name:
			job.released_to_plan_entry = next_entry.name
			job.released_qty_meter = next_entry.get("released_qty_meter")
			job.released_qty_kg = next_entry.get("released_qty_kg")
			return
		frappe.throw(
			_("Operation {0} already has released quantity from Production Job {1}.").format(
				next_entry.operation, next_entry.released_from_production_job
			)
		)
	next_entry.released_from_plan_entry = source_entry.name
	next_entry.released_from_production_job = job.name
	next_entry.released_qty_meter = eligible_qty_meter
	next_entry.released_qty_kg = eligible_qty_kg
	if next_entry.status in {"Pending", "Planned", "On Hold"}:
		next_entry.status = "Ready"
	next_entry.save()
	update_generator_planning_status(next_entry.final_eb_bom_generator)
	job.released_to_plan_entry = next_entry.name
	job.released_qty_meter = eligible_qty_meter
	job.released_qty_kg = eligible_qty_kg


@frappe.whitelist()
def start_production_job(plan_entry, operator_employee=None, workstation=None, shift=None):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("start"))
	entry.check_permission("write")
	from smart_edge_custom.manufacturing.quality_control import (
		begin_review,
		ensure_pending_inspection,
		get_current_employee,
		is_qc_entry,
		require_qc_authority,
	)

	qc_entry = is_qc_entry(entry)
	if qc_entry:
		require_qc_authority()
		operator_employee = operator_employee or get_current_employee()
	company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
	if qc_entry:
		validate_employee(operator_employee, company)
	workstation = workstation or entry.workstation
	validate_selected_workstation(entry, workstation)
	validate_start_eligible(entry)
	if not qc_entry:
		operator_employee = get_authenticated_employee(company)
		if not operator_employee:
			frappe.throw(_("An active Employee record is required for Production Job execution. Planner is linked to the current User separately."))
		validate_employee(operator_employee, company)
	if entry.planned_start_datetime and entry.planned_end_datetime:
		validate_capacity(workstation, entry.name, entry.planned_start_datetime, entry.planned_end_datetime)
	else:
		validate_workstation_job_capacity(workstation, plan_entry=entry.name)
	lock_plan_entry(plan_entry)
	entry.reload()
	validate_start_eligible(entry)
	material_transfer = None
	if entry.get("plan_type") == "Required Material":
		from smart_edge_custom.manufacturing.material_production import ensure_material_transfer

		material_transfer = ensure_material_transfer(entry)
	job_rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": plan_entry, "status": ["not in", ["Cancelled"]]},
		fields=["name", "status"],
		order_by="modified desc",
		limit_page_length=5,
	)
	active_jobs = [row for row in job_rows if row.status != "Completed"]
	if len(active_jobs) > 1:
		frappe.throw(_("Multiple active Production Jobs exist for this planned operation."))
	existing = active_jobs[0].name if active_jobs else None
	from smart_edge_custom.manufacturing.production_lot import get_input_production_lot
	resolved_input_lot = get_input_production_lot(entry)
	if existing:
		job = frappe.get_doc("SmartEdge Production Job", existing)
		job.check_permission("write")
		if resolved_input_lot and job.meta.has_field("input_production_lot") and not job.get("input_production_lot"):
			job.input_production_lot = resolved_input_lot
		if job.status in {"Completed", "Cancelled"}:
			frappe.throw(_("Completed or cancelled Production Jobs cannot be restarted."))
		if job.status in {"Draft", "Paused", "On Hold"}:
			if not qc_entry and job.meta.has_field("operator"):
				job.operator = frappe.session.user
			job.operator_employee = operator_employee
			job.workstation = workstation
			if shift:
				job.shift = shift
			elif not job.shift:
				from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
					get_current_shift,
				)

				job.shift = get_current_shift(operator_employee)
			if not job.actual_start:
				job.actual_start = now_datetime()
			if job.meta.has_field("current_timer_start") and not job.current_timer_start:
				job.current_timer_start = now_datetime()
			job.status = "In Progress"
			if job.meta.has_field("material_transfer_stock_entry"):
				job.material_transfer_stock_entry = material_transfer
			job.save()
			job.sync_plan_entry("In Progress")
			entry.reload()
		elif job.status == "In Progress":
			if not job.operator_employee:
				job.operator_employee = operator_employee
				job.save()
			if job.workstation != workstation:
				frappe.throw(_("Production Job {0} is already in progress on Workstation {1}.").format(job.name, job.workstation))
		from smart_edge_custom.manufacturing.production_lot import record_qc_hold

		record_qc_hold(job, entry)
		if qc_entry:
			inspection = ensure_pending_inspection(entry)
			if inspection:
				begin_review(inspection.name, operator_employee)
		if "pack" in str(entry.get("planning_category") or "").lower() or "pack" in str(entry.get("operation") or "").lower():
			from smart_edge_custom.manufacturing.roll_tracking import bind_roll_reservations_to_job
			bind_roll_reservations_to_job(entry.name, job.name)
		return {"production_job": existing, "existing": 1, "requires_operator": 0}
	job = frappe.new_doc("SmartEdge Production Job")
	job.plan_entry = entry.name
	job.final_eb_bom_generator = entry.final_eb_bom_generator
	job.company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
	job.operation = entry.operation
	job.workstation = workstation
	job.planned_start = entry.planned_start_datetime
	job.planned_end = entry.planned_end_datetime
	pending_quantity = get_remaining_or_planned_quantity(entry)
	job.planned_qty_meter = (
		cint(pending_quantity.get("meter"))
		if entry.get("plan_type") == MATERIAL_PLAN_TYPE
		else round_meter_quantity(pending_quantity.get("meter"))
	)
	job.planned_qty_kg = pending_quantity.get("kg")
	apply_released_quantity_to_job(job, entry)
	if resolved_input_lot and job.meta.has_field("input_production_lot"):
		job.input_production_lot = resolved_input_lot
	if not qc_entry and job.meta.has_field("operator"):
		job.operator = frappe.session.user
	job.operator_employee = operator_employee
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import get_current_shift

	job.shift = shift or get_current_shift(operator_employee)
	job.actual_start = now_datetime()
	if job.meta.has_field("current_timer_start"):
		job.current_timer_start = job.actual_start
	job.status = "In Progress"
	if job.meta.has_field("material_transfer_stock_entry"):
		job.material_transfer_stock_entry = material_transfer
	job.insert()
	from smart_edge_custom.manufacturing.production_lot import record_qc_hold

	record_qc_hold(job, entry)
	if qc_entry:
		inspection = ensure_pending_inspection(entry)
		if inspection:
			begin_review(inspection.name, operator_employee)
	if frappe.get_meta("SmartEdge Production Plan Entry", cached=True).has_field("production_job"):
		entry.production_job = job.name
	entry.status = "In Progress"
	entry.save()
	if "pack" in str(entry.get("planning_category") or "").lower() or "pack" in str(entry.get("operation") or "").lower():
		from smart_edge_custom.manufacturing.roll_tracking import bind_roll_reservations_to_job
		bind_roll_reservations_to_job(entry.name, job.name)
	update_generator_planning_status(entry.final_eb_bom_generator)
	return {"production_job": job.name, "existing": 0, "requires_operator": 0}


def apply_released_quantity_to_job(job, entry):
	"""Seed a new operation job from the completed predecessor's released output."""
	source_job_name = entry.get("released_from_production_job")
	if not source_job_name:
		return

	source_job = frappe.db.get_value(
		"SmartEdge Production Job",
		source_job_name,
		["actual_qty_meter", "actual_qty_kg", "good_qty", "released_qty_meter", "released_qty_kg"],
		as_dict=True,
	)
	if not source_job:
		return

	# The plan entry is the release gate; never seed values from an unreleased job.
	if not flt(entry.get("released_qty_meter")) and not flt(entry.get("released_qty_kg")):
		return
	released_meter = flt(entry.get("released_qty_meter"))
	previous_entry = get_previous_mandatory_entry(entry)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry
	if previous_entry and is_qc_entry(previous_entry):
		released_meter = whole_meter_not_exceeding(released_meter)
	else:
		released_meter = round_meter_quantity(released_meter)
	released_kg = flt(entry.get("released_qty_kg"))
	job.actual_qty_meter = released_meter
	job.actual_qty_kg = released_kg
	job.good_qty = released_meter


def validate_start_eligible(entry):
	from smart_edge_custom.manufacturing.work_order_sync import validate_material_work_order_reference
	validate_material_work_order_reference(entry)
	if entry.status in {"Completed", "Cancelled"}:
		frappe.throw(_("Completed or cancelled operations cannot be started."))
	validate_material_plan_released(entry)
	validate_scheduled_start_date(entry)
	validate_predecessor_release(entry)
	if not entry.workstation:
		frappe.throw(_("Schedule the operation on a Workstation before starting production."))
	if not entry.planned_start_datetime:
		frappe.throw(_("Schedule the operation before starting production."))


def get_scheduled_start_eligibility(entry, current_date=None):
	if not entry.get("planned_date"):
		return {
			"allowed": False,
			"message": _("Schedule the operation before starting production."),
		}
	scheduled_date = getdate(entry.get("planned_date"))
	site_date = getdate(current_date or nowdate())
	if site_date < scheduled_date:
		return {
			"allowed": False,
			"message": _("Production cannot be started before the scheduled date: {0}.").format(
				scheduled_date.strftime("%d-%m-%Y")
			),
		}
	return {"allowed": True, "message": None}


def validate_scheduled_start_date(entry, current_date=None):
	eligibility = get_scheduled_start_eligibility(entry, current_date=current_date)
	if not eligibility["allowed"]:
		frappe.throw(eligibility["message"])


def validate_predecessor_release(entry):
	previous_entry = get_previous_mandatory_entry(entry)
	material_predecessors = get_material_start_predecessors(entry, previous_entry)
	incomplete_materials = [row for row in material_predecessors if row.status != "Completed"]
	if incomplete_materials:
		frappe.throw(
			_("Cannot start {0} before linked material operation {1} is completed.").format(
				entry.operation,
				incomplete_materials[0].operation,
			)
		)
	if not previous_entry:
		return
	if flt(entry.get("released_qty_meter")) or flt(entry.get("released_qty_kg")):
		return
	frappe.throw(
		_("Cannot start {0} because the required preceding operation has not released completed production quantity.").format(
			entry.operation
		)
	)


def get_material_start_predecessors(entry, route_predecessor=None):
	filters = {
		"final_eb_bom_generator": entry.final_eb_bom_generator,
		"plan_type": MATERIAL_PLAN_TYPE,
		"status": ["!=", "Cancelled"],
	}
	if entry.get("plan_type") == MATERIAL_PLAN_TYPE:
		if entry.get("material_type") != "Base Colour":
			return []
		filters["material_type"] = "Compound"
	elif route_predecessor:
		return []

	return frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters=filters,
		fields=["name", "operation", "material_type", "status"],
		order_by="creation asc",
		limit_page_length=100,
	)


def validate_selected_workstation(entry, workstation):
	if not workstation:
		frappe.throw(_("Workstation is required."))
	if entry.get("plan_type") == "Required Material" and workstation == entry.workstation:
		row = frappe.db.get_value("Workstation", workstation, ["disabled"], as_dict=True)
		if not row or row.disabled:
			frappe.throw(_("Select an enabled Workstation."))
		frappe.get_doc("Workstation", workstation).check_permission("read")
		return
	validate_workstation_compatibility(entry.operation, entry.operation_row, workstation)


def lock_plan_entry(plan_entry):
	frappe.db.sql(
		"select name from `tabSmartEdge Production Plan Entry` where name=%s for update",
		(plan_entry,),
	)


@frappe.whitelist()
def pause_production_job(plan_entry, downtime_reason=None, downtime_remarks=None):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("pause"))
	job = get_open_job(plan_entry)
	lock_production_job(job.name)
	job.reload()
	job.pause_job(downtime_reason=downtime_reason, downtime_remarks=downtime_remarks)
	return {"production_job": job.name, "status": job.status}


@frappe.whitelist()
def resume_production_job(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("resume"))
	job = get_open_job(plan_entry)
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry, require_qc_authority

	if is_qc_entry(entry):
		require_qc_authority()
	validate_predecessor_release(entry)
	lock_production_job(job.name)
	job.reload()
	job.resume_job()
	return {"production_job": job.name, "status": job.status}


@frappe.whitelist()
def complete_production_job(
	plan_entry,
	completion_type=None,
	actual_qty_meter=None,
	actual_qty_kg=None,
	good_qty=None,
	good_qty_kg=None,
	rejected_qty=None,
	rejected_qty_kg=None,
	rework_qty=None,
	rework_qty_kg=None,
	usable_scrap=None,
	usable_scrap_uom=None,
	unusable_scrap=None,
	unusable_scrap_uom=None,
	remarks=None,
	roll_count=None,
	roll_packing_details=None,
):
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
		OUTPUT_QUANTITY_FIELDS,
		parse_output_quantity,
	)

	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.operator_access import require_plan_entry_access
	require_plan_entry_access(entry, _("complete"))
	job = get_open_job(plan_entry, include_completed=True)
	lock_production_job(job.name)
	job.reload()
	if entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		actual_qty_meter = round_meter_quantity(
			parse_output_quantity(actual_qty_meter, _("Actual Completed Qty"), 6)
		)
		if all(value is None for value in (
			good_qty_kg, rejected_qty_kg, rework_qty_kg, usable_scrap_uom, unusable_scrap_uom
		)):
			good_qty = round_meter_quantity(parse_output_quantity(good_qty, _("Good Output"), 6))
			rejected_qty = round_meter_quantity(parse_output_quantity(rejected_qty, _("Rejected Output"), 6))
			rework_qty = round_meter_quantity(parse_output_quantity(rework_qty, _("Rework"), 6))
	is_extrusion = is_extrusion_operation(entry)
	# A repeated request for an already completed job must remain idempotent;
	# never append a second copy of the submitted roll rows.
	roll_rows = []
	if job.status != "Completed":
		roll_rows = validate_roll_packing_details(
			entry, job, roll_count, roll_packing_details, actual_qty_meter
		)
	elif is_extrusion and roll_packing_details not in (None, "", [], "[]"):
		roll_rows = []
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry, validate_downstream_quantity

	if is_qc_entry(entry) and not getattr(frappe.flags, "smartedge_qc_decision", False):
		frappe.throw(_("Use Submit QC Decision to complete a mandatory QC checkpoint."))
	completion_quantities = None
	if any(value is not None for value in (
		good_qty_kg, rejected_qty_kg, rework_qty_kg, usable_scrap_uom, unusable_scrap_uom
	)):
		completion_quantities = calculate_completion_quantities(
			plan_entry,
			actual_qty_kg=actual_qty_kg,
			rejected_qty_kg=rejected_qty_kg,
			rework_qty_kg=rework_qty_kg,
			usable_scrap=usable_scrap,
			usable_scrap_uom=usable_scrap_uom,
			unusable_scrap=unusable_scrap,
			unusable_scrap_uom=unusable_scrap_uom,
		)
		if good_qty_kg is not None and abs(
			flt(parse_completion_quantity(good_qty_kg, _("Good Output (Kg)")))
			- flt(completion_quantities["good_qty_kg"])
		) > 0.000001:
			frappe.throw(_("Good Output does not match the authoritative quantity calculation."))
		job.actual_qty_kg = completion_quantities["actual_qty_kg"]
		job.good_qty = completion_quantities["good_qty"]
		job.rejected_qty = completion_quantities["rejected_qty"]
		job.rework_qty = completion_quantities["rework_qty"]
		job.usable_scrap = completion_quantities["usable_scrap_stock_qty"]
		job.unusable_scrap = completion_quantities["unusable_scrap_stock_qty"]
	elif entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		# In the meter-only editor Good Output is derived from the explicit
		# rejected/rework quantities.  Keep the established output fields, but do
		# not let a stale client value hide an unallocated extrusion balance.
		job.rejected_qty = round_meter_quantity(parse_output_quantity(rejected_qty, _("Rejected Output"), 6))
		job.rework_qty = round_meter_quantity(parse_output_quantity(rework_qty, _("Rework"), 6))
		job.good_qty = round_meter_quantity(
			max(flt(actual_qty_meter) - flt(job.rejected_qty) - flt(job.rework_qty), 0)
		)
	if is_extrusion and roll_rows:
		# Re-run after all approved loss channels have been resolved from the
		# request (including Kg/scrap-to-meter conversion).
		validate_roll_packing_details(entry, job, len(roll_rows), roll_rows, actual_qty_meter, enforce_accounting=True)
	if job.status == "Completed" and entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		validate_packing_rolls_for_completion(entry, job, completion_type or "Full Completion")
		is_packing = "pack" in str(entry.get("planning_category") or "").lower() or "pack" in str(entry.get("operation") or "").lower()
		previous_defer = getattr(frappe.flags, "smartedge_defer_packing_reconciliation", False)
		if is_packing:
			frappe.flags.smartedge_defer_packing_reconciliation = True
		try:
			result = job.complete_job(completion_type or "Full Completion")
		finally:
			frappe.flags.smartedge_defer_packing_reconciliation = previous_defer
		# The completion boundary is the only place where permanent Roll masters
		# are created.  A retry is handled idempotently by the source-job/sequence
		# key in roll_tracking and cannot create stock or child rolls.
		if is_extrusion and roll_rows:
			from smart_edge_custom.manufacturing.roll_tracking import create_rolls_for_extrusion
			create_rolls_for_extrusion(job, entry, roll_rows)
		if is_packing:
			from smart_edge_custom.manufacturing.roll_tracking import mark_rolls_packed_for_job
			mark_rolls_packed_for_job(job.name)
			if physical_operation_route_active(entry.final_eb_bom_generator):
				post_completed_physical_packing(job.name)
		if entry.get("plan_type") != MATERIAL_PLAN_TYPE and entry.final_eb_bom_generator:
			update_generator_planning_status(entry.final_eb_bom_generator)
		return {"production_job": job.name, **result}
	material_result = None
	if entry.get("plan_type") == "Required Material":
		from smart_edge_custom.manufacturing.work_order_sync import validate_material_work_order_reference
		validate_material_work_order_reference(entry)
		from smart_edge_custom.manufacturing.material_production import complete_material_production

		completed_batches = cint(actual_qty_meter)
		planned_batches = cint(job.planned_qty_meter)
		completion_type = completion_type or "Full Completion"
		if completion_type == "Full Completion" and completed_batches != planned_batches:
			frappe.throw(
				_("Full Completion requires all {0} planned batches.").format(planned_batches)
			)
		if completion_type == "Partial Completion" and completed_batches >= planned_batches:
			frappe.throw(_("Partial Completion must leave at least one batch remaining."))
		if completion_quantities:
			expected_material_quantity = flt(Decimal(str(completed_batches)) * Decimal(str(flt(entry.weight_per_batch))), 6)
			if abs(flt(completion_quantities["actual_qty_kg"]) - expected_material_quantity) > 0.000001:
				frappe.throw(
					_("Actual Completed Quantity must match the selected Completed Batches ({0} {1}) before completion.").format(
						expected_material_quantity, entry.quantity_uom or "Kg"
					)
				)
		previous_completion_flag = getattr(frappe.flags, "smartedge_board_completion", False)
		frappe.flags.smartedge_board_completion = True
		if unusable_scrap is not None and not completion_quantities:
			field = job.meta.get_field("unusable_scrap")
			job.unusable_scrap = parse_output_quantity(
				unusable_scrap, field.label if field else _("Unusable Scrap"),
				job.precision("unusable_scrap") if field else 6,
			)
		try:
			material_result = complete_material_production(entry, completed_batches, production_job=job)
		finally:
			frappe.flags.smartedge_board_completion = previous_completion_flag
		actual_qty_meter = completed_batches
		actual_qty_kg = material_result["quantity"]
		good_qty = material_result["quantity"]

	for fieldname, value in {
		"actual_qty_meter": actual_qty_meter,
		"actual_qty_kg": actual_qty_kg,
		"good_qty": good_qty,
		"rejected_qty": rejected_qty,
		"rework_qty": rework_qty,
		"usable_scrap": usable_scrap,
		"unusable_scrap": unusable_scrap,
		"remarks": remarks,
	}.items():
		if value is not None:
			if completion_quantities and fieldname in {
				"actual_qty_kg", "good_qty", "rejected_qty", "rework_qty", "usable_scrap", "unusable_scrap"
			}:
				continue
			if fieldname in OUTPUT_QUANTITY_FIELDS:
				field = job.meta.get_field(fieldname)
				value = parse_output_quantity(
					value,
					field.label if field else fieldname,
					job.precision(fieldname) if field else 6,
				)
			job.set(fieldname, flt(value) if fieldname != "remarks" else value)
	if entry.get("plan_type") != MATERIAL_PLAN_TYPE:
		validate_downstream_quantity(entry, max(flt(job.actual_qty_meter), flt(job.good_qty)))
	completion_type = completion_type or "Full Completion"
	validate_packing_rolls_for_completion(entry, job, completion_type)
	if material_result:
		job.manufacturing_stock_entry = material_result["stock_entry"]
		job.stock_posted_qty = material_result["quantity"]
	else:
		validate_final_completion_stock_entry_setup(job, completion_type)
	if roll_rows:
		existing_roll_numbers = [cint(row.roll_no) for row in (job.get("roll_packing_details") or [])]
		roll_offset = max(existing_roll_numbers or [0])
		for row in roll_rows:
			job.append(
				"roll_packing_details",
				{
					"roll_no": roll_offset + row["roll_no"],
					"meter_quantity": row["meter_quantity"],
					"destination_warehouse": row.get("destination_warehouse"),
				},
			)
	is_packing = "pack" in str(entry.get("planning_category") or "").lower() or "pack" in str(entry.get("operation") or "").lower()
	previous_defer = getattr(frappe.flags, "smartedge_defer_packing_reconciliation", False)
	if is_packing:
		frappe.flags.smartedge_defer_packing_reconciliation = True
	try:
		completion_result = job.complete_job(completion_type)
	finally:
		frappe.flags.smartedge_defer_packing_reconciliation = previous_defer
	if is_extrusion and roll_rows:
		from smart_edge_custom.manufacturing.roll_tracking import create_rolls_for_extrusion
		create_rolls_for_extrusion(job, entry, roll_rows)
	if is_packing:
		from smart_edge_custom.manufacturing.roll_tracking import mark_rolls_packed_for_job
		mark_rolls_packed_for_job(job.name)
		if physical_operation_route_active(entry.final_eb_bom_generator):
			post_completed_physical_packing(job.name)
	# Re-evaluate the exact linked generator after the job has persisted its
	# completed plan-entry state.  This is the authoritative point at which a
	# newly completed final operation can trigger final manufacturing posting.
	if entry.get("plan_type") != MATERIAL_PLAN_TYPE and entry.final_eb_bom_generator:
		update_generator_planning_status(entry.final_eb_bom_generator)
	if material_result:
		from smart_edge_custom.manufacturing.material_production import refresh_material_readiness
		from smart_edge_custom.manufacturing.work_order_sync import sync_work_order_to_planning

		sync_work_order_to_planning(entry.work_order, refresh_readiness=False)
		refresh_material_readiness(entry.final_eb_bom_generator)
	return {"production_job": job.name, "status": job.status, **(completion_result or {})}


def post_completed_physical_packing(production_job):
	"""Post one submitted Physical WIP Repack per eligible packed roll.

	Each roll has its own stable Stock Entry reference, so retries can resume a
	partially completed job without reposting a roll that already succeeded.
	"""
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	if job.status != "Completed" or not is_packing_plan_entry(entry):
		return None
	from smart_edge_custom.manufacturing import physical_operation_stock as physical_stock
	if not physical_stock.production_uses_physical_stock(job.final_eb_bom_generator):
		return None
	# Preserve the established job-level transaction when one already exists;
	# never split or repost a submitted historical receipt.
	if job.get("manufacturing_stock_entry"):
		if frappe.db.get_value("Stock Entry", job.manufacturing_stock_entry, "docstatus") == 1:
			return job.manufacturing_stock_entry
		frappe.throw(_("Completed Physical Packing Job {0} has an unresolved Stock Entry reference.").format(job.name))
	lock_final_eb_bom_generator(job.final_eb_bom_generator)
	generator = frappe.get_doc("Final EB BOM Generator", job.final_eb_bom_generator)
	from smart_edge_custom.manufacturing.roll_tracking import link_packed_rolls_to_finished_batch
	from smart_edge_custom.manufacturing.production_lot import reconcile_final_production
	rolls = frappe.get_all("SmartEdge Roll", filters={
		"packing_job": job.name, "qc_status": "Passed",
		"process_status": "Packed", "packing_status": "Packed",
	}, fields=["name", "current_meter_quantity"], order_by="roll_sequence asc, name asc", limit_page_length=500)
	if not rolls:
		frappe.throw(_("Physical Packing Job {0} has no eligible QC-approved packed Rolls.").format(job.name))
	fg_batch = physical_stock.get_packing_fg_batch(generator)
	posted = []
	for roll in rolls:
		quantity = flt(roll.current_meter_quantity)
		if quantity <= 0:
			frappe.throw(_("SmartEdge Roll {0} has no positive packing quantity.").format(roll.name))
		output = get_finished_stock_row(generator, quantity, get_finished_goods_warehouse(generator))
		stock_entry = physical_stock.post_incremental_packing_repack(
			generator, job, frappe._dict(output), fg_batch, roll_name=roll.name
		)
		posted.append(stock_entry)
		link_packed_rolls_to_finished_batch(stock_entry, generator.name, roll_name=roll.name)
		reconcile_final_production(generator.name, stock_entry)
		if not fg_batch:
			fg_batch = physical_stock.get_packing_fg_batch(generator)
	if job.meta.has_field("stock_posted_qty"):
		job.db_set("stock_posted_qty", sum(flt(roll.current_meter_quantity) for roll in rolls), update_modified=False)
	lot = job.get("output_production_lot")
	if lot and frappe.db.exists("SmartEdge Production Lot", lot) and posted:
		# Keep the lot auditable without making one roll's receipt the job-level
		# idempotency key.  Retries are keyed by each roll reference above.
		frappe.db.set_value("SmartEdge Production Lot", lot, "stock_entry", posted[-1], update_modified=False)
	return posted[0] if len(posted) == 1 else posted


def lock_production_job(production_job):
	frappe.db.sql("select name from `tabSmartEdge Production Job` where name=%s for update", (production_job,))


def get_open_job(plan_entry, include_completed=False):
	# A Plan Entry can outlive its Production Job if historical data was
	# removed outside the normal lifecycle.  Detect that dangling reference
	# explicitly before querying active jobs; recreating a Job here would risk
	# duplicate stock posting and would lose the original audit trail.
	plan_link = frappe.db.get_value(
		"SmartEdge Production Plan Entry",
		plan_entry,
		["production_job", "operation"],
		as_dict=True,
	)
	linked_job = plan_link.get("production_job") if plan_link else None
	if linked_job and not frappe.db.exists("SmartEdge Production Job", linked_job):
		frappe.throw(
			_(
				"Plan Entry {0} references missing Production Job {1}. "
				"Restore the original Job or use the controlled replanning workflow "
				"after reconciling its Lots, Rolls, QC decisions, and Stock Entries."
			).format(plan_entry, linked_job),
			title=_("Missing Production Job"),
		)
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": plan_entry, "status": ["!=", "Cancelled"]},
		fields=["name", "status"],
		order_by="modified desc",
		limit_page_length=10,
	)
	active_jobs = [job for job in jobs if job.status not in {"Completed", "Cancelled"}]
	if len(active_jobs) > 1:
		frappe.throw(_("Multiple active Production Jobs exist for this planned operation."))
	name = active_jobs[0].name if active_jobs else None
	if not name and include_completed and jobs and jobs[0].status == "Completed":
		name = jobs[0].name
	if not name:
		frappe.throw(_("No active Production Job found for this planned operation."))
	job = frappe.get_doc("SmartEdge Production Job", name)
	job.check_permission("write")
	return job


def validate_employee(employee, company=None):
	if not employee:
		frappe.throw(_("Operator is required."))
	employee_doc = frappe.get_doc("Employee", employee)
	employee_doc.check_permission("read")
	if employee_doc.status != "Active":
		frappe.throw(_("Select an active Employee for this Company."))
	if company and frappe.get_meta("Employee", cached=True).has_field("company") and employee_doc.company != company:
		frappe.throw(_("Select an active Employee for this Company."))


def get_authenticated_employee(company=None):
	filters = {"user_id": frappe.session.user, "status": "Active"}
	if company and frappe.get_meta("Employee", cached=True).has_field("company"):
		filters["company"] = company
	employees = frappe.get_all("Employee", filters=filters, pluck="name", limit=2)
	return employees[0] if len(employees) == 1 else None


def can_read_employee(employee):
	try:
		frappe.get_doc("Employee", employee).check_permission("read")
	except frappe.PermissionError:
		return False
	return True
