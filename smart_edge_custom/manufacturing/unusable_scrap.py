"""Controlled storage, disposal, and history for unusable production scrap."""

import hashlib
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime

EPSILON = 0.000001
DISPOSAL_ROLES = {"Manufacturing Manager", "Stock Manager"}


def _number(value, label):
	try:
		value = float(str(value).replace(",", ""))
	except (TypeError, ValueError, OverflowError):
		frappe.throw(_("{0} must be a valid number.").format(label))
	if not math.isfinite(value) or value <= EPSILON:
		frappe.throw(_("{0} must be greater than zero.").format(label))
	return flt(value, 6)


def _require_disposal_authority():
	if frappe.session.user != "Administrator" and not DISPOSAL_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Manufacturing Manager or Stock Manager authorization is required for scrap disposal."), frappe.PermissionError)


def _item(item_code):
	row = frappe.db.get_value(
		"Item", item_code, ["name", "stock_uom", "is_stock_item", "disabled", "has_batch_no"], as_dict=True,
	)
	if not row or row.disabled or not row.is_stock_item:
		frappe.throw(_("Configured Unusable Scrap Item must be an enabled stock Item."))
	return row


def _warehouse(warehouse, company):
	row = frappe.db.get_value("Warehouse", warehouse, ["name", "company", "disabled", "is_group"], as_dict=True)
	if not row or row.disabled or row.is_group or row.company != company:
		frappe.throw(_("Authorized Unusable Scrap Warehouse must be an enabled non-group Warehouse for {0}.").format(company))
	return row.name


def validate_unusable_configuration(config):
	if bool(config.get("unusable_scrap_item")) != bool(config.get("unusable_scrap_warehouse")):
		frappe.throw(_("Configure both Unusable Scrap Item and Authorized Unusable Scrap Warehouse, or leave both empty."))
	if not config.get("unusable_scrap_item"):
		return
	item = _item(config.unusable_scrap_item)
	_warehouse(config.unusable_scrap_warehouse, config.company)
	if config.unusable_scrap_item in {
		config.usable_scrap_item, config.ground_material_item, config.recovered_material_item,
	}:
		frappe.throw(_("Unusable Scrap Item must be separate from usable scrap and recovery Items."))
	return item


def validate_unusable_scrap_lot(doc):
	if flt(doc.quantity) <= 0:
		frappe.throw(_("Recorded unusable scrap quantity must be greater than zero."))
	if flt(doc.disposed_quantity) > flt(doc.stored_quantity) + EPSILON:
		frappe.throw(_("Disposed quantity cannot exceed stored quantity."))
	old = doc.get_doc_before_save()
	if old and old.storage_stock_entry:
		immutable = (
			"production_job", "production_lot", "source_operation", "source_workstation", "source_employee",
			"source_item", "source_batch", "scrap_item", "scrap_batch", "warehouse", "quantity", "uom",
		)
		if any(doc.has_value_changed(fieldname) for fieldname in immutable):
			frappe.throw(_("Posted unusable-scrap source and stock identity cannot be changed."))


def _config(source_item):
	name = frappe.db.get_value("SmartEdge Recovery Material", {"source_item": source_item, "disabled": 0}, "name")
	return frappe.get_doc("SmartEdge Recovery Material", name) if name else None


def _batch_id(job):
	return "UNUSABLE-" + hashlib.sha256(job.encode()).hexdigest()[:16].upper()


def _create_batch(lot, item):
	if not item.has_batch_no:
		return None
	batch_id = _batch_id(lot.production_job)
	if frappe.db.exists("Batch", batch_id):
		batch = frappe.get_doc("Batch", batch_id)
		if batch.item != item.name or batch.get("custom_smartedge_unusable_scrap_lot") != lot.name:
			frappe.throw(_("Unusable scrap Batch identity conflicts with another stock record."))
		return batch.name
	batch = frappe.get_doc({
		"doctype": "Batch", "batch_id": batch_id, "item": item.name,
		"item_name": frappe.db.get_value("Item", item.name, "item_name"), "disabled": 0,
		"manufacturing_date": get_datetime(lot.movement_datetime).date(),
		"custom_batch_purpose": "Physical Stock",
		"custom_smartedge_recovery_stage": "Unusable Scrap",
		"custom_smartedge_source_batch": lot.source_batch,
		"custom_smartedge_source_production_job": lot.production_job,
		"custom_smartedge_unusable_scrap_lot": lot.name,
	})
	batch.insert(ignore_permissions=True)
	return batch.name


