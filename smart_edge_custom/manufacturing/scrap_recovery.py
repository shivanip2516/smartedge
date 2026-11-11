"""Stock-controlled usable-scrap recovery and explicitly permitted reuse."""

import hashlib
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, nowdate

EPSILON = 0.000001
RECOVERY_ROLES = ("Manufacturing User", "Manufacturing Manager", "Stock User", "Stock Manager")


def _positive(value, label, allow_zero=False):
	try:
		number = float(str(value).replace(",", ""))
	except (TypeError, ValueError, OverflowError):
		frappe.throw(_("{0} must be a valid number.").format(label))
	if not math.isfinite(number) or number < 0 or (not allow_zero and number <= EPSILON):
		frappe.throw(_("{0} must be greater than zero.").format(label))
	return flt(number, 6)


def _require_recovery_user():
	if frappe.session.user == "Administrator":
		return
	if not set(RECOVERY_ROLES).intersection(frappe.get_roles()):
		frappe.throw(_("A manufacturing or stock role is required for scrap recovery."), frappe.PermissionError)


def _stock_item(item_code, label):
	if not item_code:
		frappe.throw(_("{0} is required.").format(label))
	item = frappe.db.get_value(
		"Item", item_code,
		["name", "item_name", "stock_uom", "is_stock_item", "disabled", "has_batch_no"],
		as_dict=True,
	)
	if not item or item.disabled or not item.is_stock_item:
		frappe.throw(_("{0} must be an enabled stock Item.").format(label))
	if cint(item.get("has_batch_no")) != 1:
		frappe.throw(_("{0} ({1}) must have batch tracking enabled. The authoritative Item.has_batch_no value is {2}.").format(label, item_code, item.get("has_batch_no") or 0))
	return item


def _warehouse(name, company, label):
	row = frappe.db.get_value("Warehouse", name, ["name", "company", "is_group", "disabled"], as_dict=True)
	if not row or row.disabled or row.is_group or row.company != company:
		frappe.throw(_("{0} must be an enabled non-group Warehouse for {1}.").format(label, company))
	return row.name


def _item_in_group(item_code, configured_group):
	if not configured_group:
		return True
	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if item_group == configured_group:
		return True
	group = frappe.db.get_value("Item Group", configured_group, ["lft", "rgt"], as_dict=True)
	child = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	return bool(group and child and group.lft <= child.lft and child.rgt <= group.rgt)


def validate_recovery_material(doc):
	items = [
		_stock_item(doc.usable_scrap_item, _("Usable Scrap Item")),
		_stock_item(doc.ground_material_item, _("Ground Material Item")),
		_stock_item(doc.recovered_material_item, _("Recovered Material Item")),
	]
	uoms = {item.stock_uom for item in items}
	if len(uoms) != 1:
		frappe.throw(_("Usable scrap, ground material, and recovered material must use one stock UOM."))
	for fieldname, label in (
		("usable_scrap_warehouse", _("Usable Scrap Warehouse")),
		("ground_material_warehouse", _("Ground Material Warehouse")),
		("recovered_material_warehouse", _("Recovered Material Warehouse")),
	):
		_warehouse(doc.get(fieldname), doc.company, label)
	from smart_edge_custom.manufacturing.unusable_scrap import validate_unusable_configuration

	validate_unusable_configuration(doc)


def validate_reuse_rule(doc):
	if not doc.recovered_material_item and not doc.recovered_item_group:
		frappe.throw(_("Select a recovered material Item or Item Group."))
	if not doc.target_item and not doc.target_item_group:
		frappe.throw(_("Select a permitted target Item or Item Group."))
	for fieldname, label in (
		("maximum_reuse_percentage", _("Maximum Reuse Percentage")),
		("maximum_reuse_quantity", _("Maximum Reuse Quantity")),
	):
		value = flt(doc.get(fieldname))
		if value < 0:
			frappe.throw(_("{0} cannot be negative.").format(label))
	if flt(doc.maximum_reuse_percentage) > 100:
		frappe.throw(_("Maximum Reuse Percentage cannot exceed 100."))


def _material_config(source_item):
	name = frappe.db.get_value("SmartEdge Recovery Material", {"source_item": source_item, "disabled": 0}, "name")
	return frappe.get_doc("SmartEdge Recovery Material", name) if name else None


def get_recovery_uom(source_item):
	"""Return the configured scrap stock UOM without requiring recovery setup to be migrated."""
	if not source_item or not frappe.db.exists("DocType", "SmartEdge Recovery Material"):
		return None
	item = frappe.db.get_value(
		"SmartEdge Recovery Material",
		{"source_item": source_item, "disabled": 0},
		"usable_scrap_item",
	)
	return frappe.db.get_value("Item", item, "stock_uom") if item else None


