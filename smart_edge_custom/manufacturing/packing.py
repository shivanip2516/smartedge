"""Read-only Phase 12 packing verification helpers."""
import re

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, today
from frappe.model.naming import make_autoname

QR_PREFIX = "SE-LOT:"
PACK_SESSION = "SmartEdge Packing Session"
PACK_DETAIL = "SmartEdge Pack Detail"

def operation_qr_required(operation):
	if not operation or not frappe.get_meta("Operation", cached=True).has_field("custom_require_input_qr_scan"):
		return False
	return bool(frappe.db.get_value("Operation", operation, "custom_require_input_qr_scan"))

def _is_packing_entry(entry):
	return str(entry.get("planning_category") or "").lower() == "packing" or "pack" in str(entry.get("operation") or "").lower()


def _packing_roles():
	return {"Manufacturing User", "Manufacturing Manager", "Stock User", "Stock Manager", "System Manager"}


def _require_packing_role():
	if frappe.session.user != "Administrator" and not _packing_roles().intersection(frappe.get_roles()):
		frappe.throw(_("You are not authorized to manage packing allocations."), frappe.PermissionError)


def _batch_balance(item_code, batch, warehouse):
	from erpnext.stock.doctype.batch.batch import get_batch_qty
	return sum(flt(row.get("qty")) for row in (get_batch_qty(item_code=item_code) or [])
		if row.get("batch_no") == batch and row.get("warehouse") == warehouse)


def _reserved_quantity(item_code, batch, warehouse):
	"""Return conservative active reservations for the selected stock scope."""
	meta = frappe.get_meta("Stock Reservation Entry", cached=True)
	if not meta.has_field("reserved_qty") or not meta.has_field("item_code") or not meta.has_field("warehouse"):
		return 0
	filters = {"item_code": item_code, "warehouse": warehouse}
	if meta.has_field("batch_no"):
		filters["batch_no"] = batch
	if meta.has_field("docstatus"):
		filters["docstatus"] = 1
	if meta.has_field("status"):
		filters["status"] = ["not in", ["Cancelled", "Completed"]]
	return sum(flt(row.reserved_qty) for row in frappe.get_all("Stock Reservation Entry", filters=filters, fields=["reserved_qty"]))


def _pack_uom_quantities(item_code, quantity, stock_uom):
	"""Expose meter/kg values only when the Item's declared UOM conversion is known."""
	result = {"meter_quantity": None, "kg_quantity": None}
	unit = str(stock_uom or "").strip().lower()
	if unit in {"meter", "metre", "m"}:
		result["meter_quantity"] = quantity
	if unit in {"kg", "kilogram", "kilograms"}:
		result["kg_quantity"] = quantity
	try:
		for target, fieldname in (("Meter", "meter_quantity"), ("Kg", "kg_quantity")):
			if result[fieldname] is not None or not frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "uom": target}):
				continue
			factor = flt(frappe.db.get_value("UOM Conversion Detail", {"parent": item_code, "uom": target}, "conversion_factor"))
			if factor > 0:
				result[fieldname] = quantity / factor
	except Exception:
		# Missing optional conversion metadata must not block physical packing.
		pass
	return result


def _validate_sales_order_item(sales_order_item, item_code):
	row = frappe.db.get_value("Sales Order Item", sales_order_item,
		["name", "parent", "parenttype", "item_code", "qty", "delivered_qty", "uom", "stock_uom"], as_dict=True)
	if not row or row.parenttype != "Sales Order":
		frappe.throw(_("Sales Order Item {0} does not exist.").format(sales_order_item))
	if row.item_code != item_code:
		frappe.throw(_("Sales Order Item {0} is for Item {1}, not {2}.").format(sales_order_item, row.item_code, item_code))
	order = frappe.db.get_value("Sales Order", row.parent, ["docstatus", "customer", "company"], as_dict=True)
	if not order or order.docstatus != 1:
		frappe.throw(_("Sales Order {0} must be submitted before packing.").format(row.parent))
	return row, order