def prepare_unusable_scrap(job, production_lot, source_batch=None):
	"""Create one pending storage record; inventory is posted only by its manufacturing transaction."""
	quantity = flt(job.get("unusable_scrap"))
	if quantity <= EPSILON:
		return None
	existing = frappe.db.get_value("SmartEdge Unusable Scrap Lot", {"production_job": job.name}, "name", for_update=True)
	if existing:
		lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", existing)
		if abs(flt(lot.quantity) - quantity) > EPSILON:
			frappe.throw(_("Unusable scrap for Production Job {0} differs from its recorded scrap lot.").format(job.name))
		return lot.name
	config = _config(production_lot.finished_item)
	if not config or not config.get("unusable_scrap_item") or not config.get("unusable_scrap_warehouse"):
		frappe.throw(_("Configure an Unusable Scrap Item and authorized Warehouse for {0} before recording unusable scrap.").format(production_lot.finished_item))
	item = validate_unusable_configuration(config)
	lot = frappe.get_doc({
		"doctype": "SmartEdge Unusable Scrap Lot", "status": "Recorded",
		"company": job.company, "production_job": job.name,
		"final_eb_bom_generator": job.final_eb_bom_generator, "production_lot": production_lot.get("name"),
		"source_operation": job.operation, "source_workstation": job.workstation,
		"source_employee": job.operator_employee, "source_item": production_lot.finished_item,
		"source_batch": source_batch, "movement_datetime": job.actual_end or now_datetime(),
		"scrap_item": item.name, "warehouse": config.unusable_scrap_warehouse,
		"quantity": quantity, "uom": item.stock_uom, "stored_quantity": 0,
		"disposed_quantity": 0, "available_quantity": 0,
	})
	lot.insert(ignore_permissions=True)
	batch = _create_batch(lot, item)
	lot.db_set({"status": "Pending Storage", "scrap_batch": batch}, update_modified=False)
	return lot.name


def append_storage_output(stock_entry, unusable_scrap_lot):
	lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", unusable_scrap_lot)
	if lot.storage_stock_entry:
		if lot.storage_stock_entry != stock_entry.name:
			frappe.throw(_("Unusable scrap lot {0} is already linked to another storage transaction.").format(lot.name))
		return None
	item = _item(lot.scrap_item)
	row = stock_entry.append("items", {
		"item_code": item.name, "qty": lot.quantity, "uom": item.stock_uom,
		"stock_uom": item.stock_uom, "conversion_factor": 1,
		"t_warehouse": lot.warehouse,
		"type": "Scrap",
		"allow_zero_valuation_rate": 1,
	})
	return (row, lot.scrap_batch) if lot.scrap_batch else None


def attach_storage_bundle(stock_entry, pair):
	if not pair:
		return
	row, batch = pair
	from smart_edge_custom.manufacturing.scrap_recovery import _bundle
	_bundle(stock_entry, row, batch, row.transfer_qty, row.t_warehouse, "Inward")


def mark_stored(lot_names, stock_entry):
	for name in lot_names:
		frappe.db.set_value("SmartEdge Unusable Scrap Lot", name, {
			"status": "Stored", "storage_stock_entry": stock_entry,
			"stored_quantity": frappe.db.get_value("SmartEdge Unusable Scrap Lot", name, "quantity"),
			"available_quantity": frappe.db.get_value("SmartEdge Unusable Scrap Lot", name, "quantity"),
		}, update_modified=False)


def _available(lot):
	entries = frappe.get_all("Stock Entry", filters={
		"custom_smartedge_unusable_scrap_lot": lot.name,
		"custom_smartedge_scrap_movement_type": "Unusable Scrap Disposal", "docstatus": 1,
	}, pluck="name")
	issued = flt(sum(flt(value) for value in frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": ["in", entries or [""]], "item_code": lot.scrap_item},
		pluck="transfer_qty",
	)))
	record_balance = max(flt(lot.stored_quantity) - issued, 0)
	if lot.scrap_batch:
		from erpnext.stock.doctype.batch.batch import get_batch_qty
		physical = flt(get_batch_qty(batch_no=lot.scrap_batch, warehouse=lot.warehouse, item_code=lot.scrap_item))
	else:
		physical = flt(frappe.db.get_value("Bin", {"item_code": lot.scrap_item, "warehouse": lot.warehouse}, "actual_qty"))
	return min(record_balance, physical)


