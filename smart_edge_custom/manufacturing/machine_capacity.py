from __future__ import annotations

from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_time, getdate
from erpnext.stock.doctype.item.item import get_uom_conv_factor


CAPACITY_DOCTYPE = "SmartEdge Workstation Capacity Rate"
CAPACITY_BY_CATEGORY = {
	"Extrusion": ("Kg/Hour", "planned_kg", "Kg"),
	"Mixing": ("Kg/Hour", "planned_kg", "Kg"),
	"Printing": ("Meter/Hour", "planned_meter", "Meter"),
	"Slitting": ("Meter/Hour", "planned_meter", "Meter"),
	"Packing": ("Meter/Hour", "planned_meter", "Meter"),
}
NON_CAPACITY_CATEGORIES = {"QC"}
CAPACITY_UNITS = {
	"Kg/Hour": ("planned_kg", "Kg"),
	"Meter/Hour": ("planned_meter", "Meter"),
}
ACTIVE_PLAN_STATUSES = ("Ready", "Planned", "In Progress", "On Hold")
ACTIVE_WORK_ORDER_STATUSES = ("Not Started", "In Process")


def _value(row, fieldname):
	if isinstance(row, dict):
		return row.get(fieldname)
	return getattr(row, fieldname, None)


def _normalize_product_size(value):
	return " ".join(str(value or "").strip().lower().split())


def _request_cache():
	cache = getattr(frappe.local, "smartedge_machine_capacity", None)
	if cache is None:
		cache = frappe.local.smartedge_machine_capacity = {}
	return cache


def clear_capacity_request_cache():
	"""Discard only request-local capacity lookups after a configuration write."""
	try:
		frappe.local.smartedge_machine_capacity = {}
	except AttributeError:
		pass


def capacity_table_exists():
	cache = _request_cache()
	if "table_exists" not in cache:
		cache["table_exists"] = bool(frappe.db.table_exists(CAPACITY_DOCTYPE))
	return cache["table_exists"]


def _get_workstation_capacity_rows(workstations):
	"""Return size-specific rows plus a workstation default.

	The child table is authoritative for new configurations. Legacy parent
	fields remain readable until the migration patch has been reviewed.
	"""
	workstations = sorted({value for value in workstations if value})
	if not workstations or not capacity_table_exists():
		return []
	parents = frappe.get_all(
		CAPACITY_DOCTYPE,
		filters={"workstation": ["in", workstations], "enabled": 1},
		fields=["name", "workstation", "operation", "company", "product_size", "product_item", "process_route", "capacity_rate", "capacity_uom"],
		limit_page_length=5000,
	)
	rows = []
	for parent in parents:
		try:
			child_rows = frappe.get_all(
				"SmartEdge Workstation Capacity Rate Row",
				filters={"parent": parent.name, "parenttype": CAPACITY_DOCTYPE, "parentfield": "capacity_rows"},
				fields=["name", "product_size", "capacity_rate", "capacity_uom"],
				limit_page_length=500,
			)
		except Exception:
			child_rows = []
		if child_rows:
			rows.extend(
				{
					**dict(row),
					"workstation": parent.workstation,
					"operation": parent.operation,
					"company": parent.company,
					"product_item": parent.product_item,
					"process_route": parent.process_route,
					"parent": parent.name,
				}
				for row in child_rows
			)
			if parent.capacity_rate and parent.capacity_uom:
				rows.append({
					"name": parent.name,
					"workstation": parent.workstation,
					"operation": parent.operation,
					"product_size": None,
					"capacity_rate": parent.capacity_rate,
					"capacity_uom": parent.capacity_uom,
					"company": parent.company,
					"product_item": parent.product_item,
					"process_route": parent.process_route,
					"parent": parent.name,
				})
		else:
			rows.append(parent)
	return rows


def get_expected_capacity(category):
	return CAPACITY_BY_CATEGORY.get(category)


def _capacity_context(row):
	return {
		"operation": _value(row, "operation"),
		"product_size": _value(row, "custom_size"),
		"product_item": _value(row, "material_item") or _value(row, "finished_item"),
		"process_route": _value(row, "selected_route"),
		"company": _value(row, "company"),
	}


def _select_capacity_rate_for_unit(rows, row, workstation, capacity_uom):
	context = _capacity_context(row)
	return select_capacity_rate(
		[
			candidate
			for candidate in rows
			if _value(candidate, "workstation") == workstation
			and _value(candidate, "capacity_uom") == capacity_uom
		],
		**context,
	)


def _uom_family(value):
	value = str(value or "").strip().lower()
	if value in {"kg", "kilogram", "kilograms", "g", "gram", "grams", "mg", "lb", "lbs", "pound", "pounds"}:
		return "Kg"
	if value in {"m", "meter", "meters", "metre", "metres"}:
		return "Meter"
	return None


def _infer_capacity_definition(row, workstation, rows):
	category = _value(row, "planning_category")
	expected = get_expected_capacity(category)
	if expected:
		return {"capacity_uom": expected[0], "quantity_field": expected[1], "quantity_uom": expected[2]}
	if category in NON_CAPACITY_CATEGORIES:
		return None

	# Custom categories are supported when the configured master has one
	# unambiguous capacity unit. If both units are configured, use the item's
	# authoritative UOM where it identifies the unit; otherwise fail closed.
	family = _uom_family(_value(row, "quantity_uom") or _value(row, "stock_uom"))
	unit_candidates = []
	for capacity_uom, (quantity_field, quantity_uom) in CAPACITY_UNITS.items():
		if _select_capacity_rate_for_unit(rows, row, workstation, capacity_uom):
			unit_candidates.append((capacity_uom, quantity_field, quantity_uom))
	if family:
		family_candidates = [candidate for candidate in unit_candidates if candidate[2] == family]
		if family_candidates:
			unit_candidates = family_candidates
	if len(unit_candidates) == 1:
		capacity_uom, quantity_field, quantity_uom = unit_candidates[0]
		return {"capacity_uom": capacity_uom, "quantity_field": quantity_field, "quantity_uom": quantity_uom}
	return None


