from collections import defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_time, getdate, now_datetime, nowdate

from smart_edge_custom.manufacturing.fifo_batch_selection import get_kg_to_stock_factor
from smart_edge_custom.manufacturing.workstation_capacity import (
	get_capacity,
	get_reserved_work_order_count,
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
	week_start = get_week_start(week_start)
	week_end = week_start + timedelta(days=6)
	entries = get_plan_entries(
		week_start,
		week_end,
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
	board = make_empty_board(week_start)

	for row in entries:
		card = make_operation_card(row)
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
	board["filters"] = {
		"categories": list(CATEGORY_COLORS),
		"workstations": frappe.get_all("Workstation", filters={"disabled": 0}, pluck="name", order_by="name"),
		"operations": frappe.get_all("Operation", pluck="name", order_by="name"),
		"statuses": PLANNING_STATUSES,
		"priorities": ("Urgent", "High", "Normal", "Low"),
	}
	return board


def get_week_start(value=None):
	date_value = getdate(value or nowdate())
	return date_value - timedelta(days=date_value.weekday())


def get_plan_entries(week_start, week_end, **filters):
	entry_filters = {"status": ["!=", "Cancelled"]}
	if filters.get("category"):
		entry_filters["planning_category"] = filters["category"]
	if filters.get("workstation"):
		entry_filters["workstation"] = filters["workstation"]
	if filters.get("operation"):
		entry_filters["operation"] = filters["operation"]
	if filters.get("status"):
		entry_filters["status"] = filters["status"]
	if filters.get("priority"):
		entry_filters["priority"] = filters["priority"]

	fields = [
		"name",
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
	result = []
	for row in rows:
		generator = generators.get(row.final_eb_bom_generator)
		if not generator:
			continue
		if row.planned_date and not (week_start <= getdate(row.planned_date) <= week_end):
			continue
		if filters.get("finished_product") and generator.finished_item != filters["finished_product"]:
			continue
		if filters.get("size") and str(generator.custom_size or "") != str(filters["size"]):
			continue
		if filters.get("customer") and str(generator.custom_customer_order_code or "") != str(filters["customer"]):
			continue
		if filters.get("search") and not matches_search(row, generator, filters["search"]):
			continue
		for key, value in generator.items():
			if key not in {"name", "status"}:
				row[key] = value
		result.append(row)
	attach_job_details(result)
	return result


def attach_job_details(rows):
	if not rows:
		return
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
		row.job_remaining_qty_meter = job.get("remaining_qty_meter")
		row.job_remaining_qty_kg = job.get("remaining_qty_kg")
	attach_open_downtime_details(rows)


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
	if row.get("job_status") == "Draft":
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
		"finished_item",
		"finished_item_name",
		"custom_size",
		"custom_base_colour",
		"custom_customer_order_code",
		"production_quantity",
		"stock_uom",
		"total_eb_weight_kg",
		"compound",
		"compound_total_required",
		"base_colour",
		"shade_total_required",
		"printing_type",
		"printing_route",
		"extrusion_type",
		"slitting_requirement",
		"selected_route",
		"status",
	]
	return {
		row.name: row
		for row in frappe.get_all("Final EB BOM Generator", filters={"name": ["in", names]}, fields=fields)
	}


def matches_search(row, generator, search):
	text = " ".join(
		str(value or "")
		for value in (
			row.name,
			row.final_eb_bom_generator,
			row.operation,
			row.workstation,
			generator.finished_item,
			generator.finished_item_name,
			generator.custom_base_colour,
			generator.custom_customer_order_code,
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
	return {
		"id": row.name,
		"production": row.final_eb_bom_generator,
		"operation_row": row.operation_row,
		"operation": row.operation,
		"operation_sequence": row.sequence,
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
		"compound": row.compound,
		"base_colour": row.base_colour,
		"priority": row.priority or "Normal",
		"customer_code": row.custom_customer_order_code,
		"production_job": row.get("production_job"),
		"job_status": row.get("job_status"),
		"operator_employee": row.get("operator_employee"),
		"operator_name": row.get("operator_name"),
		"shift": row.get("shift"),
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
		"job_remaining_qty_meter": flt(row.get("job_remaining_qty_meter")),
		"job_remaining_qty_kg": flt(row.get("job_remaining_qty_kg")),
		"downtime_start": str(row.get("downtime_start")) if row.get("downtime_start") else None,
		"downtime_reason": row.get("downtime_reason"),
		"downtime_remarks": row.get("downtime_remarks"),
		"downtime_employee": row.get("downtime_employee"),
		"downtime_elapsed_seconds": cint(row.get("downtime_elapsed_seconds") or 0),
	}


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
			workstations.append(
				{
					"workstation": workstation,
					"cards": cards,
					"planned_hours": sum(flt(card["duration_hours"]) for card in cards),
					"capacity": get_workstation_capacity_payload(workstation, cards[0]["planned_date"]) if cards else {},
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
		"compound": doc.compound,
		"compound_required": doc.compound_total_required,
		"base_colour": doc.base_colour,
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
def get_compatible_workstations(operation, operation_row=None):
	frappe.has_permission("Workstation", "read", throw=True)
	if not operation or not frappe.db.exists("Operation", operation):
		return []
	workstations = []
	default_workstation = frappe.db.get_value("Operation", operation, "workstation")
	if operation_row:
		add_unique(
			workstations,
			frappe.db.get_value("Final EB BOM Operation", operation_row, "workstation"),
		)

	if frappe.db.exists("DocType", "SmartEdge Operation Workstation"):
		rows = frappe.get_all(
			"SmartEdge Operation Workstation",
			filters={"operation": operation, "enabled": 1},
			fields=["workstation"],
			order_by="priority asc",
		)
		for row in rows:
			add_unique(workstations, row.workstation)

	if frappe.db.exists("DocType", "SmartEdge Route Operation"):
		route_rows = frappe.get_all(
			"SmartEdge Route Operation",
			filters={"operation": operation},
			fields=["workstation", "parent"],
			order_by="idx asc",
			limit_page_length=500,
		)
		route_names = [row.parent for row in route_rows if row.parent]
		enabled_routes = set()
		if route_names:
			enabled_routes = set(
				frappe.get_all(
					"SmartEdge Process Route",
					filters={"name": ["in", route_names], "enabled": 1},
					pluck="name",
					limit_page_length=500,
				)
			)
		for row in route_rows:
			if row.parent in enabled_routes:
				add_unique(workstations, row.workstation)

	add_unique(workstations, default_workstation)
	return filter_enabled_workstations(workstations)


def filter_enabled_workstations(workstations):
	if not workstations:
		return []
	enabled = set(
		frappe.get_all(
			"Workstation",
			filters={"name": ["in", workstations], "disabled": 0},
			pluck="name",
			limit_page_length=len(workstations),
		)
	)
	return [workstation for workstation in workstations if workstation in enabled]


def add_unique(values, value):
	if value and value not in values:
		values.append(value)


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
):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	validate_plan_entry(entry)
	validate_workstation_compatibility(entry.operation, entry.operation_row, workstation)

	start_dt = combine_date_time(planned_date, planned_start)
	duration_hours = flt(duration_hours) or get_entry_default_duration(entry)
	end_dt = start_dt + timedelta(hours=duration_hours)
	validate_future_schedule(start_dt, end_dt)

	validate_sequence(entry, start_dt, bool(int(override_sequence or 0)))
	capacity_warning = validate_capacity(
		workstation, entry.name, start_dt, end_dt, bool(int(override_capacity or 0))
	)

	entry.workstation = workstation
	entry.planned_date = getdate(planned_date)
	entry.planned_start_datetime = start_dt
	entry.planned_end_datetime = end_dt
	entry.status = "Planned"
	if priority:
		entry.priority = priority
	entry.save()
	update_generator_planning_status(entry.final_eb_bom_generator)
	return {"message": _("Operation scheduled."), "warning": capacity_warning}


@frappe.whitelist()
def reschedule_operation(**kwargs):
	return schedule_operation(**kwargs)


@frappe.whitelist()
def unplan_operation(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
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
	if workstation in get_compatible_workstations(operation, operation_row):
		return
	frappe.throw(_("Selected Workstation is not configured for this Operation. Please choose a compatible Workstation."))


def combine_date_time(date_value, time_value):
	date_value = getdate(date_value)
	if isinstance(time_value, str) and len(time_value.split(":")) == 2:
		time_value = f"{time_value}:00"
	return get_datetime(datetime.combine(date_value, get_time(time_value or time(9, 0))))


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


def validate_sequence(entry, start_dt, override=False):
	previous = get_sequence_predecessors(entry)
	for row in previous:
		if row.status == "Completed":
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


def validate_capacity(workstation, plan_entry, start_dt, end_dt, override=False):
	lock_workstation(workstation)
	capacity_hours = get_workstation_capacity_hours(workstation, getdate(start_dt))
	planned = get_workstation_planned_minutes(workstation, getdate(start_dt), plan_entry)
	new_minutes = (end_dt - start_dt).total_seconds() / 60
	overlaps = get_overlapping_entries(workstation, start_dt, end_dt, plan_entry)
	if overlaps and not override:
		occupied = get_reserved_work_order_count(workstation) + len(overlaps)
		maximum = get_capacity(workstation)
		if occupied >= maximum:
			frappe.throw(
				_("""
				<p><b>Workstation Job Capacity Exceeded</b></p>
				<p>
					Workstation: {0}<br>
					Maximum Job Capacity: {1}<br>
					Occupied Capacity: {2}<br>
					Available Capacity: {3}
				</p>
				<p>Cannot plan or start another production job because this Workstation has reached its maximum Job Capacity.</p>
				""").format(
					frappe.bold(workstation),
					maximum,
					occupied,
					max(maximum - occupied, 0),
				),
				title=_("Workstation Job Capacity Exceeded"),
			)
	if capacity_hours and planned + new_minutes > capacity_hours * 60 and not override:
		over_by = (planned + new_minutes - capacity_hours * 60) / 60
		frappe.throw(_("Capacity exceeded by {0} hours.").format(flt(over_by, 2)))
	if overlaps:
		return _("Capacity override applied despite overlap: {0}.").format(", ".join(overlaps))
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
			"status": ["in", ["Planned", "In Progress"]],
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
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"workstation": workstation, "status": ["in", ["Planned", "In Progress"]]},
		fields=["name", "final_eb_bom_generator", "operation", "planned_start_datetime", "planned_end_datetime"],
		limit_page_length=500,
	)
	overlaps = []
	for row in rows:
		if row.name == exclude_entry or not row.planned_start_datetime or not row.planned_end_datetime:
			continue
		existing_start = get_datetime(row.planned_start_datetime)
		existing_end = get_datetime(row.planned_end_datetime)
		if existing_start < end_dt and start_dt < existing_end:
			overlaps.append(f"{row.final_eb_bom_generator} / {row.operation}")
	return overlaps


def update_generator_planning_status(production):
	status = get_completed_parent_status(production)
	if status:
		set_generator_status(production, status)
		return
	if frappe.db.get_value("Final EB BOM Generator", production, "status") == "Completed":
		return

	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"final_eb_bom_generator": production, "status": ["!=", "Cancelled"]},
		fields=["status"],
	)
	if not rows:
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


def get_completed_parent_status(production):
	if not production or not frappe.db.exists("Final EB BOM Generator", production):
		return None
	if not parent_allows_completed_status(production):
		return None
	mandatory_rows = get_mandatory_route_operation_rows(production)
	if not mandatory_rows:
		return None

	for operation_row in mandatory_rows:
		entry = get_route_plan_entry(production, operation_row.name)
		if not entry or entry.status != "Completed":
			return None
		job = get_completed_job_for_plan_entry(entry)
		if not job or job.status != "Completed":
			return None
		if job.get("completion_type") == "Partial Completion" and (
			flt(job.get("remaining_qty_meter")) or flt(job.get("remaining_qty_kg"))
		):
			return None
	return "Completed"


def parent_allows_completed_status(production):
	current_status = frappe.db.get_value("Final EB BOM Generator", production, "status")
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
	entries = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": production,
			"operation_row": operation_row,
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "status", "operation", "operation_row", "production_job"],
		order_by="modified desc",
		limit_page_length=2,
	)
	return entries[0] if len(entries) == 1 else None


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
	current_status = frappe.db.get_value("Final EB BOM Generator", production, "status")
	if current_status == "Cancelled":
		return
	if current_status == status and status != "Completed":
		return
	doc = frappe.get_doc("Final EB BOM Generator", production)
	if status == "Completed":
		create_final_manufacturing_stock_entry(doc)
	doc.db_set("status", status, update_modified=False)


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
			validate_final_job_stock_entry_matches_parent(doc.name, final_jobs, existing_stock_entry)
			link_final_manufacturing_stock_entry(
				doc,
				final_jobs,
				existing_stock_entry,
				sum(flt(job.stock_posting_qty) for job in final_jobs),
			)
		show_existing_stock_entry_message(existing_stock_entry)
		return existing_stock_entry
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

	components = get_final_stock_components(doc, finished_qty, source_warehouse)
	component_batch_nos = get_final_component_batch_nos(doc, components)
	finished_row = get_finished_stock_row(doc, finished_qty, target_warehouse)
	validate_batch_control([finished_row])

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.purpose = "Manufacture"
	if stock_entry.meta.has_field("stock_entry_type") and frappe.db.exists("Stock Entry Type", "Manufacture"):
		stock_entry.stock_entry_type = "Manufacture"
	stock_entry.company = doc.company
	stock_entry.from_warehouse = source_warehouse
	stock_entry.to_warehouse = target_warehouse
	stock_entry.posting_date = nowdate()
	stock_entry.remarks = _("Final manufacturing Stock Entry for Final EB BOM Generator {0}").format(doc.name)
	for component in components:
		stock_entry.append("items", component)
	stock_entry.append("items", finished_row)
	stock_entry.insert()
	set_final_component_batch_bundles(stock_entry, component_batch_nos)
	stock_entry.save()
	stock_entry.submit()
	link_final_manufacturing_stock_entry(doc, final_jobs, stock_entry.name, finished_qty)
	return stock_entry.name


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
			frappe.throw(
				_("Production Job {0} is linked to {1}, but {2} is linked to {3}. Resolve the posting traceability before reposting.").format(
					job.name, stock_entry, production, parent_stock_entry
				)
			)