def set_storage_metadata(stock_entry, lot_names):
	if len(lot_names) != 1:
		return
	lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", lot_names[0])
	for fieldname, value in {
		"custom_smartedge_recovery_stage": "Unusable Scrap Storage",
		"custom_smartedge_scrap_movement_type": "Unusable Scrap Transferred to Storage",
		"custom_smartedge_production_job": lot.production_job,
		"custom_smartedge_source_batch": lot.source_batch,
		"custom_smartedge_source_production_lot": lot.production_lot,
		"custom_smartedge_source_operation": lot.source_operation,
		"custom_smartedge_source_workstation": lot.source_workstation,
		"custom_smartedge_source_employee": lot.source_employee,
		"custom_smartedge_movement_workstation": lot.source_workstation,
		"custom_smartedge_movement_employee": lot.source_employee,
		"custom_smartedge_unusable_scrap_lot": lot.name,
	}.items():
		if stock_entry.meta.has_field(fieldname):
			stock_entry.set(fieldname, value)


def _refresh(lot_name, pending=False):
	lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", lot_name)
	available = max(_available(lot), 0) if lot.storage_stock_entry else 0
	disposed = max(flt(lot.stored_quantity) - available, 0)
	status = "Disposed" if lot.storage_stock_entry and available <= EPSILON else ("Pending Disposal" if pending else "Stored")
	lot.db_set({"status": status, "disposed_quantity": disposed, "available_quantity": available}, update_modified=False)
	return available


@frappe.whitelist()
def request_disposal(unusable_scrap_lot):
	_require_disposal_authority()
	frappe.db.get_value("SmartEdge Unusable Scrap Lot", unusable_scrap_lot, "name", for_update=True)
	lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", unusable_scrap_lot)
	lot.check_permission("write")
	if lot.status == "Disposed":
		return {"status": lot.status, "available_quantity": 0}
	if lot.status not in {"Stored", "Pending Disposal"} or not lot.storage_stock_entry:
		frappe.throw(_("Only successfully stored unusable scrap can be submitted for disposal."))
	available = _refresh(lot.name, pending=True)
	return {"status": "Pending Disposal" if available > EPSILON else "Disposed", "available_quantity": available}


def _validate_employee(employee, company):
	row = frappe.db.get_value("Employee", employee, ["status", "company"], as_dict=True)
	if not row or row.status != "Active" or row.company != company:
		frappe.throw(_("Responsible Employee must be active and belong to {0}.").format(company))


@frappe.whitelist()
def dispose_unusable_scrap(unusable_scrap_lot, quantity, reason, employee, disposal_datetime=None, remarks=None, request_id=None):
	_require_disposal_authority()
	if not request_id:
		frappe.throw(_("A disposal request ID is required for safe retry handling."))
	request_id = str(request_id).strip()[:140]
	frappe.db.get_value("SmartEdge Unusable Scrap Lot", unusable_scrap_lot, "name", for_update=True)
	existing = frappe.db.get_value("Stock Entry", {
		"custom_smartedge_unusable_scrap_lot": unusable_scrap_lot,
		"custom_smartedge_scrap_request_id": request_id,
		"docstatus": ["<", 2],
	}, ["name", "docstatus"], as_dict=True)
	if existing:
		if cint(existing.docstatus) != 1:
			frappe.throw(_("Draft disposal Stock Entry {0} requires review.").format(existing.name))
		return {"stock_entry": existing.name, "idempotent": 1}
	if frappe.db.exists("Stock Entry", {
		"custom_smartedge_unusable_scrap_lot": unusable_scrap_lot,
		"custom_smartedge_scrap_request_id": request_id, "docstatus": 2,
	}):
		frappe.throw(_("This disposal request was cancelled; use the controlled amendment workflow."))
	lot = frappe.get_doc("SmartEdge Unusable Scrap Lot", unusable_scrap_lot)
	lot.check_permission("write")
	if lot.status != "Pending Disposal":
		frappe.throw(_("Request disposal authorization before posting unusable scrap disposal."))
	qty = _number(quantity, _("Disposal Quantity"))
	if not str(reason or "").strip():
		frappe.throw(_("Disposal Reason is required."))
	_validate_employee(employee, lot.company)
	if lot.scrap_batch:
		frappe.db.get_value("Batch", lot.scrap_batch, "name", for_update=True)
	available = _available(lot)
	if qty > available + EPSILON:
		frappe.throw(_("Disposal quantity exceeds the available stored quantity of {0} {1}.").format(flt(available, 6), lot.uom))
	datetime_value = get_datetime(disposal_datetime or now_datetime())
	doc = frappe.new_doc("Stock Entry")
	doc.company = lot.company
	doc.purpose = "Material Issue"
	doc.set_stock_entry_type()
	doc.posting_date, doc.posting_time, doc.set_posting_time = datetime_value.date(), datetime_value.time(), 1
	doc.remarks = _("Unusable scrap disposal {0}: {1}{2}").format(lot.name, reason, f" — {remarks}" if remarks else "")
	for fieldname, value in {
		"custom_smartedge_scrap_movement_type": "Unusable Scrap Disposal",
		"custom_smartedge_production_job": lot.production_job,
		"custom_smartedge_source_batch": lot.source_batch,
		"custom_smartedge_source_production_lot": lot.production_lot,
		"custom_smartedge_source_operation": lot.source_operation,
		"custom_smartedge_source_workstation": lot.source_workstation,
		"custom_smartedge_source_employee": lot.source_employee,
		"custom_smartedge_unusable_scrap_lot": lot.name,
		"custom_smartedge_movement_employee": employee,
		"custom_smartedge_scrap_request_id": request_id,
		"custom_smartedge_disposal_reason": str(reason).strip(),
	}.items():
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, value)
	row = doc.append("items", {
		"item_code": lot.scrap_item, "qty": qty, "uom": lot.uom, "stock_uom": lot.uom,
		"conversion_factor": 1, "s_warehouse": lot.warehouse,
	})
	doc.insert(ignore_permissions=True)
	if lot.scrap_batch:
		from smart_edge_custom.manufacturing.scrap_recovery import _bundle
		_bundle(doc, row, lot.scrap_batch, row.transfer_qty, lot.warehouse, "Outward")
		doc.save(ignore_permissions=True)
	doc.submit()
	_refresh(lot.name, pending=True)
	return {"stock_entry": doc.name, "available_quantity": _available(lot)}