def _item_uom_to_target_factor(item_code, source_uom, target_uom):
	if not source_uom or not target_uom or source_uom == target_uom:
		return 1.0
	if item_code and frappe.db.exists("Item", item_code):
		item = frappe.db.get_value("Item", item_code, ["stock_uom"], as_dict=True)
		stock_uom = item.stock_uom if item else None
		if stock_uom:
			def to_stock(uom):
				if uom == stock_uom:
					return 1.0
				factor = frappe.db.get_value(
					"UOM Conversion Detail",
					{"parent": item_code, "uom": uom},
					"conversion_factor",
				)
				return flt(factor) or flt(get_uom_conv_factor(uom, stock_uom))

			source_to_stock = to_stock(source_uom)
			target_to_stock = to_stock(target_uom)
			if source_to_stock > 0 and target_to_stock > 0:
				return source_to_stock / target_to_stock

	# Fall back to the standard UOM conversion table when no item-specific
	# conversion is needed or the item does not have a usable conversion.
	factor = get_uom_conv_factor(target_uom, source_uom)
	return flt(1 / factor) if flt(factor) > 0 else 0


def _resolve_operation_quantity(row, quantity_uom):
	plan_type = _value(row, "plan_type")
	if plan_type == "Required Material":
		value = _value(row, "production_quantity")
		if flt(value) <= 0:
			value = _value(row, "planned_kg")
		if flt(value) <= 0:
			return None, _("Planned quantity is not available for this operation.")
		source_uom = _value(row, "quantity_uom")
		if not source_uom:
			return None, _("Quantity UOM is not configured for this material operation.")
		factor = _item_uom_to_target_factor(
			_value(row, "material_item"), source_uom, quantity_uom
		)
		if factor <= 0:
			return None, _(
				"Cannot convert the planned quantity from {0} to {1} for this operation."
			).format(source_uom, quantity_uom)
		return flt(value * factor), None

	if quantity_uom == "Kg":
		value = _value(row, "planned_kg")
		if flt(value) <= 0:
			value = _value(row, "total_eb_weight_kg")
	else:
		value = _value(row, "planned_meter")
		if flt(value) <= 0:
			value = _value(row, "production_quantity")
	if flt(value) <= 0:
		return None, _("Planned quantity is not available for this operation.")
	return flt(value), None


def _match_rank(row):
	operation = _value(row, "operation")
	product_size = _value(row, "product_size")
	product_item = _value(row, "product_item")
	process_route = _value(row, "process_route")
	if not operation:
		return 5
	if process_route and (product_size or product_item):
		return 1
	if product_size:
		return 2
	if product_item:
		return 3
	return 4


def select_capacity_rate(rows, operation=None, product_size=None, product_item=None, process_route=None, company=None):
	"""Select one rate using the Phase 7 specificity order.

	Rows supplied here are already scoped to one Workstation and one capacity unit.
	A tie at the same priority/specificity is deliberately rejected instead of
	choosing by creation order.
	"""
	eligible = []
	context = {
		"operation": operation,
		"product_size": product_size,
		"product_item": product_item,
		"process_route": process_route,
	}
	for row in rows:
		row_company = _value(row, "company")
		if row_company and row_company != company:
			continue
		if any(
			_value(row, fieldname)
			and (
				_normalize_product_size(_value(row, fieldname))
				!= _normalize_product_size(value)
				if fieldname == "product_size"
				else _value(row, fieldname) != value
			)
			for fieldname, value in context.items()
		):
			continue
		specificity = sum(bool(_value(row, fieldname)) for fieldname in context)
		company_specificity = 1 if row_company else 0
		eligible.append(((_match_rank(row), -specificity, -company_specificity), row))

	if not eligible:
		return None
	eligible.sort(key=lambda candidate: candidate[0])
	best_key = eligible[0][0]
	winners = [row for key, row in eligible if key == best_key]
	if len(winners) > 1:
		frappe.throw(
			_("Conflicting equally specific capacity rates found: {0}.").format(
				", ".join(str(_value(row, "name") or _value(row, "capacity_rate")) for row in winners)
			)
		)
	return winners[0]


def get_capacity_rate(
	workstation,
	capacity_uom,
	operation=None,
	product_size=None,
	product_item=None,
	process_route=None,
	company=None,
):
	if not workstation or not capacity_table_exists():
		return None
	cache_key = (
		"rate",
		workstation,
		capacity_uom,
		operation,
		product_size,
		product_item,
		process_route,
		company,
	)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]
	rows = _get_workstation_capacity_rows([workstation])
	rows = [row for row in rows if not row.get("capacity_uom") or row.get("capacity_uom") == capacity_uom]
	match = select_capacity_rate(rows, operation, product_size, product_item, process_route, company)
	cache[cache_key] = match
	return match


def get_capacity_rate_rows(workstations):
	workstations = sorted({workstation for workstation in workstations if workstation})
	if not workstations or not capacity_table_exists():
		return []
	cache_key = ("rate_rows", *workstations)
	cache = _request_cache()
	if cache_key not in cache:
		cache[cache_key] = _get_workstation_capacity_rows(workstations)
	return cache[cache_key]


def select_capacity_rate_for_row(rows, row, workstation, capacity_uom):
	return select_capacity_rate(
		[
			candidate
			for candidate in rows
			if _value(candidate, "workstation") == workstation
			and _value(candidate, "capacity_uom") == capacity_uom
		],
		operation=_value(row, "operation"),
		product_size=_value(row, "custom_size"),
		product_item=_value(row, "material_item") or _value(row, "finished_item"),
		process_route=_value(row, "selected_route"),
		company=_value(row, "company"),
	)


def merge_intervals(intervals):
	valid = sorted(
		(get_datetime(start), get_datetime(end))
		for start, end in intervals
		if start and end and get_datetime(end) > get_datetime(start)
	)
	merged = []
	for start, end in valid:
		if not merged or start > merged[-1][1]:
			merged.append([start, end])
		else:
			merged[-1][1] = max(merged[-1][1], end)
	return [(start, end) for start, end in merged]


