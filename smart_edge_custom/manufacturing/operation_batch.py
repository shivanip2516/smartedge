"""Standard Batch identities for non-stock operations; stock remains ERPNext-owned."""
import hashlib

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

TRACE = "Operation Traceability"
PHYSICAL = "Physical Stock"
PURPOSE = "custom_batch_purpose"
LOT_LINK = "custom_operation_lot"


def schema_ready():
	return frappe.get_meta("Batch").has_field(LOT_LINK)


def batch_for_lot(lot_name):
	return frappe.db.get_value("Batch", {LOT_LINK: lot_name}, "name") if lot_name and schema_ready() else None


def _context(lot):
	job = frappe.get_doc("SmartEdge Production Job", lot.production_job)
	entry = frappe.get_doc("SmartEdge Production Plan Entry", job.plan_entry)
	item = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "finished_item")
	if (job.status != "Completed" or entry.plan_type != "Final EB" or entry.operation != job.operation
		or lot.operation != job.operation or lot.plan_entry != entry.name or lot.finished_item != item
		or cint(lot.operation_sequence) != cint(entry.sequence) or not job.actual_end
		or not lot.actual_end or flt(job.actual_qty_meter) <= 0):
		frappe.throw(_("An operation Batch requires a matching completed FG operation with recorded output and completion time."))
	return job, entry


def _batch_id(lot, job, entry):
	from smart_edge_custom.manufacturing.production_lot import get_operation_lot_name, get_operation_lot_prefix
	name = get_operation_lot_name(get_operation_lot_prefix(entry), job.name, entry.sequence)
	if frappe.db.exists("Batch", name):
		name = name[:110] + "-OP-" + hashlib.sha256(lot.name.encode()).hexdigest()[:24]
	return name


def ensure_operation_batch(lot, allow_create=False):
	"""Called inside successful completion; retries never backfill historical batches."""
	if isinstance(lot, str):
		lot = frappe.get_doc("SmartEdge Production Lot", lot)
	job, entry = _context(lot)
	from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
	if is_final_stock_posting_entry(entry):
		return link_final_batch(lot, job, entry) if allow_create else _physical_batches(job)
	if not schema_ready():
		if allow_create:
			frappe.throw(_("Operation Batch metadata is not installed. Run the smart_edge_custom operation Batch setup patch."))
		return []
	from smart_edge_custom.manufacturing.physical_operation_stock import enabled as physical_stock_enabled, post_operation
	if physical_stock_enabled():
		if allow_create:
			post_operation(lot, job, entry, None, _batch_id)
		else:
			posting = job.get("manufacturing_stock_entry")
			if not posting or frappe.db.get_value("Stock Entry", posting, "docstatus") != 1:
				frappe.throw(_("Completed physical operation {0} has no submitted stock posting. Review cancellation or historical migration before retrying.").format(job.name))
		return [frappe.db.get_value("Batch", {LOT_LINK: lot.name}, "name")]
	existing = batch_for_lot(lot.name)
	if existing:
		return [existing]
	if not allow_create:
		return []
	frappe.db.sql("select name from `tabSmartEdge Production Job` where name=%s for update", (job.name,))
	existing = batch_for_lot(lot.name)
	if existing:
		return [existing]
	if not frappe.db.get_value("Item", lot.finished_item, "has_batch_no"):
		frappe.throw(_("Finished Goods Item {0} must have Has Batch No enabled before operation Batch creation.").format(lot.finished_item))
	previous = frappe.db.get_value("SmartEdge Production Lot", {"production_job": entry.get("released_from_production_job")}, "name") if entry.get("released_from_production_job") else lot.input_production_lot
	batch = frappe.get_doc({"doctype": "Batch", "batch_id": _batch_id(lot, job, entry),
		"item": lot.finished_item, "item_name": frappe.db.get_value("Item", lot.finished_item, "item_name"),
		"reference_doctype": "SmartEdge Production Job", "reference_name": job.name,
		"manufacturing_date": getdate(job.actual_end), "disabled": 0,
		PURPOSE: TRACE, LOT_LINK: lot.name,
		"custom_operation_name": lot.operation, "custom_operation_sequence": lot.operation_sequence,
		"custom_operation_output_quantity": job.actual_qty_meter, "custom_operation_completed_at": job.actual_end,
		"custom_previous_operation_batch": batch_for_lot(previous),
	})
	batch.insert(ignore_permissions=True)
	return [batch.name]