def reject_unusable_scrap_consumption(doc, method=None):
	"""Warehouse/batch separation is backed by server validation on every Stock Entry."""
	if doc.get("custom_smartedge_scrap_movement_type") == "Unusable Scrap Disposal":
		return
	warehouses = set(frappe.get_all("SmartEdge Recovery Material", filters={"disabled": 0, "unusable_scrap_warehouse": ["is", "set"]}, pluck="unusable_scrap_warehouse"))
	for row in doc.get("items") or []:
		if row.get("s_warehouse") in warehouses:
			frappe.throw(_("Unusable scrap in {0} is restricted to the authorized disposal workflow.").format(row.s_warehouse))
		if row.get("batch_no") and frappe.db.exists("Batch", {"name": row.batch_no, "custom_smartedge_unusable_scrap_lot": ["is", "set"]}):
			frappe.throw(_("Unusable scrap Batch {0} cannot be consumed by production or recovery.").format(row.batch_no))


def prevent_unusable_scrap_cancellation(doc, method=None):
	lot_names = frappe.get_all("SmartEdge Unusable Scrap Lot", filters={"storage_stock_entry": doc.name}, pluck="name")
	if doc.get("custom_smartedge_unusable_scrap_lot"):
		lot_names.append(doc.get("custom_smartedge_unusable_scrap_lot"))
	for lot_name in set(lot_names):
		if frappe.db.exists("Stock Entry", {
			"custom_smartedge_unusable_scrap_lot": lot_name,
			"custom_smartedge_scrap_movement_type": "Unusable Scrap Disposal", "docstatus": 1,
		}):
			frappe.throw(_("Cancel all unusable scrap disposal Stock Entries before cancelling its storage transaction."))


def sync_unusable_scrap_cancellation(doc, method=None):
	storage_lots = frappe.get_all("SmartEdge Unusable Scrap Lot", filters={"storage_stock_entry": doc.name}, pluck="name")
	for lot_name in storage_lots:
		frappe.db.set_value("SmartEdge Unusable Scrap Lot", lot_name, {
			"status": "Pending Storage", "stored_quantity": 0, "disposed_quantity": 0, "available_quantity": 0,
		}, update_modified=False)
	lot_name = doc.get("custom_smartedge_unusable_scrap_lot")
	if lot_name and doc.get("custom_smartedge_scrap_movement_type") == "Unusable Scrap Disposal":
		_refresh(lot_name, pending=True)