def intersect_intervals(left, right):
	intersections = []
	for left_start, left_end in merge_intervals(left):
		for right_start, right_end in merge_intervals(right):
			start = max(left_start, right_start)
			end = min(left_end, right_end)
			if end > start:
				intersections.append((start, end))
	return merge_intervals(intersections)


def subtract_intervals(source, blocked):
	result = []
	blocked = merge_intervals(blocked)
	for source_start, source_end in merge_intervals(source):
		cursor = source_start
		for blocked_start, blocked_end in blocked:
			if blocked_end <= cursor or blocked_start >= source_end:
				continue
			if blocked_start > cursor:
				result.append((cursor, min(blocked_start, source_end)))
			cursor = max(cursor, blocked_end)
			if cursor >= source_end:
				break
		if cursor < source_end:
			result.append((cursor, source_end))
	return merge_intervals(result)


def interval_hours(intervals):
	return sum((end - start).total_seconds() for start, end in merge_intervals(intervals)) / 3600


def is_holiday(holiday_list, date_value):
	if not holiday_list:
		return False
	return bool(frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": getdate(date_value)}))


def get_workstation_schedule(workstation, date_value):
	date_value = getdate(date_value)
	doc = frappe.get_doc("Workstation", workstation)
	configured = bool(doc.get("working_hours"))
	if cint(doc.disabled):
		return [], configured, _("Workstation is disabled."), False
	if not configured:
		return [], False, _("Workstation working hours are not configured."), False
	if is_holiday(doc.holiday_list, date_value):
		return [], True, _("Workstation holiday."), True
	intervals = []
	for row in doc.working_hours:
		if not cint(row.enabled) or not row.start_time or not row.end_time:
			continue
		start = datetime.combine(date_value, get_time(row.start_time))
		end = datetime.combine(date_value, get_time(row.end_time))
		if end <= start:
			end += timedelta(days=1)
		intervals.append((start, end))
	if not intervals:
		return [], False, _("Workstation has no enabled working-hour intervals."), False
	return merge_intervals(intervals), True, None, False


def get_workstation_intervals_for_window(workstation, window_start, window_end, shift=None):
	"""Return configured machine intervals covering a requested datetime window.

	Workstation working-hour rows are date anchored, so include the previous day
	to recognize an overnight interval that is already in progress at midnight.
	The next day is included for a request that itself crosses midnight.
	"""
	window_start = get_datetime(window_start)
	window_end = get_datetime(window_end)
	first_date = window_start.date() - timedelta(days=1)
	last_date = window_end.date() + timedelta(days=1)
	configured_intervals = []
	available_intervals = []
	holidays = set()
	configured = False
	messages = []
	current_date = first_date
	while current_date <= last_date:
		daily, daily_configured, daily_message, daily_holiday = get_workstation_schedule(
			workstation, current_date
		)
		configured = configured or daily_configured
		if daily_holiday:
			holidays.add(str(current_date))
		if daily_message and daily_message not in messages:
			messages.append(daily_message)

		intervals = daily
		if shift:
			shift_intervals, shift_configured, shift_message, shift_holiday = get_shift_schedule(
				shift,
				current_date,
				frappe.db.get_value("Workstation", workstation, "holiday_list"),
			)
			if shift_configured:
				intervals = intersect_intervals(shift_intervals, daily) if daily_configured else shift_intervals
				configured = True
			if shift_message and shift_message not in messages:
				messages.append(shift_message)
			if shift_holiday:
				holidays.add(str(current_date))

		configured_intervals.extend(intervals)
		current_date += timedelta(days=1)

	anchor_date = window_start.date()
	configured_intervals = [
		(start, end)
		for start, end in merge_intervals(configured_intervals)
		if start.date() == anchor_date or (start < window_end and end > window_start)
	]
	if configured_intervals:
		downtime = get_downtime_intervals(workstation, window_start, window_end)
		available_intervals = subtract_intervals(configured_intervals, downtime)
	return {
		"configured": cint(configured),
		"configured_intervals": configured_intervals,
		"available_intervals": available_intervals,
		"holidays": sorted(holidays),
		"messages": messages,
	}


def _format_workstation_interval(interval):
	start, end = interval
	return f"{start.strftime('%H:%M:%S')}–{end.strftime('%H:%M:%S')}"


def validate_workstation_time_window(workstation, planned_date, planned_start, duration_hours, shift=None):
	"""Validate one unsplittable operation against actual machine intervals."""
	planned_date = getdate(planned_date)
	start_dt = datetime.combine(planned_date, get_time(planned_start or time(9, 0)))
	end_dt = start_dt + timedelta(hours=flt(duration_hours))
	window = get_workstation_intervals_for_window(workstation, start_dt, end_dt, shift=shift)
	configured_intervals = window["configured_intervals"]
	available_intervals = window["available_intervals"]
	interval_labels = [_format_workstation_interval(interval) for interval in configured_intervals]
	base = {
		"status": "not_configured",
		"message": None,
		"planned_start": start_dt.strftime("%H:%M:%S"),
		"planned_end": end_dt.strftime("%H:%M:%S"),
		"working_intervals": interval_labels,
		"working_intervals_display": ", ".join(interval_labels),
		"shift": shift,
	}
	if end_dt.date() != planned_date:
		base["planned_end"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
	if not configured_intervals:
		if window["holidays"]:
			base.update(
				status="unavailable",
				message=_("Workstation is unavailable on the selected date."),
			)
		else:
			base["message"] = (
				window["messages"][0]
				if window["messages"]
				else _("Workstation working hours are not configured for the selected date.")
			)
		return base
	interval_end_date = (end_dt - timedelta(microseconds=1)).date() if end_dt > start_dt else planned_date
	interval_dates = set()
	current_date = planned_date
	while current_date <= interval_end_date:
		interval_dates.add(str(current_date))
		current_date += timedelta(days=1)
	if interval_dates.intersection(window["holidays"]):
		base.update(
			status="unavailable",
			message=_("Workstation is unavailable on one or more dates in the planned interval."),
		)
		return base

	if any(start <= start_dt and end_dt <= end for start, end in available_intervals):
		base["status"] = "ok"
		return base

	first_relevant = next(
		((start, end) for start, end in available_intervals if end > start_dt),
		None,
	)
	first_configured = configured_intervals[0]
	last_configured = configured_intervals[-1]
	if start_dt < first_configured[0]:
		base.update(
			status="before_open",
			message=_(
				"Planned Start is before the workstation's working hours. Available interval: {0}."
			).format(_format_workstation_interval(first_relevant or first_configured)),
		)
		return base
	start_interval = next(
		((start, end) for start, end in configured_intervals if start <= start_dt < end),
		None,
	)
	if start_dt >= last_configured[1] or (
		start_interval == last_configured and end_dt > last_configured[1]
	):
		base.update(
			status="after_close",
			message=_(
				"Workstation Working Hours Exceeded: {0} is available from {1}. The proposed operation ends at {2}."
			).format(
				workstation,
				base["working_intervals_display"],
				base["planned_end"],
			),
		)
		return base

	base.update(
		status="outside_working_hours",
		message=_(
			"The proposed operation does not fit inside one continuous workstation working interval. Available intervals: {0}."
		).format(base["working_intervals_display"]),
	)
	return base


def get_shift_schedule(shift, date_value, workstation_holiday_list=None):
	if not shift:
		return [], False, _("Shift is not configured."), False
	values = frappe.db.get_value("Shift Type", shift, ["start_time", "end_time", "holiday_list"], as_dict=True)
	if not values or not values.start_time or not values.end_time:
		return [], False, _("Shift {0} has invalid or missing start/end times.").format(shift), False
	date_value = getdate(date_value)
	holiday_list = values.holiday_list or workstation_holiday_list
	if is_holiday(holiday_list, date_value):
		return [], True, _("Shift holiday."), True
	intervals, message = make_shift_intervals(date_value, values.start_time, values.end_time)
	if message:
		return [], False, message, False
	return intervals, True, None, False


def make_shift_intervals(date_value, start_time, end_time):
	"""Build a site-local shift window; an end before start means overnight."""
	date_value = getdate(date_value)
	start = datetime.combine(date_value, get_time(start_time))
	end = datetime.combine(date_value, get_time(end_time))
	if end == start:
		return [], _("Shift duration must be greater than zero.")
	if end < start:
		end += timedelta(days=1)
	return [(start, end)], None


def get_downtime_intervals(workstation, window_start, window_end):
	rows = frappe.get_all(
		"Downtime Entry",
		filters={"workstation": workstation},
		fields=["name", "from_time", "to_time"],
		limit_page_length=5000,
	)
	return merge_intervals(
		(max(get_datetime(row.from_time), window_start), min(get_datetime(row.to_time), window_end))
		for row in rows
		if row.from_time
		and row.to_time
		and get_datetime(row.from_time) < window_end
		and get_datetime(row.to_time) > window_start
	)


def get_reservation_intervals(workstation, window_start, window_end):
	"""Return distinct active allocations without adding Production Job time twice.

	SmartEdge Production Jobs reference their plan entry, so the plan interval is
	the reservation. ERPNext Work Order operations are added only when no matching
	SmartEdge entry already represents the same Work Order operation.
	"""
	plan_rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"workstation": workstation, "status": ["in", ACTIVE_PLAN_STATUSES]},
		fields=["name", "work_order", "operation", "planned_start_datetime", "planned_end_datetime"],
		limit_page_length=5000,
	)
	reservations = []
	represented_work_order_operations = set()
	for row in plan_rows:
		if row.work_order and row.operation:
			represented_work_order_operations.add((row.work_order, row.operation))
		if not row.planned_start_datetime or not row.planned_end_datetime:
			continue
		start = get_datetime(row.planned_start_datetime)
		end = get_datetime(row.planned_end_datetime)
		if start < window_end and end > window_start:
			reservations.append((max(start, window_start), min(end, window_end), f"plan:{row.name}"))

	operation_rows = frappe.get_all(
		"Work Order Operation",
		filters={"workstation": workstation, "status": ["!=", "Completed"]},
		fields=["name", "parent", "operation", "planned_start_time", "planned_end_time"],
		limit_page_length=5000,
	)
	parents = list({row.parent for row in operation_rows if row.parent})
	active_parents = set()
	if parents:
		active_parents = {
			row.name
			for row in frappe.get_all(
				"Work Order",
				filters={"name": ["in", parents], "docstatus": 1, "status": ["in", ACTIVE_WORK_ORDER_STATUSES]},
				fields=["name"],
				limit_page_length=len(parents),
			)
		}
	for row in operation_rows:
		if row.parent not in active_parents or (row.parent, row.operation) in represented_work_order_operations:
			continue
		if not row.planned_start_time or not row.planned_end_time:
			continue
		start = get_datetime(row.planned_start_time)
		end = get_datetime(row.planned_end_time)
		if start < window_end and end > window_start:
			reservations.append((max(start, window_start), min(end, window_end), f"work-order-operation:{row.name}"))
	return reservations


