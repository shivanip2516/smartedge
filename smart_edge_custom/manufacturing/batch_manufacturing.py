from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt


SUPPORTED_ITEM_GROUPS = {"Compounds", "Base Colour"}
MASTER_BY_ITEM_GROUP = {
	"Compounds": ("Compound", "compound_name"),
	"Base Colour": ("Base Colour", "colour_name"),
}


def get_item_batch_details(item_code):
	details = {
		"has_batch_no": False,
		"is_batch_manufacturing_item": False,
		"output_qty": 0,
		"stock_uom": None,
	}
	if not item_code:
		return details

	item = frappe.db.get_value(
		"Item", item_code, ["item_group", "has_batch_no", "stock_uom"], as_dict=True
	)
	if not item:
		return details

	details.update(
		{
			"has_batch_no": bool(item.has_batch_no),
			"stock_uom": item.stock_uom,
			"is_batch_manufacturing_item": bool(
				item.has_batch_no and item.item_group in SUPPORTED_ITEM_GROUPS
			),
		}
	)
	if item.item_group not in SUPPORTED_ITEM_GROUPS:
		return details

	master_doctype, link_field = MASTER_BY_ITEM_GROUP[item.item_group]
	# total_kg is the master-owned recipe total. Use it instead of re-summing
	# child rows whose display UOM can differ from Kg.
	master = frappe.db.get_value(master_doctype, {link_field: item_code}, ["name", "total_kg"], as_dict=True)
	if master:
		details["output_qty"] = flt(master.total_kg)
		details["master_doctype"] = master_doctype
		details["master_name"] = master.name

	return details


def validate_bom(doc, method=None):
	details = get_item_batch_details(doc.item)
	if not details["is_batch_manufacturing_item"]:
		return

	if cint(doc.get("custom_batch")) != 1:
		frappe.throw(_("Batch must be 1 for a batch-controlled Compound or Base Colour BOM."))

	output_qty = flt(details["output_qty"])
	if output_qty <= 0:
		frappe.throw(
			_("The total recipe weight in {0} {1} must be greater than zero.").format(
				details.get("master_doctype") or _("the manufacturing master"),
				details.get("master_name") or "",
			)
		)

	if doc.is_new() or doc.has_value_changed("item"):
		doc.quantity = output_qty
		return

	precision = doc.precision("quantity")
	if flt(doc.quantity, precision) != flt(output_qty, precision):
		frappe.throw(
			_("Quantity (Output Qty) must be {0} {1}, the confirmed weight of one batch.").format(
				frappe.bold(flt(output_qty, precision)), details.get("stock_uom") or ""
			)
		)


@frappe.whitelist()
def get_work_order_batch_details(item_code, bom_no):
	result = {"applicable": False, "weight_per_batch": 0, "stock_uom": None}
	item = get_item_batch_details(item_code)
	result["stock_uom"] = item.get("stock_uom")
	if not item["is_batch_manufacturing_item"] or not bom_no:
		return result

	bom = frappe.db.get_value(
		"BOM", bom_no, ["item", "quantity", "custom_batch", "docstatus", "is_active"], as_dict=True
	)
	if (
		not bom
		or bom.item != item_code
		or bom.docstatus != 1
		or not bom.is_active
		or cint(bom.custom_batch) != 1
		or flt(bom.quantity) <= 0
	):
		return result

	result.update({"applicable": True, "weight_per_batch": flt(bom.quantity)})
	return result


def calculate_manufacturing_quantity(number_of_batches, weight_per_batch):
	batches = validate_number_of_batches(number_of_batches)
	weight_per_batch = flt(weight_per_batch)
	if weight_per_batch <= 0:
		frappe.throw(_("Weight per Batch must be greater than zero."))
	return batches * weight_per_batch


def validate_number_of_batches(number_of_batches, label="No. of Batches"):
	value = Decimal(str(number_of_batches or 0))
	if value < 1 or value != value.to_integral_value():
		frappe.throw(_("{0} must be a positive whole number.").format(label))
	return int(value)


