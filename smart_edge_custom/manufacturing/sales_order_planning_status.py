"""Maintain the derived planning status shown on Sales Order lists.

The status is deliberately derived from Sales Order Item references on active
Final EB BOM Generators.  It is not based on item-code-only matching and it
does not change any production or stock transaction.
"""

from collections import defaultdict

import frappe
from frappe.utils import flt

from smart_edge_custom.manufacturing.final_bom_generator import get_generator_lifecycle_status
from smart_edge_custom.manufacturing.sales_order_planning import (
	ACTIVE_COVERAGE_STATUSES,
	SALES_ORDER_REFERENCE_DOCTYPE,
)


PLANNING_STATUS_FIELD = "custom_planning_status"
PLANNING_STATUSES = ("Not Planned", "Partially Planned", "Planned")
QUANTITY_TOLERANCE = 0.000001


def update_sales_order_planning_status(doc, method=None):
	"""Recalculate one Sales Order after its items or status change."""
	if not doc or not doc.get("name"):
		return

	update_sales_order_status(doc.name, order=doc)


def update_from_final_eb_bom_generator(doc, method=None):
	"""Refresh every Sales Order touched by a generator insert/update/cancel/delete."""
	sales_orders = set(get_generator_sales_orders(doc))

	# If references were edited, the old Sales Orders also need recalculation.
	previous = get_doc_before_save(doc)
	if previous:
		sales_orders.update(get_generator_sales_orders(previous))

	exclude_generators = {doc.name} if method == "on_trash" and doc.get("name") else set()
	for sales_order in sorted(sales_orders):
		update_sales_order_status(sales_order, exclude_generators=exclude_generators)


def update_sales_order_status(sales_order, order=None, exclude_generators=None):
	"""Calculate and persist the status without saving the Sales Order document."""
	if not sales_order or not frappe.db.exists("Sales Order", sales_order):
		return "Not Planned"

	status = calculate_sales_order_planning_status(
		sales_order,
		order=order,
		exclude_generators=exclude_generators,
	)
	current = frappe.db.get_value("Sales Order", sales_order, PLANNING_STATUS_FIELD)
	if current != status:
		frappe.db.set_value(
			"Sales Order",
			sales_order,
			PLANNING_STATUS_FIELD,
			status,
			update_modified=False,
		)
	return status


def calculate_sales_order_planning_status(sales_order, order=None, exclude_generators=None):
	"""Return the status for a Sales Order from current active FEBG allocations."""
	if order is None:
		order = frappe.get_doc("Sales Order", sales_order)

	if order.get("docstatus") == 2 or order.get("status") == "Cancelled":
		return "Not Planned"

	items = get_eligible_sales_order_items(order)
	if not items:
		# An order with no eligible manufacturing items is not considered planned.
		return "Not Planned"

	planned_by_item = get_planned_quantities(
		sales_order,
		items,
		exclude_generators=exclude_generators,
	)

	has_planned_quantity = False
	all_items_fully_planned = True
	for sales_order_item, details in items.items():
		required = details["required_qty"]
		planned = planned_by_item.get(sales_order_item, 0.0)
		if planned > QUANTITY_TOLERANCE:
			has_planned_quantity = True
		if planned + QUANTITY_TOLERANCE < required:
			all_items_fully_planned = False

	if not has_planned_quantity:
		return "Not Planned"
	return "Planned" if all_items_fully_planned else "Partially Planned"


def get_eligible_sales_order_items(order):
	"""Map exact SO Item row names to required stock-UOM production quantities."""
	item_codes = {row.item_code for row in order.items if row.item_code}
	if not item_codes:
		return {}

	eligible_item_codes = {
		row.name
		for row in frappe.get_all(
			"Item",
			filters={
				"name": ["in", list(item_codes)],
				"disabled": 0,
				"is_stock_item": 1,
				"include_item_in_manufacturing": 1,
			},
			fields=["name"],
			limit_page_length=len(item_codes),
		)
	}

	items = {}
	for row in order.items:
		if not row.item_code or row.item_code not in eligible_item_codes or not row.name:
			continue
		conversion_factor = flt(row.conversion_factor) or 1
		stock_qty = flt(row.stock_qty)
		if not stock_qty and flt(row.qty):
			stock_qty = flt(row.qty) * conversion_factor
		required_qty = max(stock_qty - flt(row.delivered_qty) * conversion_factor, 0)
		items[row.name] = {
			"item_code": row.item_code,
			"required_qty": required_qty,
		}
	return items