def _reservation_hours(reservations, available_intervals):
	# Each distinct allocation consumes its own machine time. Only duplicate
	# representations of the same allocation are removed by the source query.
	result = {}
	for start, end, reference in reservations:
		result[reference] = result.get(reference, 0) + interval_hours(
			intersect_intervals([(start, end)], available_intervals)
		)
	return result


def get_capacity_availability(workstation, date_value, shift=None, rate=None):
	if not workstation or not date_value:
		return {
			"configured": 0,
			"message": _("Workstation and planned date are required for availability."),
		}
	date_value = getdate(date_value)
	cache_key = ("availability", workstation, str(date_value), shift)
	cache = _request_cache()
	if cache_key in cache:
		return add_rate_capacity(cache[cache_key], rate)

	workstation_doc = frappe.get_doc("Workstation", workstation)
	daily_schedule, daily_configured, daily_message, daily_holiday = get_workstation_schedule(workstation, date_value)
	day_start = datetime.combine(date_value, time.min)
	day_end = day_start + timedelta(days=1)
	window_end = max([day_end, *(end for _start, end in daily_schedule)], default=day_end)
	downtime = get_downtime_intervals(workstation, day_start, window_end)
	reservations = get_reservation_intervals(workstation, day_start, window_end)

	daily_blocked = intersect_intervals(daily_schedule, downtime)
	daily_net_intervals = subtract_intervals(daily_schedule, daily_blocked)
	daily_scheduled = interval_hours(daily_schedule)
	daily_downtime = interval_hours(daily_blocked)
	daily_net = interval_hours(daily_net_intervals)
	daily_reservation_hours = _reservation_hours(reservations, daily_net_intervals)
	daily_reserved = sum(daily_reservation_hours.values())
	daily_over = max(daily_reserved - daily_net, 0)
	daily_remaining = max(daily_net - daily_reserved, 0)

	shift_schedule, shift_configured, shift_message, shift_holiday = get_shift_schedule(
		shift, date_value, workstation_doc.holiday_list
	)
	if shift_configured and daily_configured:
		shift_schedule = intersect_intervals(shift_schedule, daily_schedule)
	shift_blocked = intersect_intervals(shift_schedule, downtime)
	shift_net_intervals = subtract_intervals(shift_schedule, shift_blocked)
	shift_scheduled = interval_hours(shift_schedule)
	shift_downtime = interval_hours(shift_blocked)
	shift_net = interval_hours(shift_net_intervals)
	shift_reservation_hours = _reservation_hours(reservations, shift_net_intervals)
	shift_reserved = sum(shift_reservation_hours.values())
	shift_over = max(shift_reserved - shift_net, 0)
	shift_remaining = max(shift_net - shift_reserved, 0)

	result = {
		"configured": cint(daily_configured),
		"message": daily_message,
		"holiday": cint(daily_holiday),
		"scheduled_hours": daily_scheduled,
		"downtime_hours": daily_downtime,
		"net_hours": daily_net,
		"reserved_hours": daily_reserved,
		"remaining_hours": daily_remaining,
		"overallocated_hours": daily_over,
		"reservation_hours_by_reference": daily_reservation_hours,
		"shift": shift,
		"shift_configured": cint(shift_configured),
		"shift_message": shift_message,
		"shift_holiday": cint(shift_holiday),
		"shift_scheduled_hours": shift_scheduled,
		"shift_downtime_hours": shift_downtime,
		"shift_net_hours": shift_net,
		"shift_reserved_hours": shift_reserved,
		"shift_remaining_hours": shift_remaining,
		"shift_overallocated_hours": shift_over,
		"shift_reservation_hours_by_reference": shift_reservation_hours,
	}
	cache[cache_key] = dict(result)
	return add_rate_capacity(result, rate)