def get_unusable_scrap_uom(source_item):
	if not source_item or not frappe.db.exists("DocType", "SmartEdge Recovery Material"):
		return None
	item = frappe.db.get_value(
		"SmartEdge Recovery Material",
		{"source_item": source_item, "disabled": 0},
		"unusable_scrap_item",
	)
	return frappe.db.get_value("Item", item, "stock_uom") if item else None


def _batch_id(prefix, identity):
	digest = hashlib.sha256(identity.encode()).hexdigest()[:16].upper()
	return f"{prefix}-{digest}"


def _create_recovery_batch(item_code, batch_id, stage, source_batch=None, production_job=None, scrap_lot=None, recovery=None, manufacturing_date=None):
	if frappe.db.exists("Batch", batch_id):
		batch = frappe.get_doc("Batch", batch_id)
		if batch.item != item_code:
			frappe.throw(_("Recovery Batch identity {0} belongs to another Item.").format(batch_id))
		return batch.name
	meta = frappe.get_meta("Batch")
	if not meta.has_field("custom_smartedge_recovery_stage"):
		frappe.throw(_("Scrap recovery Batch metadata is not installed. Run migration before recovery posting."))
	batch = frappe.get_doc({
		"doctype": "Batch",
		"batch_id": batch_id,
		"item": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name"),
		"manufacturing_date": getdate(manufacturing_date or nowdate()),
		"disabled": 0,
		"custom_batch_purpose": "Physical Stock",
		"custom_smartedge_recovery_stage": stage,
		"custom_smartedge_source_batch": source_batch,
		"custom_smartedge_source_production_job": production_job,
		"custom_smartedge_usable_scrap_lot": scrap_lot,
		"custom_smartedge_scrap_recovery": recovery,
	})
	batch.insert(ignore_permissions=True)
	return batch.name


def _bundle(stock_entry, row, batch_no, quantity, warehouse, direction):
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	bundle = SerialBatchCreation({
		"item_code": row.item_code,
		"warehouse": warehouse,
		"posting_date": stock_entry.posting_date,
		"posting_time": stock_entry.posting_time,
		"voucher_type": stock_entry.doctype,
		"voucher_no": stock_entry.name,
		"voucher_detail_no": row.name,
		"qty": flt(quantity) * (-1 if direction == "Outward" else 1),
		"type_of_transaction": direction,
		"company": stock_entry.company,
		"do_not_submit": True,
	}).make_serial_and_batch_bundle(batch_nos={batch_no: flt(quantity)})
	row.serial_and_batch_bundle = bundle.name
	row.use_serial_batch_fields = 0


def _stock_entry(reference, company, purpose, rows, links=None, posting_datetime=None):
	existing = frappe.get_all("Stock Entry", filters={"remarks": reference, "docstatus": ["<", 2]}, fields=["name", "docstatus"])
	if len(existing) > 1:
		frappe.throw(_("Multiple Stock Entries exist for {0}; review them before retrying.").format(reference))
	if existing:
		if existing[0].docstatus != 1:
			frappe.throw(_("Draft Stock Entry {0} exists; review it before retrying.").format(existing[0].name))
		return existing[0].name
	if frappe.db.exists("Stock Entry", {"remarks": reference, "docstatus": 2}):
		frappe.throw(_("The Stock Entry for {0} was cancelled; use the controlled amendment workflow.").format(reference))
	datetime_value = get_datetime(posting_datetime or now_datetime())
	doc = frappe.new_doc("Stock Entry")
	doc.company = company
	doc.purpose = purpose
	doc.set_stock_entry_type()
	doc.posting_date = datetime_value.date()
	doc.posting_time = datetime_value.time()
	doc.set_posting_time = 1
	doc.remarks = reference
	for fieldname, value in (links or {}).items():
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, value)
	for row in rows:
		item = _stock_item(row["item_code"], _("Recovery Item"))
		line = doc.append("items", {
			"item_code": item.name,
			"qty": flt(row["qty"]),
			"uom": item.stock_uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": 1,
			"s_warehouse": row.get("s_warehouse"),
			"t_warehouse": row.get("t_warehouse"),
			"allow_zero_valuation_rate": 1 if purpose == "Material Receipt" else 0,
		})
		row["line"] = line
	doc.insert(ignore_permissions=True)
	for row in rows:
		warehouse = row.get("s_warehouse") or row.get("t_warehouse")
		_bundle(doc, row["line"], row["batch_no"], row["qty"], warehouse, "Outward" if row.get("s_warehouse") else "Inward")
	doc.save(ignore_permissions=True)
	doc.submit()
	from smart_edge_custom.manufacturing.batch_genealogy import capture_stock_entry

	capture_stock_entry(doc)
	return doc.name


def _batch_available(item_code, batch_no, warehouse):
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	quantity = get_batch_qty(batch_no=batch_no, warehouse=warehouse, item_code=item_code)
	return flt(quantity)


