from collections import defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, get_time, getdate, now_datetime, nowdate

PLANNING_STATUSES = ("Pending", "Planned", "In Progress", "Completed", "On Hold", "Cancelled")

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

	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters=entry_filters,
		fields=[
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
		],
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
			if key != "name":
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
		fields=["name", "plan_entry", "status", "operator_employee", "operator_name", "shift", "actual_start"],
		order_by="modified desc",
		limit_page_length=1000,
	)
	by_entry = {}
	for job in job_rows:
		by_entry.setdefault(job.plan_entry, job)
	for row in rows:
		job = by_entry.get(row.name)
		if not job:
			continue
		row.production_job = job.name
		row.job_status = job.status
		row.operator_employee = job.operator_employee
		row.operator_name = job.operator_name
		row.shift = job.shift
		row.job_actual_start = job.actual_start


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
	return {
		"id": row.name,
		"production": row.final_eb_bom_generator,
		"operation_row": row.operation_row,
		"operation": row.operation,
		"operation_sequence": row.sequence,
		"workstation": row.workstation,
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
	}


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
	if not workstations:
		workstations = frappe.get_all("Workstation", filters={"disabled": 0}, pluck="name", order_by="name")
	return workstations


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
	if not frappe.db.exists("Workstation", workstation):
		frappe.throw(_("Workstation {0} does not exist.").format(workstation))
	if workstation in get_compatible_workstations(operation, operation_row):
		return
	frappe.throw(_("Workstation {0} is not configured for Operation {1}.").format(workstation, operation))


def combine_date_time(date_value, time_value):
	date_value = getdate(date_value)
	if isinstance(time_value, str) and len(time_value.split(":")) == 2:
		time_value = f"{time_value}:00"
	return get_datetime(datetime.combine(date_value, get_time(time_value or time(9, 0))))


def get_entry_default_duration(entry):
	if entry.operation_row:
		standard_time, time_uom = frappe.db.get_value(
			"Final EB BOM Operation", entry.operation_row, ["standard_time", "time_uom"]
		) or (0, "Minutes")
		if flt(standard_time):
			return flt(standard_time) if time_uom == "Hours" else flt(standard_time) / 60
	return 1


def validate_sequence(entry, start_dt, override=False):
	previous = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": entry.final_eb_bom_generator,
			"sequence": ["<", entry.sequence],
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "operation", "planned_end_datetime", "sequence"],
		order_by="sequence asc",
	)
	for row in previous:
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


def validate_capacity(workstation, plan_entry, start_dt, end_dt, override=False):
	capacity_hours = get_workstation_capacity_hours(workstation, getdate(start_dt))
	planned = get_workstation_planned_minutes(workstation, getdate(start_dt), plan_entry)
	new_minutes = (end_dt - start_dt).total_seconds() / 60
	overlaps = get_overlapping_entries(workstation, start_dt, end_dt, plan_entry)
	if overlaps and not override:
		frappe.throw(_("{0} already has a planned entry in this slot: {1}.").format(workstation, ", ".join(overlaps)))
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
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"final_eb_bom_generator": production, "status": ["!=", "Cancelled"]},
		fields=["status"],
	)
	if not rows:
		return
	statuses = [row.status for row in rows]
	if all(status == "Completed" for status in statuses):
		status = "Completed"
	elif any(status == "In Progress" for status in statuses):
		status = "In Production"
	elif all(status in {"Planned", "Completed"} for status in statuses):
		status = "Planned"
	elif any(status in {"Planned", "In Progress", "Completed"} for status in statuses):
		status = "Partially Planned"
	else:
		status = "Ready for Planning"
	frappe.db.set_value("Final EB BOM Generator", production, "status", status, update_modified=False)


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
	company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
		get_current_employee,
		get_current_shift,
	)

	operator_employee = get_current_employee(company)
	return {
		"operation": entry.operation,
		"workstation": entry.workstation,
		"company": company,
		"operator_employee": operator_employee,
		"operator_name": frappe.db.get_value("Employee", operator_employee, "employee_name") if operator_employee else None,
		"shift": get_current_shift(operator_employee) if operator_employee else None,
		"planned_qty_meter": entry.planned_meter,
		"planned_qty_kg": entry.planned_kg,
	}