def add_rate_capacity(availability, rate):
	result = dict(availability)
	rate = flt(rate)
	result.update(
		{
			"production_capacity": result.get("net_hours", 0) * rate if rate else None,
			"remaining_production_capacity": result.get("remaining_hours", 0) * rate if rate else None,
			"shift_production_capacity": result.get("shift_net_hours", 0) * rate if rate else None,
			"shift_remaining_production_capacity": (
				result.get("shift_remaining_hours", 0) * rate if rate else None
			),
		}
	)
	return result


def get_rate_payload(row, workstation=None, rate_rows=None):
	category = _value(row, "planning_category")
	workstation = workstation or _value(row, "workstation")
	if not workstation or workstation == _("Unassigned"):
		return {"applicable": 0}
	expected = get_expected_capacity(category)
	match = None
	if expected:
		definition = {
			"capacity_uom": expected[0],
			"quantity_field": expected[1],
			"quantity_uom": expected[2],
		}
		if rate_rows is None:
			match = get_capacity_rate(
				workstation,
				definition["capacity_uom"],
				operation=_value(row, "operation"),
				product_size=_value(row, "custom_size"),
				product_item=_value(row, "material_item") or _value(row, "finished_item"),
				process_route=_value(row, "selected_route"),
				company=_value(row, "company"),
			)
		else:
			match = _select_capacity_rate_for_unit(rate_rows, row, workstation, definition["capacity_uom"])
	else:
		if rate_rows is None:
			rate_rows = get_capacity_rate_rows([workstation])
		definition = _infer_capacity_definition(row, workstation, rate_rows)
	if not definition:
		if category in NON_CAPACITY_CATEGORIES:
			return {"applicable": 0}
		return {
			"applicable": 1,
			"configured": 0,
			"configuration_status": "Invalid Capacity Unit",
			"message": _(
				"Capacity unit is not configured unambiguously for this Workstation and operation."
			),
		}
	capacity_uom = definition["capacity_uom"]
	quantity_uom = definition["quantity_uom"]
	if match is None:
		match = _select_capacity_rate_for_unit(rate_rows or [], row, workstation, capacity_uom)
	planned_quantity, quantity_error = _resolve_operation_quantity(row, quantity_uom)
	base = {
		"applicable": 1,
		"capacity_uom": capacity_uom,
		"quantity_uom": quantity_uom,
		"planned_quantity": planned_quantity,
		"planned_quantity_available": cint(planned_quantity is not None and planned_quantity > 0),
		"planned_kg": flt(planned_quantity) if quantity_uom == "Kg" and planned_quantity else (
			flt(_value(row, "planned_kg")) if _value(row, "planned_kg") is not None else None
		),
		"planned_meter": (
			flt(planned_quantity) if quantity_uom == "Meter" and planned_quantity else (
				flt(_value(row, "planned_meter")) if _value(row, "planned_meter") is not None else None
			)
		),
	}
	if not match:
		base.update(
			{
				"configured": 0,
				"configuration_status": "Not Configured",
				"message": _("Capacity not configured for this Workstation and production specification."),
			}
		)
		return base
	rate = flt(_value(match, "capacity_rate"))
	if rate <= 0:
		base.update(
			{
				"configured": 0,
				"configuration_status": "Not Configured",
				"message": _("Capacity not configured for this Workstation and production specification."),
			}
		)
		return base
	if quantity_error and planned_quantity is None and "Planned quantity is not available" not in str(quantity_error):
		base.update(
			{
				"configured": 0,
				"configuration_status": "Invalid Quantity",
				"message": quantity_error,
			}
		)
		return base
	base.update(
		{
			"configured": 1,
			"configuration_status": "Configured",
			"configuration": _value(match, "name"),
			"capacity_rate": rate,
			"required_hours": planned_quantity / rate if planned_quantity else None,
			"planned_hours_status": "Available" if planned_quantity else "Not Available",
		}
	)
	return base


