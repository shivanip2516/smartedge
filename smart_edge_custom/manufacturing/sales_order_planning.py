from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from smart_edge_custom.manufacturing.final_bom_generator import get_generator_lifecycle_status


ACTIVE_COVERAGE_STATUSES = (
	"Draft",
	"Ready for Planning",
	"Partially Planned",
	"Planned",
	"In Production",
	"On Hold",
)
QUANTITY_TOLERANCE = 0.000001
SALES_ORDER_REFERENCE_DOCTYPE = "Final EB BOM Sales Order Reference"


@frappe.whitelist()
def get_sales_order_manufacturing_overview(sales_order):
	order = get_valid_sales_order(sales_order)
	return build_manufacturing_overview(order)


@frappe.whitelist()
def create_final_eb_bom_generator(sales_order, sales_order_item, quantity):
	quantity = flt(quantity)
	if quantity <= 0:
		frappe.throw(_("Quantity to Manufacture must be greater than zero."))

	frappe.has_permission("Final EB BOM Generator", "create", throw=True)
	lock_sales_order(sales_order)
	order = get_valid_sales_order(sales_order)
	overview = build_manufacturing_overview(order)
	requirement = next(
		(row for row in overview["items"] if row["sales_order_item"] == sales_order_item),
		None,
	)
	if not requirement:
		frappe.throw(_("Sales Order Item {0} is not eligible for Final EB manufacturing.").format(sales_order_item))

	shortage = flt(requirement["manufacturing_shortage"])
	if shortage <= 0:
		frappe.throw(_("No uncovered manufacturing requirement remains for Sales Order Item {0}.").format(sales_order_item))
	if quantity - shortage > QUANTITY_TOLERANCE:
		frappe.throw(
			_("Quantity to Manufacture cannot exceed the current uncovered requirement of {0} {1}.").format(
				frappe.format_value(shortage, {"fieldtype": "Float", "precision": 6}),
				requirement["stock_uom"],
			)
		)

	source = build_sales_order_reference(order, requirement, quantity)
	generator = create_final_eb_bom_generator_from_sources([source])

	return {
		"name": generator.name,
		"quantity": flt(generator.required_production_quantity),
		"stock_uom": generator.stock_uom or requirement["stock_uom"],
		"shortage_before": shortage,
		"remaining_shortage": max(shortage - quantity, 0),
	}


def build_sales_order_reference(order, requirement, quantity=None):
	"""Build one traceable source allocation in the generator's stock UOM."""
	converted_quantity = flt(
		requirement["manufacturing_shortage"] if quantity is None else quantity
	)
	conversion_factor = flt(requirement.get("conversion_factor")) or 1
	return {
		"sales_order": order.name,
		"sales_order_item": requirement["sales_order_item"],
		"customer": order.customer,
		"company": order.company,
		"finished_item": requirement["item_code"],
		"finished_item_name": requirement.get("item_name"),
		"source_quantity": flt(converted_quantity / conversion_factor),
		"source_uom": requirement.get("uom") or requirement.get("stock_uom"),
		"converted_production_quantity": converted_quantity,
		"production_uom": requirement.get("stock_uom"),
		"required_delivery_date": requirement.get("delivery_date") or order.delivery_date,
	}


def create_final_eb_bom_generator_from_sources(sources):
	if not sources:
		frappe.throw(_("At least one Sales Order Item reference is required."))

	meta = frappe.get_meta("Final EB BOM Generator", cached=True)
	reference_table_available = meta.has_field("sales_order_references") and frappe.db.exists(
		"DocType", SALES_ORDER_REFERENCE_DOCTYPE
	)
	if len(sources) > 1 and not reference_table_available:
		frappe.throw(
			_("Final EB BOM Generator traceability fields are not installed. Run the app migration before consolidating Sales Orders.")
		)

	first = sources[0]
	item_codes = {source["finished_item"] for source in sources}
	companies = {source["company"] for source in sources}
	production_uoms = {source["production_uom"] for source in sources}
	if len(item_codes) != 1 or len(companies) != 1 or len(production_uoms) != 1:
		frappe.throw(_("Only compatible Finished Goods requirements can be consolidated."))

	generator = frappe.new_doc("Final EB BOM Generator")
	generator.company = first["company"]
	generator.finished_item = first["finished_item"]
	generator.required_production_quantity = flt(
		sum(flt(source["converted_production_quantity"]) for source in sources)
	)
	# Preserve the original single-reference fields for existing integrations.
	generator.sales_order = first["sales_order"]
	generator.sales_order_item = first["sales_order_item"]
	customers = {source.get("customer") for source in sources}
	if len(customers) == 1:
		generator.customer = first.get("customer")
	dates = [source.get("required_delivery_date") for source in sources if source.get("required_delivery_date")]
	if dates:
		generator.required_delivery_date = min(dates)

	if reference_table_available:
		for source in sources:
			generator.append(
				"sales_order_references",
				{
					"sales_order": source["sales_order"],
					"sales_order_item": source["sales_order_item"],
					"customer": source.get("customer"),
					"finished_item": source["finished_item"],
					"source_quantity": source["source_quantity"],
					"source_uom": source["source_uom"],
					"converted_production_quantity": source["converted_production_quantity"],
				}
			)

	generator.insert()
	return generator