def _assert_available(item_code, batch_no, warehouse, required, label):
	frappe.db.get_value("Batch", batch_no, "name", for_update=True)
	available = _batch_available(item_code, batch_no, warehouse)
	if available + EPSILON < flt(required):
		frappe.throw(_("{0} has only {1} {2} available; {3} is required.").format(
			label, flt(available, 6), frappe.db.get_value("Item", item_code, "stock_uom"), flt(required, 6)
		))
	return available


def register_usable_scrap(job, production_lot, source_batch=None):
	"""Post one physical usable-scrap receipt for one completed Production Job."""
	quantity = flt(job.get("usable_scrap"))
	if quantity <= EPSILON:
		return None
	existing = frappe.db.get_value("SmartEdge Usable Scrap Lot", {"production_job": job.name}, "name", for_update=True)
	if existing:
		lot = frappe.get_doc("SmartEdge Usable Scrap Lot", existing)
		if lot.status == "Cancelled" or frappe.db.get_value("Stock Entry", lot.stock_entry, "docstatus") == 2:
			frappe.throw(_("Usable scrap receipt for completed Production Job {0} was cancelled; use an approved amendment workflow.").format(job.name))
		if abs(flt(lot.quantity) - quantity) > EPSILON:
			frappe.throw(_("Usable scrap for completed Production Job {0} differs from its posted scrap lot.").format(job.name))
		return lot.name
	config = _material_config(production_lot.finished_item)
	if not config:
		frappe.throw(_("Configure an enabled SmartEdge Recovery Material mapping for {0} before recording usable scrap.").format(production_lot.finished_item))
	validate_recovery_material(config)
	scrap_item = _stock_item(config.usable_scrap_item, _("Usable Scrap Item"))
	batch_id = _batch_id("SCRAP", job.name)
	lot = frappe.get_doc({
		"doctype": "SmartEdge Usable Scrap Lot",
		"status": "Active",
		"company": job.company,
		"production_job": job.name,
		"production_lot": production_lot.name,
		"operation": job.operation,
		"source_workstation": job.workstation,
		"source_employee": job.operator_employee,
		"source_item": production_lot.finished_item,
		"source_batch": source_batch,
		"scrap_item": config.usable_scrap_item,
		# The Batch Link must not reference a generated name before the ERPNext
		# Batch exists. It is linked immediately after _create_recovery_batch().
		"scrap_batch": None,
		"warehouse": config.usable_scrap_warehouse,
		"quantity": quantity,
		"uom": scrap_item.stock_uom,
	})
	lot.insert(ignore_permissions=True)
	batch = _create_recovery_batch(
		config.usable_scrap_item, batch_id, "Usable Scrap", source_batch=source_batch,
		production_job=job.name, scrap_lot=lot.name, manufacturing_date=job.actual_end,
	)
	reference = f"SmartEdge usable scrap receipt {job.name}"
	stock_entry = _stock_entry(reference, job.company, "Material Receipt", [{
		"item_code": config.usable_scrap_item,
		"qty": quantity,
		"t_warehouse": config.usable_scrap_warehouse,
		"batch_no": batch,
	}], {
		"custom_smartedge_recovery_stage": "Usable Scrap Collection",
		"custom_smartedge_scrap_movement_type": "Usable Scrap Generated",
		"custom_smartedge_production_job": job.name,
		"custom_smartedge_source_batch": source_batch,
		"custom_smartedge_source_production_lot": production_lot.name,
		"custom_smartedge_source_operation": job.operation,
		"custom_smartedge_source_workstation": job.workstation,
		"custom_smartedge_source_employee": job.operator_employee,
		"custom_smartedge_movement_workstation": job.workstation,
		"custom_smartedge_movement_employee": job.operator_employee,
		"custom_smartedge_usable_scrap_lot": lot.name,
	}, job.actual_end)
	lot.db_set({"scrap_batch": batch, "stock_entry": stock_entry}, update_modified=False)
	return lot.name


@frappe.whitelist()
def get_scrap_lot_context(usable_scrap_lot):
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", usable_scrap_lot)
	lot.check_permission("read")
	config = _material_config(lot.source_item)
	if not config:
		frappe.throw(_("No enabled recovery material mapping exists for {0}.").format(lot.source_item))
	return {
		"company": lot.company,
		"material_configuration": config.name,
		"source_item": lot.scrap_item,
		"source_batch": lot.scrap_batch,
		"source_warehouse": lot.warehouse,
		"available_scrap_quantity": _batch_available(lot.scrap_item, lot.scrap_batch, lot.warehouse),
		"uom": lot.uom,
		"ground_material_item": config.ground_material_item,
		"recovered_material_item": config.recovered_material_item,
		"qc_required": config.qc_required,
		"grinder_datetime": now_datetime(),
	}