def get_scheduled_duration_hours(row):
	start = _value(row, "planned_start_datetime")
	end = _value(row, "planned_end_datetime")
	if not start or not end:
		return 0
	return max((get_datetime(end) - get_datetime(start)).total_seconds() / 3600, 0)


def get_operation_capacity_payload(row, workstation=None, rate_rows=None):
	workstation = workstation or _value(row, "workstation")
	payload = get_rate_payload(row, workstation, rate_rows)
	if not payload.get("configured"):
		return payload
	rate = payload["capacity_rate"]
	payload.update(
		get_capacity_availability(
			workstation,
			_value(row, "planned_date"),
			_value(row, "shift") or _value(row, "planned_shift"),
			rate,
		)
		if _value(row, "planned_date")
		else {
			"configured": 0,
			"message": _("Schedule the operation to calculate availability."),
			"shift_configured": 0,
			"shift_message": _("Shift is not configured."),
		}
	)
	# Preserve the fact that the rate is configured even when the calendar is not.
	payload["rate_configured"] = 1
	payload["availability_configured"] = payload.pop("configured", 0)
	payload["configured"] = 1
	return payload


def attach_board_capacity_details(rows):
	"""Attach Point 3 metrics with batched rates and shared context totals."""
	eligible = [
		row
		for row in rows
		if _value(row, "planning_category") not in NON_CAPACITY_CATEGORIES
		and (_value(row, "job_workstation") or _value(row, "workstation"))
	]
	if not eligible:
		return
	workstations = {_value(row, "job_workstation") or _value(row, "workstation") for row in eligible}
	rate_rows = get_capacity_rate_rows(workstations)
	visible_contexts = set()
	for row in eligible:
		workstation = _value(row, "job_workstation") or _value(row, "workstation")
		row.machine_capacity = get_rate_payload(row, workstation, rate_rows)
		if _value(row, "planned_date"):
			date_key = str(getdate(_value(row, "planned_date")))
			shift = _value(row, "shift") or _value(row, "planned_shift") or None
			visible_contexts.add((workstation, date_key, shift))
			visible_contexts.add((workstation, date_key, None))

	if not visible_contexts:
		return
	dates = [getdate(context[1]) for context in visible_contexts]
	allocation_rows = get_capacity_allocation_rows(workstations, min(dates), max(dates))
	allocation_by_name = prepare_capacity_allocations(allocation_rows, rate_rows)
	context_metrics = {
		context: get_context_capacity_metrics(context, allocation_by_name)
		for context in visible_contexts
	}

	for row in eligible:
		if not _value(row, "planned_date"):
			continue
		workstation = _value(row, "job_workstation") or _value(row, "workstation")
		date_key = str(getdate(_value(row, "planned_date")))
		shift = _value(row, "shift") or _value(row, "planned_shift") or None
		metrics = context_metrics[(workstation, date_key, shift)]
		daily_metrics = context_metrics[(workstation, date_key, None)]
		own_hours = flt(_value(allocation_by_name.get(_value(row, "name"), {}), "allocation_hours"))
		calculation_configured = cint(metrics["allocation_configured"])
		other_hours = (
			max(metrics["total_allocated_hours"] - own_hours, 0)
			if calculation_configured
			else None
		)
		remaining_hours = (
			max(metrics["net_available_hours"] - other_hours, 0)
			if calculation_configured
			else None
		)
		payload = row.machine_capacity
		rate_configured = cint(payload.get("configured"))
		rate_message = payload.get("message")
		payload.update(metrics["availability"])
		payload["configured"] = rate_configured
		payload["rate_configured"] = rate_configured
		if not rate_configured:
			payload["message"] = rate_message
		payload["availability_configured"] = cint(
			metrics["availability_configured"] and calculation_configured
		)
		payload.update(
			{
				"workstation": workstation,
				"operation": _value(row, "operation"),
				"product_size": _value(row, "custom_size"),
				"scheduled_date": date_key,
				"shift": shift,
				"net_available_hours": metrics["net_available_hours"],
				"other_allocated_hours": other_hours,
				"total_allocated_hours": metrics["total_allocated_hours"],
				"remaining_available_hours": remaining_hours,
				"overallocated_hours": metrics["overallocated_hours"],
				"workstation_utilization": metrics["utilization"],
				"capacity_calculation_configured": calculation_configured,
				"calculation_message": metrics["calculation_message"],
				"operation_capacity_share": (
					own_hours / metrics["net_available_hours"] * 100
					if calculation_configured and metrics["net_available_hours"] > 0
					else None
				),
				"available_capacity": (
					remaining_hours * flt(payload.get("capacity_rate"))
					if rate_configured and metrics["availability_configured"] and calculation_configured
					else None
				),
				"daily_summary": {
					"net_available_hours": daily_metrics["net_available_hours"],
					"allocated_hours": daily_metrics["total_allocated_hours"],
					"remaining_hours": (
						max(
							daily_metrics["net_available_hours"] - daily_metrics["total_allocated_hours"], 0
						)
						if daily_metrics["allocation_configured"]
						else None
					),
					"overallocated_hours": daily_metrics["overallocated_hours"],
					"utilization": daily_metrics["utilization"],
					"configured": cint(
						daily_metrics["availability"].get("configured", 0)
						and daily_metrics["allocation_configured"]
					),
					"message": (
						daily_metrics["calculation_message"]
						or daily_metrics["availability"].get("message")
					),
				},
			}
		)
		payload.pop("reservation_hours_by_reference", None)
		payload.pop("shift_reservation_hours_by_reference", None)


