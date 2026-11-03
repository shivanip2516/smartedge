"""Rollback-only final posting tests using isolated Items and real ERPNext stock posting."""
import json
import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.test_finished_operation_batches import TestFinishedOperationBatches
from smart_edge_custom.manufacturing.planning import complete_production_job, validate_batch_control


class TestFinalCompletionPosting(unittest.TestCase):
	make_bom = TestFinishedOperationBatches.make_bom
	make_finished_bom = TestFinishedOperationBatches.make_finished_bom
	source = TestFinishedOperationBatches.source
	generator = TestFinishedOperationBatches.generator
	complete = TestFinishedOperationBatches.complete

	def setUp(self):
		self.prepare_stock_fixture()
		self.complete(0, 10)
		self.complete(1, 10)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", self.entries[2])
		self.job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": entry.name,
			"final_eb_bom_generator": self.doc.name, "company": self.company,
			"operation": entry.operation, "workstation": self.workstation,
			"operator_employee": self.employee, "status": "In Progress",
			"actual_start": frappe.utils.now_datetime(), "planned_qty_meter": 500,
			"actual_qty_meter": 10, "good_qty": 10,
		}).insert()

	def prepare_stock_fixture(self):
		TestFinishedOperationBatches.setUp(self)
		self.stack.close()  # Use real completion, validation, posting and genealogy.
		item = frappe.get_doc("Item", self.doc.finished_item)
		item.has_batch_no = item.create_new_batch = 1
		item.batch_number_series = "TEST-FG-.#####"
		item.append("uoms", {"uom": "Meter", "conversion_factor": 1})
		item.save()
		self.input_batches = []
		for group, count in (("Compounds", 2), ("Base Colour", 3)):
			for _ in range(count):
				batch = frappe.get_doc({"doctype": "Batch", "item": self.items[group],
					"batch_id": "TEST-IN-" + frappe.generate_hash(length=12)}).insert()
				self.input_batches.append(batch.name)
				frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
					"purpose": "Material Receipt", "company": self.company,
					"items": [{"item_code": self.items[group], "qty": 3, "basic_rate": 2,
						"t_warehouse": self.warehouse, "batch_no": batch.name, "use_serial_batch_fields": 1}],
				}).insert().submit()

	def counts(self):
		return {dt: frappe.db.count(dt) for dt in ("Batch", "Stock Entry", "Stock Ledger Entry",
			"Serial and Batch Bundle", "SmartEdge Production Lot", "SmartEdge WIP Ledger Entry")}

	def test_disabled_batch_control_leaves_job_open_and_creates_nothing(self):
		item = frappe.get_doc("Item", self.doc.finished_item)
		item.has_batch_no = item.create_new_batch = 0
		item.save()
		before = self.counts()
		with self.assertRaisesRegex(frappe.ValidationError, "must be batch-controlled"):
			complete_production_job(self.entries[2], actual_qty_meter=10, good_qty=10)
		self.assertEqual(before, self.counts())
		self.job.reload()
		self.assertEqual(self.job.status, "In Progress")
		self.assertIsNone(self.job.actual_end)

	def test_real_final_posting_and_failed_posting_retry_are_idempotent(self):
		before = self.counts()
		plan_status = frappe.db.get_value("SmartEdge Production Plan Entry", self.entries[2], "status")
		# Fail after actual submit/link work; the whole completion must roll back.
		from smart_edge_custom.manufacturing.planning import link_final_manufacturing_stock_entry
		def fail_after_posting(*args, **kwargs):
			link_final_manufacturing_stock_entry(*args, **kwargs)
			raise frappe.ValidationError("Test posting failure")
		with patch("smart_edge_custom.manufacturing.planning.link_final_manufacturing_stock_entry", side_effect=fail_after_posting):
			with self.assertRaisesRegex(frappe.ValidationError, "Test posting failure"):
				self.job.complete_job()
		self.assertEqual(before, self.counts())
		self.assertEqual(self.job.status, "In Progress")
		self.assertEqual(frappe.db.get_value("SmartEdge Production Plan Entry", self.entries[2], "status"), plan_status)
		result = complete_production_job(self.entries[2], actual_qty_meter=10, good_qty=10)
		self.doc.reload(); self.job.reload()
		stock = frappe.get_doc("Stock Entry", self.doc.final_manufacturing_stock_entry)
		self.assertEqual(stock.docstatus, 1)
		self.assertEqual(stock.purpose, "Manufacture")
		self.assertEqual(self.job.status, "Completed")
		self.assertEqual(self.job.manufacturing_stock_entry, stock.name)
		self.assertEqual(frappe.db.get_value("SmartEdge Production Plan Entry", self.entries[2], "status"), "Completed")
		self.assertEqual(frappe.db.count("Batch", {"item": self.doc.finished_item, "custom_batch_purpose": "Physical Stock"}), 1)
		self.assertEqual(frappe.db.count("Batch", {"item": self.doc.finished_item, "custom_batch_purpose": "Operation Traceability"}), 2)
		output = next(r for r in stock.items if r.is_finished_item)
		self.assertEqual(output.item_code, self.doc.finished_item)
		self.assertEqual(output.qty, 10)
		self.assertTrue(output.serial_and_batch_bundle)
		movements = frappe.get_all("SmartEdge Batch Genealogy Movement", filters={"stock_entry": stock.name, "source_status": "Active"}, fields=["batch_no", "direction", "item_code", "quantity"])
		inputs = [r for r in movements if r.direction == "Input"]
		outputs = [r for r in movements if r.direction == "Output"]
		self.assertTrue(inputs)
		self.assertTrue(all(r.batch_no in self.input_batches for r in inputs))
		self.assertEqual({r.item_code for r in inputs}, {self.doc.compound_item, self.doc.base_colour_item})
		self.assertEqual(len(outputs), 1)
		from smart_edge_custom.manufacturing.production_lot import attach_lot_details
		rows = [frappe.get_doc("SmartEdge Production Plan Entry", self.entries[2]).as_dict()]
		attach_lot_details(rows)
		self.assertEqual(rows[0].related_finished_goods_batches, [outputs[0].batch_no])
		self.assertEqual(rows[0].production_lot, result["production_lot"])
		self.assertIn(self.job.name, rows[0].production_lot)
		for item_code, total in ((self.doc.compound_item, self.doc.compound_total_required),
			(self.doc.base_colour_item, self.doc.shade_total_required)):
			self.assertAlmostEqual(sum(r.quantity for r in inputs if r.item_code == item_code),
				total / self.doc.production_quantity * 10, places=5)
		from smart_edge_custom.manufacturing.batch_genealogy import get_genealogy
		self.assertTrue(get_genealogy(outputs[0].batch_no, "Backward")["production_lot_roots"])
		self.assertTrue(get_genealogy(inputs[0].batch_no, "Forward")["production_lot_roots"])
		after = self.counts()
		self.assertEqual(after["Batch"], before["Batch"] + 1)
		self.assertEqual(after["Stock Entry"], before["Stock Entry"] + 1)
		self.assertEqual(complete_production_job(self.entries[2])["production_lot"], result["production_lot"])
		self.assertEqual(after, self.counts())
		lots = frappe.get_all("SmartEdge Production Lot", filters={"final_eb_bom_generator": self.doc.name},
			fields=["name", "production_job", "operation", "operation_sequence", "finished_item_name"], order_by="operation_sequence")
		self.assertEqual(len(lots), 3)
		print("VERIFIED_PERSISTENT_OPERATION_LOTS", json.dumps(lots))
		print("VERIFIED_PHYSICAL_FG_BATCH", outputs[0].batch_no, "STOCK_ENTRY", stock.name)

	def test_standard_item_history_validation_is_not_bypassed(self):
		item = frappe.get_doc("Item", self.doc.compound_item)
		item.has_batch_no = 0
		with self.assertRaisesRegex(frappe.ValidationError, "existing submitted transactions"):
			item.save()

	def test_rounded_full_output_posts_exact_material_totals_without_false_shortage(self):
		self.doc.thickness_mm = 100
		self.doc.width_mm = 100
		self.doc.weight_factor = 1.33
		self.doc.save()
		self.doc.reload()
		actual = frappe.utils.flt(self.doc.production_quantity, frappe.get_precision("SmartEdge Production Job", "good_qty"))
		self.assertGreater(actual, self.doc.production_quantity)
		counts = {item: frappe.db.count("Batch", {"item": item}) for item in (self.doc.compound_item, self.doc.base_colour_item)}
		complete_production_job(self.entries[2], actual_qty_meter=actual, good_qty=actual)
		self.doc.reload()
		stock = frappe.get_doc("Stock Entry", self.doc.final_manufacturing_stock_entry)
		self.assertEqual(stock.docstatus, 1)
		for item, expected in ((self.doc.compound_item, self.doc.compound_total_required),
			(self.doc.base_colour_item, self.doc.shade_total_required)):
			self.assertEqual(next(row.qty for row in stock.items if row.item_code == item), expected)
		self.assertEqual(counts, {item: frappe.db.count("Batch", {"item": item}) for item in counts})

	def test_inward_auto_batch_permission_does_not_allow_outward_or_manual_batches(self):
		validate_batch_control([{"item_code": self.doc.finished_item, "is_finished_item": 1, "t_warehouse": self.warehouse}])
		with self.assertRaisesRegex(frappe.ValidationError, "explicit batch allocation"):
			validate_batch_control([{"item_code": self.doc.finished_item, "is_finished_item": 1, "s_warehouse": self.warehouse}])
		item = frappe.get_doc("Item", self.doc.finished_item)
		item.create_new_batch = 0
		item.save()
		with self.assertRaisesRegex(frappe.ValidationError, "explicit batch allocation"):
			validate_batch_control([{"item_code": item.name, "is_finished_item": 1, "t_warehouse": self.warehouse}])