def validate_recovery_document(doc):
	if not doc.usable_scrap_lot:
		return
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot)
	if frappe.db.exists("Batch", {"name": lot.scrap_batch, "custom_smartedge_unusable_scrap_lot": ["is", "set"]}):
		frappe.throw(_("Unusable scrap cannot enter Grinder or Pulverizer recovery."))
	config = _material_config(lot.source_item)
	if not config:
		frappe.throw(_("An enabled recovery material mapping is required."))
	values = {
		"company": lot.company,
		"material_configuration": config.name,
		"source_item": lot.scrap_item,
		"source_batch": lot.scrap_batch,
		"source_warehouse": lot.warehouse,
		"uom": lot.uom,
		"ground_material_item": config.ground_material_item,
		"recovered_material_item": config.recovered_material_item,
		"qc_required": config.qc_required,
	}
	for fieldname, value in values.items():
		if doc.get(fieldname) not in (None, "", value):
			frappe.throw(_("Recovery field {0} must match its configured scrap lot.").format(fieldname))
		doc.set(fieldname, value)
	for fieldname in ("grinder_input_quantity", "ground_material_quantity", "grinder_process_loss", "pulverizer_input_quantity", "recovered_material_quantity", "pulverizer_process_loss"):
		if doc.get(fieldname) not in (None, ""):
			_positive(doc.get(fieldname), doc.meta.get_label(fieldname), allow_zero=True)
	if doc.grinder_stock_entry and doc.has_value_changed("usable_scrap_lot"):
		frappe.throw(_("Posted Grinder source cannot be changed."))
	if doc.pulverizer_stock_entry and any(doc.has_value_changed(field) for field in ("ground_material_quantity", "ground_material_batch")):
		frappe.throw(_("Posted Pulverizer source cannot be changed."))


def _validate_employee_workstation(employee, workstation, company, stage):
	if not employee or frappe.db.get_value("Employee", employee, "status") != "Active":
		frappe.throw(_("Select an active Employee for {0}.").format(stage))
	if frappe.db.get_value("Employee", employee, "company") != company:
		frappe.throw(_("{0} Employee must belong to {1}.").format(stage, company))
	if not workstation or cint(frappe.db.get_value("Workstation", workstation, "disabled")):
		frappe.throw(_("Select an enabled Workstation for {0}.").format(stage))


def _validate_balance(input_quantity, output_quantity, loss_quantity, stage):
	input_quantity = _positive(input_quantity, _("{0} Input").format(stage))
	output_quantity = _positive(output_quantity, _("{0} Output").format(stage))
	loss_quantity = _positive(loss_quantity, _("{0} Process Loss").format(stage), allow_zero=True)
	if abs(input_quantity - output_quantity - loss_quantity) > EPSILON:
		frappe.throw(_("{0} material balance must satisfy Input = Output + Process Loss.").format(stage))
	return input_quantity, output_quantity, loss_quantity