def get_capacity_allocation_rows(workstations, date_from, date_to):
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters=[
			["workstation", "in", list(workstations)],
			["planned_date", "between", [date_from, date_to]],
			["status", "in", ACTIVE_PLAN_STATUSES],
		],
		fields=[
			"name",
			"creation",
			"final_eb_bom_generator",
			"plan_type",
			"material_item",
			"production_quantity",
			"quantity_uom",
			"operation_row",
			"operation",
			"sequence",
			"workstation",
			"planning_category",
			"planned_date",
			"planned_start_datetime",
			"planned_end_datetime",
			"planned_meter",
			"planned_kg",
			"planned_shift",
		],
		limit_page_length=5000,
	)
	rows = remove_superseded_allocation_rows(rows)
	generator_names = list({_value(row, "final_eb_bom_generator") for row in rows if row.final_eb_bom_generator})
	if not generator_names:
		return rows
	generators = {
		row.name: row
		for row in frappe.get_all(
			"Final EB BOM Generator",
			filters={"name": ["in", generator_names]},
			fields=["name", "company", "finished_item", "custom_size", "selected_route", "stock_uom"],
			limit_page_length=len(generator_names),
		)
	}
	for row in rows:
		generator = generators.get(row.final_eb_bom_generator)
		if generator:
			for fieldname in ("company", "finished_item", "custom_size", "selected_route", "stock_uom"):
				row[fieldname] = generator.get(fieldname)
	return rows


def remove_superseded_allocation_rows(rows):
	"""Keep the newest active representation of one production operation."""
	by_operation = {}
	for row in rows:
		operation_identity = _value(row, "operation_row") or (
			_value(row, "operation"),
			cint(_value(row, "sequence")),
		)
		key = (_value(row, "final_eb_bom_generator"), operation_identity)
		current = by_operation.get(key)
		candidate_order = (get_datetime(_value(row, "creation")), str(_value(row, "name") or ""))
		current_order = (
			(get_datetime(_value(current, "creation")), str(_value(current, "name") or ""))
			if current
			else None
		)
		if not current or candidate_order > current_order:
			by_operation[key] = row
	return list(by_operation.values())


def prepare_capacity_allocations(rows, rate_rows):
	result = {}
	for row in rows:
		payload = get_rate_payload(row, row.workstation, rate_rows)
		applicable = cint(payload.get("applicable"))
		required_hours = payload.get("required_hours") if payload.get("configured") else None
		capacity_configured = cint(not applicable or required_hours is not None)
		scheduled_hours = get_scheduled_duration_hours(row)
		result[row.name] = frappe._dict(
			name=row.name,
			workstation=row.workstation,
			planned_date=str(getdate(row.planned_date)),
			shift=row.planned_shift or None,
			capacity_applicable=applicable,
			capacity_configured=capacity_configured,
			allocation_hours=(
				scheduled_hours
				if scheduled_hours > 0 and (not applicable or required_hours is not None)
				else (
					flt(required_hours)
					if required_hours is not None
					else (scheduled_hours if not applicable else None)
				)
			),
		)
	return result


def get_context_capacity_metrics(context, allocation_by_name, excluded_plan_entries=None):
	workstation, date_key, shift = context
	excluded_plan_entries = set(excluded_plan_entries or ())
	availability = get_capacity_availability(workstation, getdate(date_key), shift)
	reservation_hours = (
		availability.get("shift_reservation_hours_by_reference", {})
		if shift
		else availability.get("reservation_hours_by_reference", {})
	)
	included = {
		name
		for name, allocation in allocation_by_name.items()
		if name not in excluded_plan_entries
		and allocation.workstation == workstation
		and allocation.planned_date == date_key
		and (not shift or allocation.shift == shift)
	}
	unconfigured = {
		name
		for name in included
		if _value(allocation_by_name[name], "capacity_configured") is not None
		and not cint(_value(allocation_by_name[name], "capacity_configured"))
	}
	total = sum(flt(allocation_by_name[name].allocation_hours) for name in included)
	for reference, hours in reservation_hours.items():
		if reference.startswith("plan:") and reference.removeprefix("plan:") in excluded_plan_entries:
			continue
		if reference.startswith("plan:") and reference.removeprefix("plan:") in included:
			continue
		total += flt(hours)
	if shift:
		net_hours = flt(availability.get("shift_net_hours"))
		availability_configured = cint(availability.get("shift_configured"))
	else:
		net_hours = flt(availability.get("net_hours"))
		availability_configured = cint(availability.get("configured"))
	allocation_configured = not unconfigured
	return {
		"net_available_hours": net_hours,
		"known_allocated_hours": total,
		"total_allocated_hours": total if allocation_configured else None,
		"overallocated_hours": max(total - net_hours, 0) if allocation_configured else None,
		"utilization": total / net_hours * 100 if allocation_configured and net_hours > 0 else None,
		"allocation_configured": cint(allocation_configured),
		"unconfigured_plan_entries": sorted(unconfigured),
		"calculation_message": (
			None
			if allocation_configured
			else _("Capacity not configured for one or more planned operations in this Workstation schedule.")
		),
		"availability_configured": availability_configured,
		"availability": dict(availability),
	}