def link_final_manufacturing_stock_entry(doc, final_jobs, stock_entry, finished_qty):
	stock_entry_name = stock_entry.name if hasattr(stock_entry, "name") else stock_entry
	doc.db_set("final_manufacturing_stock_entry", stock_entry_name, update_modified=False)
	if doc.meta.has_field("final_stock_posted_qty"):
		doc.db_set("final_stock_posted_qty", finished_qty, update_modified=False)
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	job_updates = {}
	if job_meta.has_field("manufacturing_stock_entry"):
		job_updates["manufacturing_stock_entry"] = stock_entry_name
	if job_meta.has_field("stock_posted_qty"):
		job_updates["stock_posted_qty"] = None
	if not job_updates:
		return
	for job in final_jobs:
		updates = dict(job_updates)
		if "stock_posted_qty" in updates:
			updates["stock_posted_qty"] = flt(job.stock_posting_qty)
		frappe.db.set_value("SmartEdge Production Job", job.name, updates, update_modified=False)


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
	components = get_final_stock_components(doc, finished_qty, source_warehouse)
	get_final_component_batch_nos(doc, components)
	finished_row = get_finished_stock_row(doc, finished_qty, target_warehouse)
	validate_batch_control([finished_row])


def get_final_finished_jobs(production):
	final_entry = get_final_mandatory_plan_entry(production)
	job_meta = frappe.get_meta("SmartEdge Production Job", cached=True)
	fields = ["name", "good_qty", "actual_qty_meter"]
	for fieldname in ("manufacturing_stock_entry", "stock_posted_qty"):
		if job_meta.has_field(fieldname):
			fields.append(fieldname)
	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"plan_entry": final_entry.name, "status": "Completed"},
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
	for label, item_code, consumption in (
		(_("Compound"), doc.get("compound_item"), get_final_component_consumption_per_meter(doc, "compound")),
		(_("Base Colour / Shade"), doc.get("base_colour_item"), get_final_component_consumption_per_meter(doc, "shade")),
	):
		if not item_code:
			frappe.throw(_("{0} stock item is required before creating final manufacturing Stock Entry.").format(label))
		if flt(consumption) <= 0:
			frappe.throw(_("{0} consumption per meter must be greater than zero.").format(label))
		item = validate_stock_item(item_code)
		qty = flt(consumption) * flt(finished_qty)
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
	for index, (label, table_field, batch_count_field, total_field) in enumerate(
		(
			(_("Compound"), "compound_batch_allocations", "compound_consumption_batches", "compound_total_required"),
			(
				_("Base Colour / Shade"),
				"shade_batch_allocations",
				"shade_consumption_batches",
				"shade_total_required",
			),
		)
	):
		component = components[index]
		item = validate_stock_item(component["item_code"])
		if not item.has_batch_no:
			batch_nos_by_row.append({})
			continue

		allocations = list(doc.get(table_field) or [])
		expected_count = cint(doc.get(batch_count_field))
		if expected_count <= 0 or len(allocations) != expected_count:
			frappe.throw(
				_("{0} FIFO batch allocation is missing or stale. Refresh the batch allocation before posting stock.").format(
					label
				)
			)

		batch_nos = [row.batch_no for row in allocations if row.batch_no]
		if len(batch_nos) != expected_count or len(set(batch_nos)) != expected_count:
			frappe.throw(_("{0} FIFO allocation must contain unique inventory Batches.").format(label))

		planned_qty = flt(doc.get(total_field))
		selected_qty = sum(flt(row.selected_qty) for row in allocations)
		if abs(planned_qty - selected_qty) > 0.000001:
			frappe.throw(
				_("{0} FIFO allocation quantity does not match the planned consumption quantity.").format(label)
			)

		batch_nos_by_row.append(
			get_revalidated_consumption_batches(
				component,
				allocations,
				label,
				item.stock_uom,
			)
		)

	return batch_nos_by_row


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
			_("{0} FIFO allocation is insufficient for the actual consumption quantity. Refresh the allocation.").format(
				label
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
			frappe.throw(
				_("Batch-controlled Item {0} requires explicit batch allocation before automatic Stock Entry posting.").format(
					row.get("item_code")
				)
			)


@frappe.whitelist()
def plan_full_production(production, start_date, start_time="09:00"):
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
	capacity = get_workstation_capacity_hours(workstation, planned_date)
	planned = get_workstation_planned_minutes(workstation, planned_date)
	return get_workstation_capacity_payload(workstation, planned_date, capacity, planned)


def get_workstation_capacity_payload(workstation, planned_date, capacity=None, planned=None):
	if not workstation or workstation == _("Unassigned") or not planned_date:
		return {"capacity_hours": 0, "planned_hours": 0, "utilization": 0, "configured": 0}
	planned_date = getdate(planned_date)
	capacity = get_workstation_capacity_hours(workstation, planned_date) if capacity is None else capacity
	planned = get_workstation_planned_minutes(workstation, planned_date) if planned is None else planned
	return {
		"workstation": workstation,
		"date": str(planned_date),
		"capacity_hours": capacity,
		"planned_hours": planned / 60,
		"utilization": (planned / 60 / capacity * 100) if capacity else 0,
		"configured": 1 if capacity else 0,
	}


@frappe.whitelist()
def get_start_job_context(plan_entry):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("read")
	production = frappe.db.get_value(
		"Final EB BOM Generator",
		entry.final_eb_bom_generator,
		["company", "finished_item", "finished_item_name"],
		as_dict=True,
	)
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
		get_current_employee,
		get_current_shift,
	)

	company = production.company if production else None
	operator_employee = get_current_employee(company)
	if operator_employee and not can_read_employee(operator_employee):
		operator_employee = None
	pending_quantity = get_remaining_or_planned_quantity(entry)
	return {
		"production_definition": entry.final_eb_bom_generator,
		"finished_item": (production.finished_item_name or production.finished_item) if production else None,
		"operation": entry.operation,
		"workstation": entry.workstation,
		"compatible_workstations": get_compatible_workstations(entry.operation, entry.operation_row),
		"company": company,
		"operator_employee": operator_employee,
		"operator_name": frappe.db.get_value("Employee", operator_employee, "employee_name") if operator_employee else None,
		"shift": get_current_shift(operator_employee) if operator_employee else None,
		"planned_qty_meter": pending_quantity.get("meter"),
		"planned_qty_kg": pending_quantity.get("kg"),
		"planned_start": entry.planned_start_datetime,
		"can_edit_workstation": entry.has_permission("write"),
	}