@frappe.whitelist()
def complete_grinder(recovery):
	_require_recovery_user()
	frappe.db.get_value("SmartEdge Scrap Recovery", recovery, "name", for_update=True)
	doc = frappe.get_doc("SmartEdge Scrap Recovery", recovery)
	doc.check_permission("write")
	if doc.status in {"Ground", "Recovered"} and doc.grinder_stock_entry:
		return {"stock_entry": doc.grinder_stock_entry, "ground_material_batch": doc.ground_material_batch}
	if doc.status != "Draft":
		frappe.throw(_("Only a Draft recovery can complete Grinder processing."))
	config = frappe.get_doc("SmartEdge Recovery Material", doc.material_configuration)
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot)
	_validate_employee_workstation(doc.grinder_employee, doc.grinder_workstation, doc.company, _("Grinder"))
	input_qty, output_qty, _loss = _validate_balance(
		doc.grinder_input_quantity, doc.ground_material_quantity, doc.grinder_process_loss, _("Grinder")
	)
	_assert_available(doc.source_item, doc.source_batch, doc.source_warehouse, input_qty, _("Usable Scrap Batch"))
	batch = _create_recovery_batch(
		doc.ground_material_item, _batch_id("GROUND", doc.name), "Ground Material",
		source_batch=doc.source_batch, production_job=frappe.db.get_value("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot, "production_job"),
		scrap_lot=doc.usable_scrap_lot, recovery=doc.name, manufacturing_date=doc.grinder_datetime,
	)
	stock_entry = _stock_entry(f"SmartEdge Grinder recovery {doc.name}", doc.company, "Repack", [
		{"item_code": doc.source_item, "qty": input_qty, "s_warehouse": doc.source_warehouse, "batch_no": doc.source_batch},
		{"item_code": doc.ground_material_item, "qty": output_qty, "t_warehouse": config.ground_material_warehouse, "batch_no": batch},
	], {
		"custom_smartedge_recovery_stage": "Grinder",
		"custom_smartedge_scrap_movement_type": "Usable Scrap Transferred to Grinder",
		"custom_smartedge_scrap_recovery": doc.name,
		"custom_smartedge_usable_scrap_lot": doc.usable_scrap_lot,
		"custom_smartedge_source_batch": doc.source_batch,
		"custom_smartedge_production_job": lot.production_job,
		"custom_smartedge_source_production_lot": lot.production_lot,
		"custom_smartedge_source_operation": lot.operation,
		"custom_smartedge_source_workstation": lot.source_workstation,
		"custom_smartedge_source_employee": lot.source_employee,
		"custom_smartedge_movement_workstation": doc.grinder_workstation,
		"custom_smartedge_movement_employee": doc.grinder_employee,
	}, doc.grinder_datetime)
	doc.update({
		"status": "Ground",
		"ground_material_batch": batch,
		"grinder_stock_entry": stock_entry,
		"available_scrap_quantity": _batch_available(doc.source_item, doc.source_batch, doc.source_warehouse),
		"pulverizer_input_quantity": output_qty,
		"pulverizer_datetime": now_datetime(),
	})
	doc.save(ignore_permissions=True)
	_update_scrap_lot_status(doc.usable_scrap_lot)
	return {"stock_entry": stock_entry, "ground_material_batch": batch}


@frappe.whitelist()
def complete_pulverizer(recovery):
	_require_recovery_user()
	frappe.db.get_value("SmartEdge Scrap Recovery", recovery, "name", for_update=True)
	doc = frappe.get_doc("SmartEdge Scrap Recovery", recovery)
	doc.check_permission("write")
	if doc.status == "Recovered" and doc.pulverizer_stock_entry:
		return {"stock_entry": doc.pulverizer_stock_entry, "recovered_material_batch": doc.recovered_material_batch}
	if doc.status != "Ground" or not doc.grinder_stock_entry or not doc.ground_material_batch:
		frappe.throw(_("Pulverizer processing requires a successfully posted Grinder output."))
	config = frappe.get_doc("SmartEdge Recovery Material", doc.material_configuration)
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot)
	_validate_employee_workstation(doc.pulverizer_employee, doc.pulverizer_workstation, doc.company, _("Pulverizer"))
	input_qty, output_qty, _loss = _validate_balance(
		doc.pulverizer_input_quantity, doc.recovered_material_quantity, doc.pulverizer_process_loss, _("Pulverizer")
	)
	_assert_available(doc.ground_material_item, doc.ground_material_batch, config.ground_material_warehouse, input_qty, _("Ground Material Batch"))
	batch = _create_recovery_batch(
		doc.recovered_material_item, _batch_id("RECOVERED", doc.name), "Recovered Material",
		source_batch=doc.ground_material_batch, production_job=frappe.db.get_value("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot, "production_job"),
		scrap_lot=doc.usable_scrap_lot, recovery=doc.name, manufacturing_date=doc.pulverizer_datetime,
	)
	stock_entry = _stock_entry(f"SmartEdge Pulverizer recovery {doc.name}", doc.company, "Repack", [
		{"item_code": doc.ground_material_item, "qty": input_qty, "s_warehouse": config.ground_material_warehouse, "batch_no": doc.ground_material_batch},
		{"item_code": doc.recovered_material_item, "qty": output_qty, "t_warehouse": config.recovered_material_warehouse, "batch_no": batch},
	], {
		"custom_smartedge_recovery_stage": "Pulverizer",
		"custom_smartedge_scrap_movement_type": "Pulverizer Output",
		"custom_smartedge_scrap_recovery": doc.name,
		"custom_smartedge_usable_scrap_lot": doc.usable_scrap_lot,
		"custom_smartedge_source_batch": doc.ground_material_batch,
		"custom_smartedge_production_job": lot.production_job,
		"custom_smartedge_source_production_lot": lot.production_lot,
		"custom_smartedge_source_operation": lot.operation,
		"custom_smartedge_source_workstation": lot.source_workstation,
		"custom_smartedge_source_employee": lot.source_employee,
		"custom_smartedge_movement_workstation": doc.pulverizer_workstation,
		"custom_smartedge_movement_employee": doc.pulverizer_employee,
	}, doc.pulverizer_datetime)
	doc.update({"status": "Recovered", "recovered_material_batch": batch, "pulverizer_stock_entry": stock_entry})
	if doc.qc_required:
		doc.quality_inspection = _ensure_recovered_quality_inspection(doc, output_qty)
	doc.save(ignore_permissions=True)
	return {"stock_entry": stock_entry, "recovered_material_batch": batch, "quality_inspection": doc.quality_inspection}


def _ensure_recovered_quality_inspection(recovery, quantity):
	existing = frappe.db.get_value("Quality Inspection", {
		"custom_smartedge_scrap_recovery": recovery.name, "docstatus": ["<", 2],
	}, "name")
	if existing:
		return existing
	doc = frappe.new_doc("Quality Inspection")
	doc.inspection_type = "In Process"
	doc.item_code = recovery.recovered_material_item
	doc.batch_no = recovery.recovered_material_batch
	doc.sample_size = quantity
	doc.inspected_by = frappe.session.user
	doc.manual_inspection = 1
	doc.custom_smartedge_scrap_recovery = recovery.name
	doc.insert(ignore_permissions=True)
	return doc.name


def _update_scrap_lot_status(scrap_lot):
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", scrap_lot)
	available = _batch_available(lot.scrap_item, lot.scrap_batch, lot.warehouse)
	status = "Consumed" if available <= EPSILON else "Active"
	if lot.status != status:
		lot.db_set("status", status, update_modified=False)


def _matching_rule(recovered_item, operation, target_item):
	for row in frappe.get_all("SmartEdge Recovery Reuse Rule", filters={"operation": operation, "disabled": 0}, fields=["*"]):
		if row.recovered_material_item and row.recovered_material_item != recovered_item:
			continue
		if row.recovered_item_group and not _item_in_group(recovered_item, row.recovered_item_group):
			continue
		if row.target_item and row.target_item != target_item:
			continue
		if row.target_item_group and not _item_in_group(target_item, row.target_item_group):
			continue
		return frappe._dict(row)
	return None


def _qc_accepted(recovery):
	if not recovery.quality_inspection:
		return False
	inspection = frappe.db.get_value(
		"Quality Inspection",
		recovery.quality_inspection,
		["docstatus", "status"],
		as_dict=True,
	)
	return bool(inspection and cint(inspection.docstatus) == 1 and inspection.status == "Accepted")


def validate_recovered_allocation(doc, for_consumption=False):
	job = frappe.get_doc("SmartEdge Production Job", doc.production_job)
	if job.status == "Cancelled" or (job.status == "Completed" and not for_consumption):
		frappe.throw(_("Recovered material can be allocated only before Production Job completion."))
	recovery = frappe.get_doc("SmartEdge Scrap Recovery", doc.recovery)
	if recovery.status != "Recovered" or not recovery.pulverizer_stock_entry:
		frappe.throw(_("Only successfully posted Pulverizer output can be reused."))
	target_item = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "finished_item")
	rule = _matching_rule(recovery.recovered_material_item, job.operation, target_item)
	if not rule:
		frappe.throw(_("Recovered material {0} is not permitted for operation {1} and target {2}.").format(
			recovery.recovered_material_item, job.operation, target_item
		))
	config = frappe.get_doc("SmartEdge Recovery Material", recovery.material_configuration)
	if (config.qc_required or rule.qc_required) and not _qc_accepted(recovery):
		frappe.throw(_("Recovered material QC must be submitted and Accepted before reuse."))
	quantity = _positive(doc.allocated_quantity, _("Quantity to Reuse"))
	frappe.db.get_value("Batch", recovery.recovered_material_batch, "name", for_update=True)
	available = _batch_available(recovery.recovered_material_item, recovery.recovered_material_batch, config.recovered_material_warehouse)
	reserved = flt(sum(flt(row.allocated_quantity) for row in frappe.get_all(
		"SmartEdge Recovered Material Allocation",
		filters={"recovered_material_batch": recovery.recovered_material_batch, "status": "Reserved", "name": ["!=", doc.name or ""]},
		fields=["allocated_quantity"],
	)))
	if quantity + reserved > available + EPSILON:
		frappe.throw(_("Recovered material allocation exceeds unreserved Batch stock."))
	job_allocated = flt(sum(flt(row.allocated_quantity) for row in frappe.get_all(
		"SmartEdge Recovered Material Allocation",
		filters={
			"production_job": job.name,
			"rule": rule.name,
			"status": ["in", ["Reserved", "Consumed"]],
			"name": ["!=", doc.name or ""],
		},
		fields=["allocated_quantity"],
	)))
	total_for_job = job_allocated + quantity
	if flt(rule.maximum_reuse_quantity) and total_for_job > flt(rule.maximum_reuse_quantity) + EPSILON:
		frappe.throw(_("Recovered material quantity exceeds the configured maximum of {0}.").format(rule.maximum_reuse_quantity))
	basis = flt(job.planned_qty_kg) or flt(job.planned_qty_meter)
	if flt(rule.maximum_reuse_percentage) and (not basis or total_for_job > basis * flt(rule.maximum_reuse_percentage) / 100 + EPSILON):
		frappe.throw(_("Recovered material quantity exceeds the configured {0}% limit.").format(rule.maximum_reuse_percentage))
	values = {
		"status": doc.status or "Reserved",
		"company": job.company,
		"production_operation": job.operation,
		"target_item": target_item,
		"rule": rule.name,
		"recovered_material_item": recovery.recovered_material_item,
		"recovered_material_batch": recovery.recovered_material_batch,
		"source_warehouse": config.recovered_material_warehouse,
		"available_quantity": available,
		"uom": frappe.db.get_value("Item", recovery.recovered_material_item, "stock_uom"),
	}
	for fieldname, value in values.items():
		doc.set(fieldname, value)