def get_valid_sales_order(sales_order):
	if not sales_order:
		frappe.throw(_("Sales Order is required."))
	order = frappe.get_doc("Sales Order", sales_order)
	order.check_permission("read")
	if order.docstatus != 1 or order.status == "Cancelled":
		frappe.throw(_("Manufacturing can only be planned from a submitted, non-cancelled Sales Order."))
	return order


def build_manufacturing_overview(order):
	eligible_items = get_eligible_items(order)
	coverage, linked_generators = get_existing_coverage(order.name)
	rows = []
	for item in order.items:
		item_details = eligible_items.get(item.item_code)
		if not item_details:
			continue
		conversion_factor = flt(item.conversion_factor) or 1
		outstanding_qty = max(flt(item.qty) - flt(item.delivered_qty), 0)
		outstanding_stock_qty = max(flt(item.stock_qty) - flt(item.delivered_qty) * conversion_factor, 0)
		warehouse = get_finished_goods_warehouse(order, item)
		rows.append(
			{
				"sales_order_item": item.name,
				"idx": item.idx,
				"item_code": item.item_code,
				"item_name": item.item_name or item_details.item_name,
				"ordered_qty": flt(item.qty),
				"delivered_qty": flt(item.delivered_qty),
				"outstanding_qty": outstanding_qty,
				"ordered_stock_qty": flt(item.stock_qty),
				"outstanding_stock_qty": outstanding_stock_qty,
				"uom": item.uom,
				"stock_uom": item.stock_uom or item_details.stock_uom,
				"conversion_factor": conversion_factor,
				"warehouse": warehouse,
				"delivery_date": item.delivery_date or order.delivery_date,
				"existing_planned_qty": flt(coverage.get(item.name)),
				"own_reserved_qty": flt(item.get("stock_reserved_qty")),
				"reserve_stock": bool(item.get("reserve_stock") or order.get("reserve_stock")),
			}
		)

	allocate_finished_stock(rows)
	return {
		"sales_order": order.name,
		"customer": order.customer,
		"company": order.company,
		"items": rows,
		"linked_generators": linked_generators,
		"has_eligible_items": bool(rows),
		"availability_note": _(
			"Available FG Stock respects submitted Stock Reservations when configured. Otherwise it is an estimate based on current warehouse stock and existing Sales Order demand."
		),
	}


def get_eligible_items(order):
	item_codes = list({row.item_code for row in order.items if row.item_code})
	if not item_codes:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={
				"name": ["in", item_codes],
				"disabled": 0,
				"is_stock_item": 1,
				"include_item_in_manufacturing": 1,
			},
			fields=["name", "item_name", "stock_uom"],
			limit_page_length=len(item_codes),
		)
	}