def get_schedule_capacity_projection(entry, workstation, planned_date, shift=None, duration_hours=None):
	"""Calculate the destination impact of moving one planning operation.

	The current entry is removed from the destination totals before its proposed
	allocation is added. This makes moves within the same bucket neutral while
	moves into another bucket show their actual incremental impact.
	"""
	planned_date = getdate(planned_date)
	shift = shift or _value(entry, "planned_shift") or None

	as_dict = getattr(entry, "as_dict", None)
	row = frappe._dict(as_dict() if callable(as_dict) else dict(entry))
	generator_name = _value(row, "final_eb_bom_generator")
	if generator_name:
		generator = frappe.db.get_value(
			"Final EB BOM Generator",
			generator_name,
			["company", "finished_item", "custom_size", "selected_route", "stock_uom"],
			as_dict=True,
		)
		if generator:
			for fieldname in ("company", "finished_item", "custom_size", "selected_route", "stock_uom"):
				row[fieldname] = generator.get(fieldname)

	rate_rows = get_capacity_rate_rows([workstation])
	rate_payload = get_rate_payload(row, workstation, rate_rows)
	if not rate_payload.get("applicable"):
		return {
			"status": "not_applicable",
			"requires_confirmation": 0,
			"workstation": workstation,
			"planned_date": str(planned_date),
			"shift": shift,
		}
	if not rate_payload.get("configured") or rate_payload.get("required_hours") is None:
		configuration_status = rate_payload.get("configuration_status", "Not Configured")
		if configuration_status == "Not Configured":
			status = "not_configured"
		elif configuration_status == "Invalid Quantity":
			status = "invalid_quantity"
		elif configuration_status == "Invalid Capacity Unit":
			status = "invalid_capacity_unit"
		elif not rate_payload.get("planned_quantity_available"):
			status = "quantity_not_available"
		else:
			status = configuration_status.lower().replace(" ", "_")
		return _unverified_projection(workstation, planned_date, shift, rate_payload, status=status)

	allocation_rows = get_capacity_allocation_rows([workstation], planned_date, planned_date)
	allocations = prepare_capacity_allocations(allocation_rows, rate_rows)
	context = (workstation, str(planned_date), shift)
	entry_name = _value(entry, "name")
	metrics = get_context_capacity_metrics(context, allocations, excluded_plan_entries={entry_name})
	if not metrics["availability_configured"]:
		return _unverified_projection(
			workstation,
			planned_date,
			shift,
			rate_payload,
			metrics["availability"],
			status="availability_not_configured",
		)
	if not metrics["allocation_configured"]:
		return _unverified_projection(
			workstation,
			planned_date,
			shift,
			rate_payload,
			configuration_message=metrics["calculation_message"],
			status="schedule_not_configured",
		)

	other_hours = 0
	for name, allocation in allocations.items():
		if name == entry_name:
			continue
		if allocation.workstation != workstation or allocation.planned_date != str(planned_date):
			continue
		if shift and allocation.shift != shift:
			continue
		other_hours += flt(allocation.allocation_hours)

	# Account for reservations not represented by a SmartEdge allocation.
	reservation_key = "shift_reservation_hours_by_reference" if shift else "reservation_hours_by_reference"
	for reference, hours in metrics["availability"].get(reservation_key, {}).items():
		if reference == f"plan:{entry_name}":
			continue
		if reference.startswith("plan:") and reference.removeprefix("plan:") in allocations:
			continue
		other_hours += flt(hours)

	required_hours = flt(rate_payload["required_hours"])
	new_hours = flt(duration_hours) if flt(duration_hours) > 0 else required_hours
	duration_insufficient = duration_hours is not None and new_hours + 0.000001 < required_hours
	net_hours = flt(metrics["net_available_hours"])
	projected_hours = other_hours + new_hours
	current_same_context = (
		_value(entry, "workstation") == workstation
		and _value(entry, "planned_date")
		and getdate(_value(entry, "planned_date")) == planned_date
		and ((_value(entry, "planned_shift") or None) == shift)
	)
	baseline_hours = projected_hours if current_same_context else other_hours
	baseline_overload = max(baseline_hours - net_hours, 0)
	projected_overload = max(projected_hours - net_hours, 0)
	requires_confirmation = projected_overload > baseline_overload + 0.000001
	status = "duration_insufficient" if duration_insufficient else ("overload" if requires_confirmation else "ok")
	message = None
	if duration_insufficient:
		message = _(
			"Expected Duration is insufficient for the planned quantity. Required duration: {0} hours; entered duration: {1} hours."
		).format(flt(required_hours, 2), flt(new_hours, 2))
	return {
		"status": status,
		"availability_status": "unavailable" if net_hours <= 0 and new_hours > 0 else (
			"overloaded" if projected_overload > 0 else "available"
		),
		"requires_confirmation": cint(requires_confirmation),
		"message": message,
		"workstation": workstation,
		"planned_date": str(planned_date),
		"shift": shift,
		"capacity_uom": rate_payload.get("capacity_uom"),
		"capacity_rate": flt(rate_payload.get("capacity_rate")),
		"quantity_uom": rate_payload.get("quantity_uom"),
		"planned_quantity": flt(rate_payload.get("planned_quantity")),
		"required_hours": required_hours,
		"available_hours": net_hours,
		"already_planned_hours": other_hours,
		"new_operation_hours": new_hours,
		"projected_total_hours": projected_hours,
		"utilization": projected_hours / net_hours * 100 if net_hours > 0 else None,
		"overload_hours": projected_overload,
	}


def _unverified_projection(
	workstation,
	planned_date,
	shift,
	rate_payload=None,
	availability=None,
	configuration_message=None,
	status="not_configured",
):
	availability = availability or {}
	return {
		"status": status,
		"availability_status": status,
		"requires_confirmation": 0,
		"workstation": workstation,
		"planned_date": str(planned_date),
		"shift": shift,
		"capacity_uom": (rate_payload or {}).get("capacity_uom"),
		"message": configuration_message
			or (rate_payload or {}).get("message")
			or availability.get("message")
			or _("Capacity cannot be verified for this Workstation, product size and selected date/shift."),
		"configuration_message": (
			configuration_message
			or (rate_payload or {}).get("message")
			or availability.get("message")
		),
		"capacity_rate": (rate_payload or {}).get("capacity_rate"),
		"quantity_uom": (rate_payload or {}).get("quantity_uom"),
		"planned_quantity": (rate_payload or {}).get("planned_quantity"),
		"planned_quantity_available": (rate_payload or {}).get("planned_quantity_available"),
		"required_hours": (rate_payload or {}).get("required_hours"),
	}