@frappe.whitelist()
def get_recovered_material_options(production_job):
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("read")
	target_item = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "finished_item")
	result = []
	for row in frappe.get_all("SmartEdge Scrap Recovery", filters={"status": "Recovered"}, fields=[
		"name", "material_configuration", "recovered_material_item", "recovered_material_batch", "recovered_material_quantity", "quality_inspection",
	]):
		rule = _matching_rule(row.recovered_material_item, job.operation, target_item)
		if not rule:
			continue
		config = frappe.get_doc("SmartEdge Recovery Material", row.material_configuration)
		if (config.qc_required or rule.qc_required) and not _qc_accepted(row):
			continue
		available = _batch_available(row.recovered_material_item, row.recovered_material_batch, config.recovered_material_warehouse)
		reserved = flt(sum(flt(value) for value in frappe.get_all("SmartEdge Recovered Material Allocation", filters={
			"recovered_material_batch": row.recovered_material_batch, "status": "Reserved",
		}, pluck="allocated_quantity")))
		available = max(available - reserved, 0)
		if available <= EPSILON:
			continue
		job_allocated = flt(sum(flt(value) for value in frappe.get_all(
			"SmartEdge Recovered Material Allocation",
			filters={"production_job": job.name, "rule": rule.name, "status": ["in", ["Reserved", "Consumed"]]},
			pluck="allocated_quantity",
		)))
		limit = available
		if flt(rule.maximum_reuse_quantity):
			limit = min(limit, max(flt(rule.maximum_reuse_quantity) - job_allocated, 0))
		basis = flt(job.planned_qty_kg) or flt(job.planned_qty_meter)
		if flt(rule.maximum_reuse_percentage) and basis:
			limit = min(limit, max(basis * flt(rule.maximum_reuse_percentage) / 100 - job_allocated, 0))
		if limit <= EPSILON:
			continue
		result.append({
			"recovery": row.name,
			"item_code": row.recovered_material_item,
			"batch_no": row.recovered_material_batch,
			"warehouse": config.recovered_material_warehouse,
			"available_quantity": available,
			"maximum_selectable_quantity": limit,
			"uom": frappe.db.get_value("Item", row.recovered_material_item, "stock_uom"),
		})
	return result