def get_planned_quantities(sales_order, items, exclude_generators=None):
	"""Sum active FEBG quantities by exact Sales Order Item reference."""
	if not items:
		return {}

	meta = frappe.get_meta("Final EB BOM Generator", cached=True)
	primary_rows = frappe.get_all(
		"Final EB BOM Generator",
		filters={"sales_order": sales_order},
		fields=[
			"name",
			"sales_order",
			"sales_order_item",
			"production_quantity",
			"required_production_quantity",
			"status",
			"production_status",
			"finished_item",
			"docstatus",
		],
		order_by="creation asc",
		limit_page_length=5000,
	)
	primary_names = {row.name for row in primary_rows}
	reference_table_available = meta.has_field("sales_order_references") and frappe.db.exists(
		"DocType", SALES_ORDER_REFERENCE_DOCTYPE
	)
	rows = list(primary_rows)

	if reference_table_available:
		referenced_parents = frappe.get_all(
			SALES_ORDER_REFERENCE_DOCTYPE,
			filters={"sales_order": sales_order, "parenttype": "Final EB BOM Generator"},
			fields=["parent"],
			limit_page_length=5000,
		)
		parent_names = {row.parent for row in referenced_parents if row.parent}
		additional_names = sorted(parent_names - primary_names)
		if additional_names:
			rows.extend(
				frappe.get_all(
					"Final EB BOM Generator",
					filters={"name": ["in", additional_names]},
					fields=[
						"name",
						"sales_order",
						"sales_order_item",
						"production_quantity",
						"required_production_quantity",
						"status",
						"production_status",
						"finished_item",
						"docstatus",
					],
					order_by="creation asc",
					limit_page_length=len(additional_names),
				)
			)

	excluded = set(exclude_generators or ())
	active_rows = {
		row.name: row
		for row in rows
		if row.name not in excluded
		and row.get("docstatus") != 2
		and get_generator_lifecycle_status(row) in ACTIVE_COVERAGE_STATUSES
	}
	if not active_rows:
		return {}

	planned = defaultdict(float)
	if reference_table_available:
		references = frappe.get_all(
			SALES_ORDER_REFERENCE_DOCTYPE,
			filters={
				"parent": ["in", list(active_rows)],
				"parenttype": "Final EB BOM Generator",
			},
			fields=["parent", "sales_order", "sales_order_item", "finished_item", "converted_production_quantity"],
			limit_page_length=10000,
		)
		references_by_parent = defaultdict(list)
		for reference in references:
			references_by_parent[reference.parent].append(reference)

		for parent, generator in active_rows.items():
			parent_references = references_by_parent.get(parent, [])
			if parent_references:
				for reference in parent_references:
					add_reference_quantity(planned, reference, sales_order, items)
			elif parent in primary_names:
				add_legacy_quantity(planned, generator, sales_order, items)
	else:
		for generator in active_rows.values():
			add_legacy_quantity(planned, generator, sales_order, items)

	return dict(planned)


def add_reference_quantity(planned, reference, sales_order, items):
	"""Add a consolidated child-table allocation only for an exact SO Item."""
	if reference.sales_order and reference.sales_order != sales_order:
		return
	item = items.get(reference.sales_order_item)
	if not item:
		return
	if reference.finished_item and reference.finished_item != item["item_code"]:
		return
	planned[reference.sales_order_item] = planned.get(reference.sales_order_item, 0.0) + flt(
		reference.converted_production_quantity
	)


def add_legacy_quantity(planned, generator, sales_order, items):
	"""Add a legacy generator's primary allocation without item-code-only matching."""
	item = items.get(generator.sales_order_item)
	if not item or generator.sales_order != sales_order:
		return
	if generator.finished_item and generator.finished_item != item["item_code"]:
		return
	planned[generator.sales_order_item] = planned.get(generator.sales_order_item, 0.0) + flt(
		generator.required_production_quantity or generator.production_quantity
	)


def get_generator_sales_orders(doc):
	"""Return primary and child-table Sales Orders present on a generator document."""
	values = set()
	if doc.get("sales_order"):
		values.add(doc.sales_order)
	for row in doc.get("sales_order_references") or []:
		if row.get("sales_order"):
			values.add(row.sales_order)
	return values


def get_doc_before_save(doc):
	try:
		return doc.get_doc_before_save()
	except (AttributeError, TypeError):
		return None


def ensure_planning_status_field():
	"""Create the field for sites that have not imported app fixtures yet."""
	if frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": PLANNING_STATUS_FIELD}):
		return

	field = frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Sales Order",
			"fieldname": PLANNING_STATUS_FIELD,
			"fieldtype": "Select",
			"label": "Planning Status",
			"options": "\n".join(PLANNING_STATUSES),
			"default": "Not Planned",
			"read_only": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"allow_on_submit": 1,
			"insert_after": "status",
			"module": "smart_edge_custom",
		}
	)
	field.insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Sales Order")


def recalculate_all_sales_order_planning_status():
	"""Idempotent backfill used by the migration patch."""
	for row in frappe.get_all("Sales Order", fields=["name"], order_by="name", limit_page_length=0):
		update_sales_order_status(row.name)
