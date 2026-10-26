from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


ACTIVE_COVERAGE_STATUSES = (
	"Draft",
	"Ready for Planning",
	"Partially Planned",
	"Planned",
	"In Production",
	"On Hold",
)
QUANTITY_TOLERANCE = 0.000001


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

	generator = frappe.new_doc("Final EB BOM Generator")
	generator.company = order.company
	generator.finished_item = requirement["item_code"]
	generator.production_quantity = quantity
	generator.sales_order = order.name
	generator.sales_order_item = requirement["sales_order_item"]
	generator.customer = order.customer
	generator.required_delivery_date = requirement["delivery_date"] or order.delivery_date
	generator.insert()

	return {
		"name": generator.name,
		"quantity": flt(generator.production_quantity),
		"stock_uom": generator.stock_uom or requirement["stock_uom"],
		"shortage_before": shortage,
		"remaining_shortage": max(shortage - quantity, 0),
	}


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
	rows = frappe.get_all(
		"Final EB BOM Generator",
		filters={"sales_order": sales_order},
		fields=["name", "sales_order_item", "production_quantity", "status", "finished_item"],
		order_by="creation asc",
		limit_page_length=1000,
	)
	coverage = defaultdict(float)
	for row in rows:
		if row.status in ACTIVE_COVERAGE_STATUSES and row.sales_order_item:
			coverage[row.sales_order_item] += flt(row.production_quantity)
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


def get_bin_quantities(item_code, warehouse):
	if not item_code or not warehouse:
		return frappe._dict()
	return frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["actual_qty", "reserved_qty", "reserved_stock"],
		as_dict=True,
	) or frappe._dict()