def get_existing_coverage(sales_order):
	meta = frappe.get_meta("Final EB BOM Generator", cached=True)
	if not meta.has_field("sales_order") or not meta.has_field("sales_order_item"):
		return {}, []
	fields = [
		"name",
		"sales_order",
		"sales_order_item",
		"production_quantity",
		"required_production_quantity",
		"status",
		"production_status",
		"finished_item",
	]
	rows = frappe.get_all(
		"Final EB BOM Generator",
		filters={"sales_order": sales_order},
		fields=fields,
		order_by="creation asc",
		limit_page_length=1000,
	)
	primary_names = {row.name for row in rows}
	reference_table_available = meta.has_field("sales_order_references") and frappe.db.exists(
		"DocType", SALES_ORDER_REFERENCE_DOCTYPE
	)
	if reference_table_available:
		referenced_parents = frappe.get_all(
			SALES_ORDER_REFERENCE_DOCTYPE,
			filters={"sales_order": sales_order, "parenttype": "Final EB BOM Generator"},
			fields=["parent"],
			limit_page_length=5000,
		)
		parent_names = {row.parent for row in referenced_parents if row.parent}
		known_names = {row.name for row in rows}
		additional_names = sorted(parent_names - known_names)
		if additional_names:
			rows.extend(
				frappe.get_all(
					"Final EB BOM Generator",
					filters={"name": ["in", additional_names]},
					fields=fields,
					order_by="creation asc",
					limit_page_length=len(additional_names),
				)
			)
	coverage = defaultdict(float)
	active_rows = {
		row.name: row
		for row in rows
		if get_generator_lifecycle_status(row) in ACTIVE_COVERAGE_STATUSES
	}
	if active_rows and reference_table_available:
		references = frappe.get_all(
			SALES_ORDER_REFERENCE_DOCTYPE,
			filters={
				"parent": ["in", list(active_rows)],
				"parenttype": "Final EB BOM Generator",
				"sales_order": sales_order,
			},
			fields=["parent", "sales_order_item", "converted_production_quantity"],
			limit_page_length=5000,
		)
		references_by_parent = defaultdict(list)
		for reference in references:
			references_by_parent[reference.parent].append(reference)
		for row in active_rows.values():
			if references_by_parent.get(row.name):
				for reference in references_by_parent[row.name]:
					if reference.sales_order_item:
						coverage[reference.sales_order_item] += flt(reference.converted_production_quantity)
			elif row.name in primary_names and row.sales_order_item:
				coverage[row.sales_order_item] += flt(
					row.required_production_quantity or row.production_quantity
				)
	else:
		for row in active_rows.values():
			if row.name in primary_names and row.sales_order_item:
				coverage[row.sales_order_item] += flt(
					row.required_production_quantity or row.production_quantity
				)
	return coverage, rows


def get_finished_goods_warehouse(order, item):
	warehouse = item.warehouse or order.set_warehouse
	if not warehouse:
		warehouse = frappe.db.get_value(
			"Item Default",
			{"parent": item.item_code, "company": order.company},
			"default_warehouse",
		)
	if not warehouse:
		warehouse = frappe.db.get_value("Item Default", {"parent": item.item_code}, "default_warehouse")
	if not warehouse:
		warehouse = frappe.db.get_value("Company", order.company, "default_fg_warehouse")
	return warehouse


def allocate_finished_stock(rows):
	grouped = defaultdict(list)
	for row in rows:
		grouped[(row["item_code"], row["warehouse"])].append(row)

	for (item_code, warehouse), item_rows in grouped.items():
		bin_row = get_bin_quantities(item_code, warehouse)
		actual_qty = max(flt(bin_row.get("actual_qty")), 0)
		total_sre_reserved = max(flt(bin_row.get("reserved_stock")), 0)
		uses_stock_reservation = total_sre_reserved > 0 or any(
			row["reserve_stock"] or row["own_reserved_qty"] > 0 for row in item_rows
		)
		if uses_stock_reservation:
			shared_available = max(actual_qty - total_sre_reserved, 0)
		else:
			current_order_demand = sum(row["outstanding_stock_qty"] for row in item_rows)
			other_demand = max(flt(bin_row.get("reserved_qty")) - current_order_demand, 0)
			shared_available = max(actual_qty - other_demand, 0)

		for row in sorted(item_rows, key=lambda value: value["idx"]):
			uncovered_before_stock = max(row["outstanding_stock_qty"] - row["existing_planned_qty"], 0)
			own_reserved = min(row["own_reserved_qty"], uncovered_before_stock) if uses_stock_reservation else 0
			shared_allocation = min(max(uncovered_before_stock - own_reserved, 0), shared_available)
			shared_available -= shared_allocation
			available_stock = own_reserved + shared_allocation
			row["available_fg_stock"] = flt(available_stock)
			row["manufacturing_shortage"] = flt(max(uncovered_before_stock - available_stock, 0))
			row["quantity_to_manufacture"] = row["manufacturing_shortage"]
			row["stock_is_estimate"] = not uses_stock_reservation or own_reserved < available_stock
			row["stock_actual_qty"] = flt(actual_qty)
			row["stock_reserved_for_other_demand"] = flt(max(actual_qty - shared_available - available_stock, 0))


def lock_sales_order(sales_order):
	frappe.db.sql("select name from `tabSales Order` where name=%s for update", (sales_order,))


def lock_sales_orders(sales_orders):
	for sales_order in sorted(set(sales_orders)):
		lock_sales_order(sales_order)


def get_bin_quantities(item_code, warehouse):
	if not item_code or not warehouse:
		return frappe._dict()
	return frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["actual_qty", "reserved_qty", "reserved_stock"],
		as_dict=True,
	) or frappe._dict()
