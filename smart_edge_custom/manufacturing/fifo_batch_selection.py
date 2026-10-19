import frappe
from frappe import _
from frappe.utils import flt


def get_fifo_batch_allocations(item_code, warehouse, requested_batches, batch_weight_kg, label):
	requested_batches = int(requested_batches or 0)
	if requested_batches <= 0:
		return []
	if not item_code or not warehouse:
		frappe.throw(_("{0} Item and Source Warehouse are required for FIFO batch selection.").format(label))

	item = frappe.db.get_value(
		"Item", item_code, ["has_batch_no", "has_serial_no", "stock_uom"], as_dict=True
	)
	if not item or not item.has_batch_no:
		return []
	if item.has_serial_no:
		frappe.throw(_("FIFO batch selection does not support serial-numbered Item {0}.").format(item_code))

	from erpnext.stock.doctype.batch.batch import get_batch_qty

	available_batches = get_batch_qty(item_code=item_code, warehouse=warehouse) or []
	if not available_batches:
		throw_insufficient_batches(label, requested_batches, 0)

	receipt_order = get_first_receipt_order(
		item_code, warehouse, [row.batch_no for row in available_batches if row.batch_no]
	)
	conversion_factor = get_kg_to_stock_factor(item_code, item.stock_uom)
	required_stock_qty = flt(batch_weight_kg) * conversion_factor

	eligible = []
	for row in available_batches:
		receipt = receipt_order.get(row.batch_no)
		if not receipt or flt(row.qty) + 0.000001 < required_stock_qty:
			continue
		eligible.append(
			{
				"batch_no": row.batch_no,
				"receipt_datetime": receipt.posting_datetime,
				"receipt_creation": receipt.creation,
				"receipt_ledger": receipt.name,
				"available_qty": flt(row.qty) / conversion_factor,
			}
		)

	eligible.sort(
		key=lambda row: (
			row["receipt_datetime"],
			row["receipt_creation"],
			row["receipt_ledger"],
			row["batch_no"],
		)
	)
	if len(eligible) < requested_batches:
		throw_insufficient_batches(label, requested_batches, len(eligible))

	return [
		{
			"item_code": item_code,
			"batch_no": row["batch_no"],
			"warehouse": warehouse,
			"receipt_datetime": row["receipt_datetime"],
			"available_qty": row["available_qty"],
			"selected_qty": flt(batch_weight_kg),
			"uom": "Kg",
		}
		for row in eligible[:requested_batches]
	]


def get_first_receipt_order(item_code, warehouse, batch_nos):
	if not batch_nos:
		return {}

	placeholders = ", ".join(["%s"] * len(batch_nos))
	rows = frappe.db.sql(
		f"""
		select
			sbe.batch_no,
			sle.posting_datetime,
			sle.creation,
			sle.name
		from `tabStock Ledger Entry` sle
		inner join `tabSerial and Batch Entry` sbe
			on sbe.parent = sle.serial_and_batch_bundle
		where sle.item_code = %s
			and sle.warehouse = %s
			and sle.docstatus = 1
			and sle.is_cancelled = 0
			and sle.actual_qty > 0
			and sbe.docstatus = 1
			and sbe.is_cancelled = 0
			and sbe.qty > 0
			and sbe.batch_no in ({placeholders})
		order by sle.posting_datetime asc, sle.creation asc, sle.name asc, sbe.batch_no asc
		""",
		[item_code, warehouse, *batch_nos],
		as_dict=True,
	)

	first_receipt = {}
	for row in rows:
		first_receipt.setdefault(row.batch_no, row)
	return first_receipt


def get_kg_to_stock_factor(item_code, stock_uom):
	if stock_uom == "Kg":
		return 1.0

	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": "Kg"}, "conversion_factor"
	)
	if flt(conversion_factor) <= 0:
		frappe.throw(
			_("Item {0} requires a Kg-to-{1} UOM conversion for FIFO batch allocation.").format(
				item_code, stock_uom
			)
		)
	return flt(conversion_factor)


def throw_insufficient_batches(label, requested, available):
	frappe.throw(
		_("Insufficient {0} batch stock. Requested: {1} batches. Available: {2} eligible batches.").format(
			label, requested, available
		)
	)