@frappe.whitelist()
def create_packing_session(customer, sales_order=None, warehouse=None):
	_require_packing_role()
	if sales_order:
		order = frappe.db.get_value("Sales Order", sales_order, ["docstatus", "customer"], as_dict=True)
		if not order or order.docstatus != 1:
			frappe.throw(_("Sales Order {0} must be submitted before packing.").format(sales_order))
		if customer and order.customer != customer:
			frappe.throw(_("Customer does not match Sales Order {0}.").format(sales_order))
	doc = frappe.new_doc(PACK_SESSION)
	doc.customer = customer
	doc.sales_order = sales_order
	doc.warehouse = warehouse
	doc.operator = frappe.session.user
	doc.packing_date = today()
	doc.insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def confirm_pack(session, sales_order_item, item_code, fg_batch, production_lot, packed_qty, warehouse=None, pack_id=None):
	_require_packing_role()
	qty = flt(packed_qty)
	if qty <= 0:
		frappe.throw(_("Packed quantity must be greater than zero."))
	doc = frappe.get_doc(PACK_SESSION, session)
	doc.check_permission("write")
	if doc.status != "Draft":
		frappe.throw(_("Packing Session {0} is no longer editable.").format(session))
	warehouse = warehouse or doc.warehouse
	if not warehouse:
		frappe.throw(_("A source warehouse is required."))
	# Lock the order row so concurrent operators cannot over-allocate it.
	frappe.db.sql("select name from `tabSales Order Item` where name=%s for update", (sales_order_item,))
	so_row, order = _validate_sales_order_item(sales_order_item, item_code)
	if doc.sales_order and doc.sales_order != so_row.parent:
		frappe.throw(_("Sales Order Item belongs to {0}, not this session's Sales Order.").format(so_row.parent))
	if doc.customer != order.customer:
		frappe.throw(_("Customer does not match the Sales Order Item."))
	batch = frappe.db.get_value("Batch", fg_batch, ["item", "disabled"], as_dict=True)
	if not batch or batch.item != item_code or batch.disabled:
		frappe.throw(_("FG Batch {0} is not an active batch for Item {1}.").format(fg_batch, item_code))
	lot = frappe.db.get_value("SmartEdge Production Lot", production_lot, ["name", "finished_item", "quality_inspection"], as_dict=True)
	if not lot or lot.finished_item != item_code:
		frappe.throw(_("Production Lot {0} does not match Item {1}.").format(production_lot, item_code))
	if lot.quality_inspection:
		qc = frappe.db.get_value("Quality Inspection", lot.quality_inspection, ["docstatus", "custom_smartedge_qc_status"], as_dict=True)
		if not qc or qc.docstatus != 1 or qc.custom_smartedge_qc_status != "Accepted":
			frappe.throw(_("Production Lot {0} is not QC released.").format(production_lot))
	available = _batch_balance(item_code, fg_batch, warehouse)
	available -= _reserved_quantity(item_code, fg_batch, warehouse)
	allocated = sum(flt(row.packed_qty) for row in frappe.get_all(PACK_DETAIL, filters={"sales_order_item": sales_order_item, "item_code": item_code, "fg_batch": fg_batch, "warehouse": warehouse, "status": ["in", ["Confirmed", "Delivered"]]}, fields=["packed_qty"]))
	remaining_stock = available - allocated
	remaining_order = flt(so_row.qty) - flt(so_row.delivered_qty) - sum(flt(row.packed_qty) for row in frappe.get_all(PACK_DETAIL, filters={"sales_order_item": sales_order_item, "status": ["in", ["Confirmed", "Delivered"]]}, fields=["packed_qty"]))
	if qty > remaining_stock + 0.000001:
		frappe.throw(_("Only {0} {1} remains available from Batch {2} in {3}.").format(remaining_stock, so_row.stock_uom or so_row.uom, fg_batch, warehouse))
	if qty > remaining_order + 0.000001:
		frappe.throw(_("Only {0} remains outstanding for Sales Order Item {1}.").format(remaining_order, sales_order_item))
	pack_id = pack_id or make_autoname("PACK-.#####")
	if frappe.db.exists(PACK_DETAIL, {"pack_id": pack_id}):
		frappe.throw(_("Pack {0} already exists.").format(pack_id))
	stock_uom = so_row.stock_uom or so_row.uom
	converted = _pack_uom_quantities(item_code, qty, stock_uom)
	row = doc.append("packs", {"pack_id": pack_id, "sales_order": so_row.parent, "sales_order_item": sales_order_item, "customer": order.customer, "item_code": item_code, "fg_batch": fg_batch, "production_lot": production_lot, "packed_qty": qty, "meter_quantity": converted["meter_quantity"], "kg_quantity": converted["kg_quantity"], "stock_uom": stock_uom, "warehouse": warehouse, "status": "Confirmed", "operator": frappe.session.user, "packed_at": now_datetime()})
	doc.save()
	return {"session": doc.name, "pack_id": row.pack_id, "quantity": qty}