@frappe.whitelist()
def create_recovered_allocation(production_job, recovery, quantity):
	_require_recovery_user()
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("write")
	doc = frappe.get_doc({
		"doctype": "SmartEdge Recovered Material Allocation",
		"production_job": job.name,
		"recovery": recovery,
		"allocated_quantity": quantity,
	})
	doc.insert()
	return {"allocation": doc.name}


@frappe.whitelist()
def get_job_recovered_allocations(production_job):
	job = frappe.get_doc("SmartEdge Production Job", production_job)
	job.check_permission("read")
	return frappe.get_all(
		"SmartEdge Recovered Material Allocation",
		filters={"production_job": job.name, "status": ["!=", "Cancelled"]},
		fields=["name", "status", "recovered_material_item", "recovered_material_batch", "allocated_quantity", "uom", "consumption_stock_entry"],
		order_by="creation asc",
	)


@frappe.whitelist()
def cancel_recovered_allocation(allocation):
	_require_recovery_user()
	frappe.db.get_value("SmartEdge Recovered Material Allocation", allocation, "name", for_update=True)
	doc = frappe.get_doc("SmartEdge Recovered Material Allocation", allocation)
	doc.check_permission("write")
	if doc.status == "Cancelled":
		return {"status": doc.status}
	if doc.status != "Reserved" or doc.consumption_stock_entry:
		frappe.throw(_("Only an unconsumed recovered-material allocation can be cancelled."))
	doc.db_set("status", "Cancelled")
	return {"status": "Cancelled"}


def get_recovered_components(production=None, production_job=None):
	filters = {"status": "Reserved"}
	if production_job:
		filters["production_job"] = production_job
	elif production:
		jobs = frappe.get_all("SmartEdge Production Job", filters={"final_eb_bom_generator": production, "status": ["!=", "Cancelled"]}, pluck="name")
		filters["production_job"] = ["in", jobs or [""]]
	else:
		return [], [], []
	components, batches, allocations = [], [], []
	for name in frappe.get_all("SmartEdge Recovered Material Allocation", filters=filters, pluck="name"):
		doc = frappe.get_doc("SmartEdge Recovered Material Allocation", name)
		validate_recovered_allocation(doc, for_consumption=True)
		_assert_available(doc.recovered_material_item, doc.recovered_material_batch, doc.source_warehouse, doc.allocated_quantity, _("Recovered Material Batch"))
		components.append({
			"item_code": doc.recovered_material_item,
			"s_warehouse": doc.source_warehouse,
			"qty": flt(doc.allocated_quantity),
			"uom": doc.uom,
			"stock_uom": doc.uom,
			"conversion_factor": 1,
		})
		batches.append({doc.recovered_material_batch: flt(doc.allocated_quantity)})
		allocations.append(doc.name)
	return components, batches, allocations