def get_job_scrap_movement_history(production_job):
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("read")
	rows = []
	usable_names = frappe.get_all("SmartEdge Usable Scrap Lot", filters={"production_job": job.name}, pluck="name")
	for name in usable_names:
		lot = frappe.get_doc("SmartEdge Usable Scrap Lot", name)
		common = {
			"source_production_job": job.name, "source_operation": lot.operation,
			"source_batch": lot.source_batch, "source_production_lot": lot.production_lot,
			"source_employee": lot.get("source_employee") or job.operator_employee,
			"source_workstation": lot.get("source_workstation") or job.workstation, "uom": lot.uom,
		}
		rows.append({**common, "movement_type": "Usable Scrap Generated", "status": lot.status,
			"quantity": lot.quantity, "target_warehouse": lot.warehouse, "stock_entry": lot.stock_entry,
			"movement_datetime": lot.creation})
		for recovery in frappe.get_all("SmartEdge Scrap Recovery", filters={"usable_scrap_lot": lot.name}, fields=["*"]):
			if recovery.grinder_stock_entry:
				rows.extend([
					{**common, "movement_type": "Usable Scrap Transferred to Grinder", "status": "Posted",
						"quantity": recovery.grinder_input_quantity, "source_warehouse": recovery.source_warehouse,
						"movement_employee": recovery.grinder_employee, "movement_workstation": recovery.grinder_workstation,
						"stock_entry": recovery.grinder_stock_entry, "movement_datetime": recovery.grinder_datetime},
					{**common, "movement_type": "Grinder Output", "status": "Posted",
						"source_batch": recovery.source_batch, "quantity": recovery.ground_material_quantity,
						"movement_employee": recovery.grinder_employee, "movement_workstation": recovery.grinder_workstation,
						"stock_entry": recovery.grinder_stock_entry, "movement_datetime": recovery.grinder_datetime},
				])
			if recovery.pulverizer_stock_entry:
				rows.append({**common, "movement_type": "Pulverizer Output", "status": "Posted",
					"source_batch": recovery.ground_material_batch, "quantity": recovery.recovered_material_quantity,
					"movement_employee": recovery.pulverizer_employee, "movement_workstation": recovery.pulverizer_workstation,
					"stock_entry": recovery.pulverizer_stock_entry, "movement_datetime": recovery.pulverizer_datetime,
					"quality_inspection": recovery.quality_inspection})
			for allocation in frappe.get_all("SmartEdge Recovered Material Allocation", filters={"recovery": recovery.name},
				fields=["production_job", "allocated_quantity", "uom", "status", "consumption_stock_entry", "creation"]):
				rows.append({**common, "movement_type": "Recovered Material Reused", "status": allocation.status,
					"source_batch": recovery.recovered_material_batch, "quantity": allocation.allocated_quantity,
					"uom": allocation.uom, "stock_entry": allocation.consumption_stock_entry,
					"movement_datetime": allocation.creation, "related_production_job": allocation.production_job})
	for lot in frappe.get_all("SmartEdge Unusable Scrap Lot", filters={"production_job": job.name}, fields=["*"]):
		common = {"source_production_job": job.name, "source_operation": lot.source_operation, "source_batch": lot.source_batch,
			"source_production_lot": lot.production_lot, "source_employee": lot.source_employee,
			"source_workstation": lot.source_workstation, "quantity": lot.quantity, "uom": lot.uom}
		rows.append({**common, "movement_type": "Unusable Scrap Generated", "status": "Recorded", "movement_datetime": lot.movement_datetime})
		if lot.storage_stock_entry:
			rows.append({**common, "movement_type": "Unusable Scrap Transferred to Storage", "status": "Stored" if lot.status != "Cancelled" else "Cancelled",
				"target_warehouse": lot.warehouse, "stock_entry": lot.storage_stock_entry, "movement_datetime": lot.movement_datetime})
		for entry in frappe.get_all("Stock Entry", filters={"custom_smartedge_unusable_scrap_lot": lot.name,
			"custom_smartedge_scrap_movement_type": "Unusable Scrap Disposal"},
			fields=["name", "docstatus", "posting_date", "posting_time", "custom_smartedge_movement_employee", "remarks"]):
			qty = frappe.db.get_value("Stock Entry Detail", {"parent": entry.name, "item_code": lot.scrap_item}, "transfer_qty")
			rows.append({**common, "movement_type": "Unusable Scrap Disposal" if entry.docstatus != 2 else "Scrap Movement Reversal",
				"status": {0: "Draft", 1: "Disposed", 2: "Cancelled"}.get(entry.docstatus), "quantity": qty,
				"source_warehouse": lot.warehouse, "movement_employee": entry.custom_smartedge_movement_employee,
				"stock_entry": entry.name, "movement_datetime": f"{entry.posting_date} {entry.posting_time}", "remarks": entry.remarks})
	return sorted(rows, key=lambda row: str(row.get("movement_datetime") or ""))
