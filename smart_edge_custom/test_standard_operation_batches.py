"""Real Batch metadata and stock boundary checks; fixtures are rolled back."""
import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.test_finished_operation_batches import TestFinishedOperationBatches as Fixtures
from smart_edge_custom.manufacturing import operation_batch as batches


class TestStandardOperationBatches(unittest.TestCase):
	make_bom = Fixtures.make_bom
	make_finished_bom = Fixtures.make_finished_bom
	source = Fixtures.source
	generator = Fixtures.generator
	setUp = Fixtures.setUp
	complete = Fixtures.complete

	def test_operation_batch_is_immutable_zero_stock_and_rejected_by_stock_hooks(self):
		job, lot = self.complete(0, 490, good=480)
		batch = frappe.get_doc("Batch", batches.batch_for_lot(lot.name))
		self.assertEqual(batch.custom_operation_output_quantity, 490)
		self.assertEqual(batch.batch_qty, 0)
		self.assertEqual(batch.disabled, 0)
		batch.disabled = 0
		batch.save()
		self.assertEqual(batch.disabled, 0)
		batch.custom_batch_purpose = batches.PHYSICAL
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be converted"):
			batch.save()
		batch.reload()
		batch.batch_qty = 490
		with self.assertRaisesRegex(frappe.ValidationError, "stock quantity must remain zero"):
			batch.save()
		# Exercise actual framework hooks before any stock record is inserted.
		for purpose, warehouse_field in (("Material Receipt", "t_warehouse"), ("Material Issue", "s_warehouse")):
			stock = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": purpose,
				"company": self.company, "items": [{"item_code": self.doc.finished_item,
					"qty": 1, "batch_no": batch.name, warehouse_field: self.warehouse}]})
			with self.assertRaisesRegex(frappe.ValidationError, "cannot be used in stock"):
				stock.insert()
		bundle = frappe.get_doc({"doctype": "Serial and Batch Bundle", "item_code": self.doc.finished_item,
			"warehouse": self.warehouse, "type_of_transaction": "Inward",
			"entries": [{"batch_no": batch.name, "qty": 1}]})
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be used in stock"):
			bundle.run_method("before_validate")
		for doctype, event in (("Stock Ledger Entry", "before_insert"), ("Pick List", "validate"),
			("Stock Reservation Entry", "validate"), ("Delivery Note", "validate"),
			("Purchase Receipt", "validate"), ("Purchase Invoice", "validate"),
			("Sales Invoice", "validate"), ("Stock Reconciliation", "validate")):
			guard = "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"
			self.assertIn(guard, frappe.get_hooks("doc_events")[doctype][event])
			# Core mandatory-field checks are independent; exercise the configured guard directly.
			with self.assertRaisesRegex(frappe.ValidationError, "cannot be used in stock"):
				batches.reject_operation_batches_in_stock(frappe.get_doc({"doctype": doctype, "batch_no": batch.name}))
		physical = frappe.get_doc({"doctype": "Batch", "item": self.doc.finished_item,
			"batch_id": "TEST-PHYSICAL-" + frappe.generate_hash(length=12)}).insert()
		batches.reject_operation_batches_in_stock(frappe.get_doc({"doctype": "Stock Entry", "batch_no": physical.name}))
		self.assertEqual(frappe.db.count("Batch", {batches.LOT_LINK: lot.name}), 1)
		from smart_edge_custom.manufacturing.fifo_batch_selection import get_fifo_batch_availability
		self.assertEqual(get_fifo_batch_availability(self.doc.finished_item, self.warehouse, 1, "Finished Goods")["eligible_batches"], [])

	def test_active_trace_batch_is_excluded_from_standard_stock_selectors(self):
		_, lot = self.complete(0, 490, good=480)
		trace_name = batches.batch_for_lot(lot.name)
		with patch("erpnext.controllers.queries.get_batch_no", return_value=[(trace_name, 0), ("PHYSICAL", 3)]):
			self.assertEqual(batches.get_batch_no("Batch", "", "name", 0, 20, {"item_code": self.doc.finished_item}), [("PHYSICAL", 3)])
		with patch("erpnext.controllers.queries.get_batch_numbers", return_value=[(trace_name,), ("PHYSICAL",)]):
			self.assertEqual(batches.get_batch_numbers("Batch", "", "name", 0, 20, {"item": self.doc.finished_item}), [("PHYSICAL",)])

	def test_failed_creation_rolls_back_job_lot_and_standard_batch(self):
		before = {dt: frappe.db.count(dt) for dt in ("Batch", "SmartEdge Production Lot", "SmartEdge WIP Ledger Entry")}
		original = batches.ensure_operation_batch
		def fail_after_insert(*args, **kwargs):
			original(*args, **kwargs)
			raise frappe.ValidationError("Test after operation Batch insert")
		with patch.object(batches, "ensure_operation_batch", side_effect=fail_after_insert):
			with self.assertRaisesRegex(frappe.ValidationError, "Test after operation Batch insert"):
				self.complete(0, 10)
		self.assertEqual(before, {dt: frappe.db.count(dt) for dt in before})
		job = frappe.get_doc("SmartEdge Production Job", {"plan_entry": self.entries[0]})
		self.assertEqual(job.status, "In Progress")
		self.assertIsNone(job.actual_end)

	def test_historical_preview_and_retry_do_not_create_unapproved_batches(self):
		# Simulate a completed operation from before this feature was installed.
		with patch.object(batches, "ensure_operation_batch", return_value=[]):
			job, lot = self.complete(0, 10)
		before = {dt: frappe.db.count(dt) for dt in ("Batch", "Stock Entry", "Stock Ledger Entry", "Serial and Batch Bundle")}
		preview = batches.backfill_operation_batches(self.doc.name)
		self.assertEqual(preview[0]["action"], "Create zero-stock operation Batch")
		self.assertEqual(preview[0]["actual_output"], 10)
		self.assertEqual(preview[0]["completed_at"], job.actual_end)
		job.complete_job()
		self.assertEqual(before, {dt: frappe.db.count(dt) for dt in before})
		self.assertFalse(batches.batch_for_lot(lot.name))
		# Explicit application is exercised only on this rollback-only test fixture.
		applied = batches.backfill_operation_batches(self.doc.name, dry_run=0)
		self.assertEqual(applied[0]["batch"], [preview[0]["proposed_batch_id"]])
		self.assertEqual(frappe.db.get_value("Batch", preview[0]["proposed_batch_id"], "disabled"), 0)
		self.assertEqual({dt: count + (1 if dt == "Batch" else 0) for dt, count in before.items()},
			{dt: frappe.db.count(dt) for dt in before})
		batches.backfill_operation_batches(self.doc.name, dry_run=0)
		self.assertEqual(frappe.db.count("Batch"), before["Batch"] + 1)

	def test_name_collision_does_not_reuse_or_modify_physical_batch(self):
		with patch.object(batches, "ensure_operation_batch", return_value=[]):
			job, lot = self.complete(0, 10)
		physical = frappe.get_doc({"doctype": "Batch", "item": self.doc.finished_item, "batch_id": lot.name}).insert()
		before = frappe.get_doc("Batch", physical.name).as_dict()
		created = batches.ensure_operation_batch(lot, allow_create=True)
		self.assertNotEqual(created[0], physical.name)
		self.assertEqual(before, frappe.get_doc("Batch", physical.name).as_dict())
		self.assertEqual(batches.ensure_operation_batch(lot, allow_create=True), created)