def _physical_batches(job):
	stock_entry = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "final_manufacturing_stock_entry")
	if not stock_entry or frappe.db.get_value("Stock Entry", stock_entry, "docstatus") != 1:
		return []
	item = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "finished_item")
	return list(dict.fromkeys(frappe.get_all("SmartEdge Batch Genealogy Movement",
		filters={"stock_entry": stock_entry, "direction": "Output", "source_status": "Active", "item_code": item}, pluck="batch_no")))


def link_final_batch(lot, job, entry):
	"""Add identity links only, after the existing stock entry has been submitted."""
	names = _physical_batches(job)
	if not schema_ready() or len(names) != 1:
		return names
	previous_lot = frappe.db.get_value("SmartEdge Production Lot", {"production_job": entry.get("released_from_production_job")}, "name") if entry.get("released_from_production_job") else lot.input_production_lot
	previous = batch_for_lot(previous_lot)
	if previous:
		physical = frappe.get_doc("Batch", names[0])
		if physical.get("custom_previous_operation_batch") not in (None, "", previous):
			frappe.throw(_("The physical FG Batch has a different previous operation Batch. Review its traceability links."))
		if physical.get("custom_previous_operation_batch") != previous:
			physical.custom_previous_operation_batch = previous
			physical.save(ignore_permissions=True)
	for trace in frappe.get_all("Batch", filters={PURPOSE: TRACE,
		LOT_LINK: ["in", frappe.get_all("SmartEdge Production Lot", filters={"final_eb_bom_generator": job.final_eb_bom_generator}, pluck="name")]}, pluck="name"):
		doc = frappe.get_doc("Batch", trace)
		if not doc.get("custom_related_fg_batch"):
			doc.custom_related_fg_batch = names[0]
			doc.save(ignore_permissions=True)
	return names


def validate_batch(doc, method=None):
	if not schema_ready():
		return
	old = doc.get_doc_before_save()
	if old and old.get(PURPOSE) == TRACE and (doc.get(PURPOSE) != TRACE or doc.get(LOT_LINK) != old.get(LOT_LINK) or doc.item != old.item):
		frappe.throw(_("An operation traceability Batch cannot be converted or reassigned to physical stock."))
	if doc.get(PURPOSE) != TRACE:
		return
	if not doc.get(LOT_LINK):
		frappe.throw(_("Operation Traceability Batch requires its completed Production Lot."))
	lot = frappe.get_doc("SmartEdge Production Lot", doc.get(LOT_LINK))
	job, entry = _context(lot)
	from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
	if is_final_stock_posting_entry(entry):
		frappe.throw(_("The final operation must use its existing physical FG Batch."))
	if doc.item != lot.finished_item or doc.reference_doctype != "SmartEdge Production Job" or doc.reference_name != job.name:
		frappe.throw(_("Operation Batch Item and Production Job must match the completed operation lot."))
	if doc.name and (frappe.db.exists("Serial and Batch Entry", {"batch_no": doc.name})
		or frappe.db.exists("Stock Ledger Entry", {"batch_no": doc.name})):
		frappe.throw(_("Operation traceability batches cannot have stock transactions."))
	# Traceability Batches are active for reporting, but every stock-selection
	# path is guarded below. Their quantity remains zero, so they never provide
	# available inventory.
	doc.disabled = 0
	if flt(doc.batch_qty) or flt(doc.get("produced_qty")) or flt(doc.get("qty_to_produce")):
		frappe.throw(_("Operation Batch stock quantity must remain zero; record output in Operation Output Quantity."))
	doc.custom_operation_name = lot.operation
	doc.custom_operation_sequence = lot.operation_sequence
	doc.custom_operation_output_quantity = job.actual_qty_meter
	doc.custom_operation_completed_at = job.actual_end