@frappe.whitelist()
def confirm_packing_session(session):
	_require_packing_role()
	doc = frappe.get_doc(PACK_SESSION, session)
	doc.check_permission("write")
	if doc.status != "Draft" or not doc.packs:
		frappe.throw(_("A Draft Packing Session with at least one confirmed pack is required."))
	doc.status = "Confirmed"
	doc.save()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def create_delivery_note(session):
	_require_packing_role()
	doc = frappe.get_doc(PACK_SESSION, session)
	doc.check_permission("write")
	if doc.status not in {"Confirmed", "Delivered"}:
		frappe.throw(_("Confirm the Packing Session before creating a Delivery Note."))
	if doc.delivery_note:
		return {"delivery_note": doc.delivery_note, "existing": 1}
	items = {}
	for pack in doc.packs:
		if pack.status != "Confirmed":
			continue
		key = (pack.sales_order_item, pack.item_code, pack.fg_batch, pack.warehouse)
		items[key] = items.get(key, 0) + flt(pack.packed_qty)
	if not items:
		frappe.throw(_("No undelivered confirmed packs remain."))
	dn = frappe.new_doc("Delivery Note")
	dn.customer = doc.customer
	dn.posting_date = today()
	dn.set_posting_time = 1
	for (so_detail, item_code, batch, warehouse), qty in items.items():
		so = frappe.db.get_value("Sales Order Item", so_detail, ["parent", "uom", "conversion_factor"], as_dict=True)
		dn.append("items", {"item_code": item_code, "qty": qty, "warehouse": warehouse, "against_sales_order": so.parent, "so_detail": so_detail, "batch_no": batch, "uom": so.uom, "conversion_factor": so.conversion_factor or 1})
	dn.insert()
	for row in dn.items:
		for pack in doc.packs:
			if pack.status == "Confirmed" and pack.sales_order_item == row.so_detail and pack.item_code == row.item_code and pack.fg_batch == row.batch_no and pack.warehouse == row.warehouse:
				pack.delivery_note = dn.name
				pack.delivery_note_item = row.name
	doc.delivery_note = dn.name
	doc.save()
	return {"delivery_note": dn.name, "status": "Draft"}


def on_delivery_note_submit(doc, method=None):
	for pack in frappe.get_all(PACK_DETAIL, filters={"delivery_note": doc.name, "status": "Confirmed"}, pluck="name"):
		frappe.db.set_value(PACK_DETAIL, pack, "status", "Delivered", update_modified=False)


def on_delivery_note_cancel(doc, method=None):
	for pack in frappe.get_all(PACK_DETAIL, filters={"delivery_note": doc.name, "status": "Delivered"}, pluck="name"):
		frappe.db.set_value(PACK_DETAIL, pack, {"status": "Confirmed", "delivery_note": None, "delivery_note_item": None}, update_modified=False)

@frappe.whitelist()
def verify_packing_qr(plan_entry, payload=None, quantity=None):
	if not str(payload or "").strip():
		frappe.throw(_("Scan the input Production Lot QR before verification."))
	job = frappe.db.get_value("SmartEdge Production Job", {"plan_entry": plan_entry, "status": ["!=", "Cancelled"]}, ["name", "input_production_lot"], as_dict=True)
	if not job:
		frappe.throw(_("No active Packing Production Job was found."))
	entry = frappe.get_doc("SmartEdge Production Plan Entry", plan_entry)
	from smart_edge_custom.manufacturing.production_lot import get_input_production_lot
	expected_input_lot = job.input_production_lot or get_input_production_lot(entry)
	if not operation_qr_required(entry.operation):
		frappe.throw(_("Input QR verification is not enabled for Operation {0}.").format(entry.operation))
	payload = str(payload or "").strip()
	if not payload.startswith(QR_PREFIX):
		frappe.throw(_("Invalid SmartEdge QR payload."))
	lot_name = payload[len(QR_PREFIX):].strip()
	if not lot_name or lot_name != expected_input_lot:
		if not expected_input_lot:
			frappe.throw(_("No input Production Lot is assigned or resolvable for Packing Job {0}. Complete the previous operation and verify its output Lot linkage.").format(job.name))
		frappe.throw(_("Production Lot {0} is not assigned to Packing Job {1}. Scan the previous operation Lot {2}.").format(lot_name or _("(empty)"), job.name, expected_input_lot))
	if not frappe.db.exists("SmartEdge Production Lot", lot_name):
		frappe.throw(_("Production Lot {0} does not exist.").format(lot_name))
	lot = frappe.get_doc("SmartEdge Production Lot", lot_name)
	expected_item = frappe.db.get_value("Final EB BOM Generator", entry.final_eb_bom_generator, "finished_item")
	if expected_item and lot.finished_item != expected_item:
		frappe.throw(_("The scanned Production Lot Item does not match this Operation."))
	if lot.quality_inspection:
		qc = frappe.db.get_value("Quality Inspection", lot.quality_inspection, ["docstatus", "custom_smartedge_qc_status"], as_dict=True)
		if not qc or qc.docstatus != 1 or qc.custom_smartedge_qc_status != "Accepted":
			frappe.throw(_("The scanned Production Lot is not QC released."))
	qty = flt(quantity)
	if qty <= 0 or qty > flt(lot.produced_quantity) + 0.000001:
		frappe.throw(_("Packing quantity exceeds the eligible source Production Lot quantity."))
	batch = None
	batch_meta = frappe.get_meta("Batch", cached=True)
	if batch_meta.has_field("custom_operation_lot"):
		batch = frappe.db.get_value("Batch", {"custom_operation_lot": lot.name}, "name")
	job_doc = frappe.get_doc("SmartEdge Production Job", job.name)
	if expected_input_lot and not job_doc.get("input_production_lot") and job_doc.meta.has_field("input_production_lot"):
		job_doc.db_set("input_production_lot", expected_input_lot, update_modified=False)
	job_doc.db_set({"packing_qr_verified_lot": lot.name, "packing_qr_verified_qty": qty, "packing_qr_verified_uom": lot.quantity_uom, "packing_qr_verified_at": frappe.utils.now_datetime(), "packing_qr_verified_by": frappe.session.user}, update_modified=False)
	return {
		"production_lot": lot.name,
		"source_operation": lot.operation,
		"item": lot.finished_item,
		"batch": batch,
		"qc_status": "Accepted" if lot.quality_inspection else "Not Required",
		"quantity": qty,
		"eligible_quantity": flt(lot.produced_quantity),
		"uom": lot.quantity_uom,
	}