@frappe.whitelist()
def start_production_job(plan_entry, operator_employee=None, shift=None):
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	entry.check_permission("write")
	if not entry.workstation:
		frappe.throw(_("Schedule the operation on a Workstation before starting production."))
	validate_workstation_compatibility(entry.operation, entry.operation_row, entry.workstation)
	existing = frappe.db.get_value(
		"SmartEdge Production Job",
		{"plan_entry": plan_entry, "status": ["!=", "Cancelled"]},
		"name",
		order_by="modified desc",
	)
	if existing:
		job = frappe.get_doc("SmartEdge Production Job", existing)
		job.check_permission("write")
		if operator_employee and not job.operator_employee:
			validate_employee(operator_employee, job.company)
			job.operator_employee = operator_employee
		if shift and not job.shift:
			job.shift = shift
		if job.status in {"Draft", "Paused", "On Hold"}:
			job.start_job()
			entry.reload()
		return {"production_job": existing, "existing": 1, "requires_operator": 0 if job.operator_employee else 1}
	job = frappe.new_doc("SmartEdge Production Job")
	job.plan_entry = entry.name
	job.final_eb_bom_generator = entry.final_eb_bom_generator
	job.company = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "company")
	job.operation = entry.operation
	job.workstation = entry.workstation
	job.planned_start = entry.planned_start_datetime
	job.planned_end = entry.planned_end_datetime
	job.planned_qty_meter = entry.planned_meter
	job.planned_qty_kg = entry.planned_kg
	from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job.smartedge_production_job import (
		get_current_employee,
		get_current_shift,
	)

	job.operator_employee = operator_employee or get_current_employee(job.company)
	if job.operator_employee:
		validate_employee(job.operator_employee, job.company)
		job.shift = get_current_shift(job.operator_employee)
		if shift:
			job.shift = shift
		job.actual_start = now_datetime()
		job.status = "In Progress"
	else:
		job.status = "Draft"
	job.insert()
	if frappe.get_meta("SmartEdge Production Plan Entry", cached=True).has_field("production_job"):
		entry.production_job = job.name
	if job.status == "In Progress":
		entry.status = "In Progress"
		entry.save()
		update_generator_planning_status(entry.final_eb_bom_generator)
	else:
		entry.save()
	return {"production_job": job.name, "existing": 0, "requires_operator": 0 if job.operator_employee else 1}


@frappe.whitelist()
def pause_production_job(plan_entry):
	job = get_open_job(plan_entry)
	job.pause_job()
	return {"production_job": job.name, "status": job.status}


@frappe.whitelist()
def complete_production_job(plan_entry, actual_qty_meter=None, actual_qty_kg=None, good_qty=None):
	job = get_open_job(plan_entry)
	if actual_qty_meter is not None:
		job.actual_qty_meter = flt(actual_qty_meter)
	if actual_qty_kg is not None:
		job.actual_qty_kg = flt(actual_qty_kg)
	if good_qty is not None:
		job.good_qty = flt(good_qty)
	job.complete_job()
	return {"production_job": job.name, "status": job.status}


def get_open_job(plan_entry):
	name = frappe.db.get_value(
		"SmartEdge Production Job",
		{"plan_entry": plan_entry, "status": ["!=", "Cancelled"]},
		"name",
		order_by="modified desc",
	)
	if not name:
		frappe.throw(_("No active Production Job found for this planned operation."))
	job = frappe.get_doc("SmartEdge Production Job", name)
	job.check_permission("write")
	return job


def validate_employee(employee, company=None):
	if not employee:
		frappe.throw(_("Operator is required."))
	filters = {"name": employee, "status": "Active"}
	if company and frappe.get_meta("Employee", cached=True).has_field("company"):
		filters["company"] = company
	if not frappe.db.exists("Employee", filters):
		frappe.throw(_("Select an active Employee for this Company."))