@frappe.whitelist()
def make_batch_work_order(
	bom_no,
	item,
	number_of_batches,
	company=None,
	project=None,
	use_multi_level_bom=None,
):
	details = get_work_order_batch_details(item, bom_no)
	if not details["applicable"]:
		frappe.throw(
			_("Batch-wise Work Order creation is not applicable to Item {0} and BOM {1}.").format(
				item, bom_no
			)
		)

	batches = validate_number_of_batches(number_of_batches)
	weight_per_batch = flt(details["weight_per_batch"])
	if weight_per_batch <= 0:
		frappe.throw(_("Weight per Batch must be greater than zero."))

	from erpnext.manufacturing.doctype.work_order.work_order import make_work_order

	work_order = make_work_order(
		bom_no=bom_no,
		item=item,
		qty=calculate_manufacturing_quantity(batches, weight_per_batch),
		company=company,
		project=project,
		use_multi_level_bom=use_multi_level_bom,
	)
	work_order.custom_no_of_batches = batches
	work_order.custom_weight_per_batch = weight_per_batch
	return work_order


def _is_legacy_draft(doc):
	if doc.is_new():
		return False

	before = doc.get_doc_before_save()
	return bool(
		before
		and not flt(before.get("custom_weight_per_batch"))
		and not doc.has_value_changed("production_item")
		and not doc.has_value_changed("bom_no")
	)


def set_work_order_batch_quantity(doc, method=None):
	if cint(doc.docstatus) != 0:
		return

	item = get_item_batch_details(doc.production_item)
	if not item["is_batch_manufacturing_item"] or _is_legacy_draft(doc):
		return

	details = get_work_order_batch_details(doc.production_item, doc.bom_no)
	if not details["applicable"]:
		frappe.throw(
			_("BOM {0} must be an active, submitted one-batch BOM for Item {1}.").format(
				doc.bom_no or "", doc.production_item
			)
		)

	weight = flt(details["weight_per_batch"])
	if weight <= 0:
		frappe.throw(_("Weight per Batch must be greater than zero."))
	batches = doc.get("custom_no_of_batches")

	# Work Orders created by an integration may provide only the standard planned
	# quantity. Preserve that supported path when it is an exact number of batches.
	if doc.is_new() and flt(doc.qty) > weight and flt(batches) in (0, 1):
		batches = get_complete_batch_count(doc.qty, weight, "Work Order quantity")

	batches = validate_number_of_batches(batches)
	expected_qty = calculate_manufacturing_quantity(batches, weight)
	precision = doc.precision("qty")
	provided_qty = flt(doc.qty, precision)
	if provided_qty not in (0, 1) and provided_qty != flt(expected_qty, precision):
		frappe.throw(
			_("Quantity to Manufacture must be {0}, calculated from {1} batches × {2} per batch.").format(
				frappe.bold(flt(expected_qty, precision)), batches, flt(weight, precision)
			)
		)

	doc.custom_no_of_batches = batches
	doc.custom_weight_per_batch = weight
	doc.qty = expected_qty


def get_complete_batch_count(quantity, weight_per_batch, label="Produced quantity"):
	quantity = Decimal(str(quantity or 0))
	weight_per_batch = Decimal(str(weight_per_batch or 0))
	if quantity <= 0 or weight_per_batch <= 0:
		frappe.throw(_("{0} and Weight per Batch must be greater than zero.").format(label))

	batch_count = quantity / weight_per_batch
	if batch_count != batch_count.to_integral_value():
		frappe.throw(
			_("{0} must contain complete batches of {1}. Enter an exact multiple of the batch weight.").format(
				label, weight_per_batch
			)
		)

	return int(batch_count)


def _validate_existing_bundle(row, batch_count, weight_per_batch):
	bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
	if bundle.item_code != row.item_code or bundle.warehouse != row.t_warehouse:
		frappe.throw(_("The finished-goods Serial and Batch Bundle does not match the Item or Warehouse."))
	if bundle.type_of_transaction != "Inward":
		frappe.throw(_("The finished-goods Serial and Batch Bundle must be an Inward bundle."))

	entries = [entry for entry in bundle.entries if entry.batch_no]
	if (
		len(entries) != len(bundle.entries)
		or len(entries) != batch_count
		or len({entry.batch_no for entry in entries}) != batch_count
	):
		frappe.throw(_("The finished-goods bundle must contain one unique inventory Batch per completed batch."))

	for entry in entries:
		if Decimal(str(entry.qty)) != Decimal(str(weight_per_batch)):
			frappe.throw(_("Every finished-goods Batch allocation must equal Weight per Batch."))
		if frappe.db.get_value("Batch", entry.batch_no, "item") != row.item_code:
			frappe.throw(_("Batch {0} does not belong to Item {1}.").format(entry.batch_no, row.item_code))