def mark_recovered_components_consumed(allocation_names, stock_entry):
	for name in allocation_names:
		frappe.db.set_value("SmartEdge Recovered Material Allocation", name, {
			"status": "Consumed", "consumption_stock_entry": stock_entry,
		}, update_modified=False)


def append_recovered_components(stock_entry, production_job):
	components, batches, allocations = get_recovered_components(production_job=production_job)
	pairs = []
	for component, batch_group in zip(components, batches, strict=True):
		row = stock_entry.append("items", component)
		pairs.append((row, next(iter(batch_group))))
	return pairs, allocations


def attach_recovered_batch_bundles(stock_entry, row_batch_pairs):
	for row, batch_no in row_batch_pairs:
		_bundle(stock_entry, row, batch_no, row.transfer_qty, row.s_warehouse, "Outward")


@frappe.whitelist()
def get_recovery_trace(recovery):
	doc = frappe.get_doc("SmartEdge Scrap Recovery", recovery)
	doc.check_permission("read")
	lot = frappe.get_doc("SmartEdge Usable Scrap Lot", doc.usable_scrap_lot)
	allocations = frappe.get_all("SmartEdge Recovered Material Allocation", filters={"recovery": doc.name}, fields=[
		"name", "production_job", "target_item", "allocated_quantity", "status", "consumption_stock_entry",
	])
	return {
		"original_production_job": lot.production_job,
		"original_production_lot": lot.production_lot,
		"original_production_batch": lot.source_batch,
		"usable_scrap_lot": lot.name,
		"usable_scrap_batch": lot.scrap_batch,
		"scrap_receipt": lot.stock_entry,
		"grinder_stock_entry": doc.grinder_stock_entry,
		"ground_material_batch": doc.ground_material_batch,
		"pulverizer_stock_entry": doc.pulverizer_stock_entry,
		"recovered_material_batch": doc.recovered_material_batch,
		"quality_inspection": doc.quality_inspection,
		"reuse_allocations": allocations,
	}


def prevent_recovery_cancellation(doc, method=None):
	stage = doc.get("custom_smartedge_recovery_stage")
	if not stage:
		return
	recovery = doc.get("custom_smartedge_scrap_recovery")
	scrap_lot = doc.get("custom_smartedge_usable_scrap_lot")
	if stage == "Usable Scrap Collection" and scrap_lot:
		if frappe.db.exists("SmartEdge Scrap Recovery", {"usable_scrap_lot": scrap_lot, "grinder_stock_entry": ["is", "set"]}):
			frappe.throw(_("Cancel Grinder and downstream recovery Stock Entries before cancelling scrap collection."))
	elif stage == "Grinder" and recovery:
		if frappe.db.get_value("SmartEdge Scrap Recovery", recovery, "pulverizer_stock_entry"):
			frappe.throw(_("Cancel the Pulverizer Stock Entry before cancelling Grinder processing."))
	elif stage == "Pulverizer" and recovery:
		if frappe.db.exists("SmartEdge Recovered Material Allocation", {"recovery": recovery, "status": ["in", ["Reserved", "Consumed"]]}):
			frappe.throw(_("Cancel or remove recovered-material allocations before cancelling Pulverizer processing."))


def sync_recovery_cancellation(doc, method=None):
	stage = doc.get("custom_smartedge_recovery_stage")
	recovery = doc.get("custom_smartedge_scrap_recovery")
	scrap_lot = doc.get("custom_smartedge_usable_scrap_lot")
	if stage == "Usable Scrap Collection" and scrap_lot:
		frappe.db.set_value("SmartEdge Usable Scrap Lot", scrap_lot, "status", "Cancelled", update_modified=False)
	elif stage == "Grinder" and recovery:
		frappe.db.set_value("SmartEdge Scrap Recovery", recovery, {
			"status": "Draft", "grinder_stock_entry": None,
		}, update_modified=False)
		if scrap_lot:
			_update_scrap_lot_status(scrap_lot)
	elif stage == "Pulverizer" and recovery:
		frappe.db.set_value("SmartEdge Scrap Recovery", recovery, {
			"status": "Ground", "pulverizer_stock_entry": None,
		}, update_modified=False)
	for allocation in frappe.get_all(
		"SmartEdge Recovered Material Allocation",
		filters={"consumption_stock_entry": doc.name, "status": "Consumed"},
		pluck="name",
	):
		frappe.db.set_value("SmartEdge Recovered Material Allocation", allocation, {
			"status": "Reserved", "consumption_stock_entry": None,
		}, update_modified=False)