@frappe.whitelist()
def get_complete_job_context(plan_entry):
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
		["finished_item", "finished_item_name", "stock_uom"],
		as_dict=True,
	)
	return {
		"production_job": job.name,
		"operation": job.operation,
		"finished_item": (production.finished_item_name or production.finished_item) if production else None,
		"workstation": job.workstation,
		"operator_employee": job.operator_employee,
		"operator_name": get_employee_display_name(job.operator_employee, job.operator_name),
		"planned_qty_meter": job.planned_qty_meter,
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
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
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
	next_entry = get_next_mandatory_entry(source_entry)
	if not next_entry:
		return
	eligible_qty_meter = flt(job.good_qty) or flt(job.actual_qty_meter)
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
	entry.check_permission("write")
	validate_employee(operator_employee, frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company"))
	workstation = workstation or entry.workstation
	validate_selected_workstation(entry, workstation)
	validate_start_eligible(entry)
	if entry.planned_start_datetime and entry.planned_end_datetime:
		validate_capacity(workstation, entry.name, entry.planned_start_datetime, entry.planned_end_datetime)
	else:
		validate_workstation_job_capacity(workstation, plan_entry=entry.name)
	lock_plan_entry(plan_entry)
	entry.reload()
	validate_start_eligible(entry)
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
	if existing:
		job = frappe.get_doc("SmartEdge Production Job", existing)
		job.check_permission("write")
		if job.status in {"Completed", "Cancelled"}:
			frappe.throw(_("Completed or cancelled Production Jobs cannot be restarted."))
		if job.status in {"Draft", "Paused", "On Hold"}:
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
			job.save()
			job.sync_plan_entry("In Progress")
			entry.reload()
		elif job.status == "In Progress":
			if not job.operator_employee:
				job.operator_employee = operator_employee
				job.save()
			if job.workstation != workstation:
				frappe.throw(_("Production Job {0} is already in progress on Workstation {1}.").format(job.name, job.workstation))
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
	job.planned_qty_meter = pending_quantity.get("meter")
	job.planned_qty_kg = pending_quantity.get("kg")
	apply_released_quantity_to_job(job, entry)
	job.operator_employee = operator_employee
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import get_current_shift

	job.shift = shift or get_current_shift(operator_employee)
	job.actual_start = now_datetime()
	if job.meta.has_field("current_timer_start"):
		job.current_timer_start = job.actual_start
	job.status = "In Progress"
	job.insert()
	if frappe.get_meta("SmartEdge Production Plan Entry", cached=True).has_field("production_job"):
		entry.production_job = job.name
	entry.status = "In Progress"
	entry.save()
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
	job.actual_qty_meter = source_job.actual_qty_meter if flt(entry.get("released_qty_meter")) else 0
	job.actual_qty_kg = source_job.actual_qty_kg if flt(entry.get("released_qty_kg")) else 0
	job.good_qty = source_job.good_qty if flt(entry.get("released_qty_kg")) else 0


def validate_start_eligible(entry):
	if entry.status in {"Completed", "Cancelled"}:
		frappe.throw(_("Completed or cancelled operations cannot be started."))
	validate_predecessor_release(entry)
	if not entry.workstation:
		frappe.throw(_("Schedule the operation on a Workstation before starting production."))
	if not entry.planned_date or not entry.planned_start_datetime:
		frappe.throw(_("Schedule the operation before starting production."))


def validate_predecessor_release(entry):
	previous_entry = get_previous_mandatory_entry(entry)
	if not previous_entry:
		return
	if flt(entry.get("released_qty_meter")) or flt(entry.get("released_qty_kg")):
		return
	frappe.throw(
		_("Cannot start {0} because the required preceding operation has not released completed production quantity.").format(
			entry.operation
		)
	)


def validate_selected_workstation(entry, workstation):
	if not workstation:
		frappe.throw(_("Workstation is required."))
	validate_workstation_compatibility(entry.operation, entry.operation_row, workstation)


def lock_plan_entry(plan_entry):
	frappe.db.sql(
		"select name from `tabSmartEdge Production Plan Entry` where name=%s for update",
		(plan_entry,),
	)


@frappe.whitelist()
def pause_production_job(plan_entry, downtime_reason=None, downtime_remarks=None):
	job = get_open_job(plan_entry)
	lock_production_job(job.name)
	job.reload()
	job.pause_job(downtime_reason=downtime_reason, downtime_remarks=downtime_remarks)
	return {"production_job": job.name, "status": job.status}


@frappe.whitelist()
def resume_production_job(plan_entry):
	job = get_open_job(plan_entry)
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
	rejected_qty=None,
	rework_qty=None,
	usable_scrap=None,
	unusable_scrap=None,
	remarks=None,
):
	job = get_open_job(plan_entry)
	lock_production_job(job.name)
	job.reload()
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
			job.set(fieldname, flt(value) if fieldname != "remarks" else value)
	completion_type = completion_type or "Full Completion"
	validate_final_completion_stock_entry_setup(job, completion_type)
	job.complete_job(completion_type)
	return {"production_job": job.name, "status": job.status}


def lock_production_job(production_job):
	frappe.db.sql("select name from `tabSmartEdge Production Job` where name=%s for update", (production_job,))


def get_open_job(plan_entry):
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


def can_read_employee(employee):
	try:
		frappe.get_doc("Employee", employee).check_permission("read")
	except frappe.PermissionError:
		return False
	return True