@frappe.whitelist()
def resolve_phase5_qr(payload):
	if frappe.session.user != "Administrator" and not set(frappe.get_roles()).intersection({"Manufacturing User", "Manufacturing Manager", "Stock User", "Stock Manager", "Quality Manager", "System Manager"}):
		frappe.throw(_("You are not authorized to verify packing labels."), frappe.PermissionError)
	payload = str(payload or "").strip()
	if not payload.startswith(QR_PREFIX):
		frappe.throw(_("Invalid SmartEdge QR payload."))
	lot_name = payload[len(QR_PREFIX):].strip()
	if not lot_name or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", lot_name):
		frappe.throw(_("Invalid SmartEdge Production Lot reference."))
	lot = frappe.get_doc("SmartEdge Production Lot", lot_name)
	if not lot.actual_end or flt(lot.produced_quantity) <= 0:
		frappe.throw(_("Production Lot {0} has no completed output.").format(lot.name))
	if not lot.quality_inspection:
		frappe.throw(_("Production Lot {0} has no Quality Inspection.").format(lot.name))
	qc = frappe.db.get_value("Quality Inspection", lot.quality_inspection, ["docstatus", "custom_smartedge_qc_status", "custom_accepted_qty"], as_dict=True)
	if not qc or qc.docstatus != 1 or qc.custom_smartedge_qc_status != "Accepted":
		frappe.throw(_("Production Lot {0} is not QC accepted.").format(lot.name))
	accepted = flt(qc.custom_accepted_qty)
	if accepted <= 0:
		frappe.throw(_("Production Lot {0} has zero accepted quantity.").format(lot.name))
	meta = frappe.get_meta("Batch", cached=True)
	batch = frappe.db.get_value("Batch", {"custom_operation_lot": lot.name}, "name") if meta.has_field("custom_operation_lot") else None
	if not batch:
		frappe.throw(_("No ERPNext Batch is linked to Production Lot {0}.").format(lot.name))
	item = frappe.db.get_value("Item", lot.finished_item, ["item_name", "stock_uom"], as_dict=True)
	if not item:
		frappe.throw(_("Finished Item {0} no longer exists.").format(lot.finished_item))
	from erpnext.stock.doctype.batch.batch import get_batch_qty
	balances = get_batch_qty(item_code=lot.finished_item) or []
	warehouse_rows = [row for row in balances if row.get("batch_no") == batch and flt(row.get("qty")) > 0]
	available = sum(flt(row.get("qty")) for row in warehouse_rows)
	if available <= 0:
		frappe.throw(_("Batch {0} has no available FG stock for packing.").format(batch))
	return {"production_lot": lot.name, "item_code": lot.finished_item, "item_name": lot.finished_item_name or item.item_name, "batch": batch, "qc_status": qc.custom_smartedge_qc_status, "accepted_quantity": accepted, "accepted_uom": lot.quantity_uom or item.stock_uom, "accepted_kg": flt(lot.produced_quantity_kg) or None, "stock_uom": item.stock_uom, "available_stock": available, "warehouse": warehouse_rows[0].get("warehouse"), "traceability_reference": f"{QR_PREFIX}{lot.name}", "packing_eligible": True}