def reject_operation_batches_in_stock(doc, method=None):
	if not schema_ready():
		return
	rows = [doc, *doc.get_all_children()]
	names = {row.get("batch_no") for row in rows if row.get("batch_no")}
	bundles = {row.get("serial_and_batch_bundle") for row in rows if row.get("serial_and_batch_bundle")}
	if bundles:
		names.update(frappe.get_all("Serial and Batch Entry", filters={"parent": ["in", list(bundles)]}, pluck="batch_no"))
	if not names:
		return
	invalid = frappe.get_all("Batch", filters={"name": ["in", list(names)], PURPOSE: TRACE}, pluck="name")
	if invalid:
		frappe.throw(_("Operation traceability Batch {0} cannot be used in stock allocation, receipt, or consumption.").format(", ".join(invalid)))


def _filter_traceability_selector(rows):
	"""Keep active trace Batches out of ERPNext stock selectors.

	They are active for reporting, but are never physical inventory. Validation
	guards remain in place for manually supplied values and non-UI callers.
	"""
	if not rows or not schema_ready():
		return rows
	names = [row[0] for row in rows if row and row[0]]
	trace = set(frappe.get_all("Batch", filters={"name": ["in", names], PURPOSE: TRACE}, pluck="name"))
	return [row for row in rows if not row or row[0] not in trace]


@frappe.whitelist()
def get_batch_no(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_batch_no as standard_get_batch_no

	return _filter_traceability_selector(standard_get_batch_no(doctype, txt, searchfield, start, page_len, filters))


@frappe.whitelist()
def get_batch_numbers(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_batch_numbers as standard_get_batch_numbers

	return _filter_traceability_selector(standard_get_batch_numbers(doctype, txt, searchfield, start, page_len, filters))


def backfill_operation_batches(production, dry_run=1):
	"""Preview by default; an explicitly approved manager action creates metadata only."""
	frappe.only_for(("System Manager", "Manufacturing Manager"))
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("write")
	if not schema_ready():
		frappe.throw(_("Install the operation Batch metadata before backfill."))
	results, records = [], []
	for lot in frappe.get_all("SmartEdge Production Lot", filters={"final_eb_bom_generator": production},
		fields=["name"], order_by="operation_sequence asc, actual_end asc"):
		record = frappe.get_doc("SmartEdge Production Lot", lot.name)
		job, entry = _context(record)
		from smart_edge_custom.manufacturing.planning import is_final_stock_posting_entry
		final = is_final_stock_posting_entry(entry)
		existing = _physical_batches(job) if final else ([batch_for_lot(record.name)] if batch_for_lot(record.name) else [])
		if not final and not frappe.db.get_value("Item", record.finished_item, "has_batch_no"):
			frappe.throw(_("Item {0} is not batch-controlled; its settings were not changed.").format(record.finished_item))
		row = {"production_job": job.name, "operation_lot": record.name, "operation": entry.operation,
			"item": record.finished_item, "actual_output": job.actual_qty_meter, "completed_at": job.actual_end,
			"batch": existing, "action": "Link existing physical FG Batch" if final else ("Reuse" if existing else "Create zero-stock operation Batch"),
			"proposed_batch_id": None if final or existing else _batch_id(record, job, entry)}
		results.append(row)
		records.append(record)
	if not cint(dry_run):
		# Validate the complete proposal before creating anything, and roll back a
		# partial application if a later operation or metadata link fails.
		savepoint = "operation_batches_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(savepoint)
		try:
			for row, record in zip(results, records, strict=True):
				row["batch"] = ensure_operation_batch(record, allow_create=True)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			raise
	return results