def _make_finished_bundle(doc, row, batch_count, weight_per_batch):
	from erpnext.stock.doctype.batch.batch import make_batch
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	item = frappe.db.get_value(
		"Item", row.item_code, ["create_new_batch", "batch_number_series", "has_serial_no"], as_dict=True
	)
	if not item.create_new_batch:
		frappe.throw(
			_("Enable Create New Batch and configure Batch Number Series for Item {0}.").format(
				frappe.bold(row.item_code)
			)
		)
	if item.has_serial_no:
		frappe.throw(_("Batch-wise Compound/Base Colour output does not support serial-numbered Items."))

	batches = {}
	for _index in range(batch_count):
		batch_no = make_batch(
			frappe._dict(
				{
					"item": row.item_code,
					"reference_doctype": doc.doctype,
					"reference_name": doc.name,
				}
			)
		)
		batches[batch_no] = weight_per_batch

	bundle = SerialBatchCreation(
		{
			"item_code": row.item_code,
			"warehouse": row.t_warehouse,
			"posting_date": doc.posting_date,
			"posting_time": doc.posting_time,
			"voucher_type": doc.doctype,
			"voucher_no": doc.name,
			"voucher_detail_no": row.name,
			"qty": row.transfer_qty,
			"type_of_transaction": "Inward",
			"company": doc.company,
			"do_not_submit": True,
		}
	).make_serial_and_batch_bundle(batch_nos=batches)

	row.serial_and_batch_bundle = bundle.name
	row.use_serial_batch_fields = 0


def allocate_finished_batches(doc, method=None):
	if doc.purpose != "Manufacture" or not doc.work_order:
		return

	work_order = frappe.db.get_value(
		"Work Order",
		doc.work_order,
		[
			"production_item",
			"bom_no",
			"custom_no_of_batches",
			"custom_weight_per_batch",
			"docstatus",
		],
		as_dict=True,
	)
	if not work_order or work_order.docstatus != 1 or not flt(work_order.custom_weight_per_batch):
		return

	# The Work Order stores the confirmed weight. Do not re-read the recipe or
	# require its BOM to remain active while an in-progress Work Order completes.
	item = get_item_batch_details(work_order.production_item)
	if not item["is_batch_manufacturing_item"]:
		return

	# Serialize repeated submit attempts for the same draft. Batch and bundle
	# inserts remain in the Stock Entry transaction and roll back on any failure.
	frappe.db.sql("select name from `tabStock Entry` where name = %s for update", (doc.name,))

	weight_per_batch = flt(work_order.custom_weight_per_batch)
	for row in doc.get("items"):
		if not row.is_finished_item or row.item_code != work_order.production_item:
			continue

		batch_count = get_complete_batch_count(
			row.transfer_qty, weight_per_batch, _("Finished quantity at row {0}").format(row.idx)
		)
		if row.serial_and_batch_bundle:
			_validate_existing_bundle(row, batch_count, weight_per_batch)
		else:
			_make_finished_bundle(doc, row, batch_count, weight_per_batch)


def validate_batch_stock_entry_quantity(doc, method=None):
	if doc.purpose not in {"Material Transfer for Manufacture", "Manufacture"} or not doc.work_order:
		return

	work_order = frappe.db.get_value(
		"Work Order",
		doc.work_order,
		["production_item", "custom_weight_per_batch", "docstatus"],
		as_dict=True,
	)
	if not work_order or work_order.docstatus != 1 or flt(work_order.custom_weight_per_batch) <= 0:
		return
	if not get_item_batch_details(work_order.production_item)["is_batch_manufacturing_item"]:
		return

	get_complete_batch_count(
		doc.fg_completed_qty,
		work_order.custom_weight_per_batch,
		_("Quantity for {0}").format(doc.purpose),
	)
