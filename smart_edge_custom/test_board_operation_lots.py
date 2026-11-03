"""Exercise all three board completion calls and reload their saved lot references."""
import json
import unittest
from datetime import timedelta

import frappe

from smart_edge_custom import test_final_completion_posting as posting_tests
from smart_edge_custom.manufacturing.planning import (
	complete_production_job, get_plan_entries, get_week_start, make_operation_card,
)
from smart_edge_custom.manufacturing.production_lot import get_production_lot_genealogy


class TestBoardOperationLots(unittest.TestCase):
	make_bom = posting_tests.TestFinalCompletionPosting.make_bom
	make_finished_bom = posting_tests.TestFinalCompletionPosting.make_finished_bom
	source = posting_tests.TestFinalCompletionPosting.source
	generator = posting_tests.TestFinalCompletionPosting.generator
	setUp = posting_tests.TestFinalCompletionPosting.prepare_stock_fixture

	def test_board_completion_persists_extrusion_printing_and_packing_after_refresh(self):
		from smart_edge_custom.manufacturing.operation_batch import batch_for_lot
		item_count = frappe.db.count("Item")
		stock_count = frappe.db.count("Stock Entry")
		ledger_counts = {dt: frappe.db.count(dt) for dt in ("Stock Ledger Entry", "Serial and Batch Bundle")}
		material_batch_counts = {item: frappe.db.count("Batch", {"item": item})
			for item in (self.doc.compound_item, self.doc.base_colour_item)}
		today = frappe.utils.nowdate()
		for name in self.entries:
			entry = frappe.get_doc("SmartEdge Production Plan Entry", name)
			entry.planned_date = today
			entry.save()
		lots, stages = [], []
		for index, name in enumerate(self.entries):
			entry = frappe.get_doc("SmartEdge Production Plan Entry", name)
			job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": name,
				"final_eb_bom_generator": self.doc.name, "company": self.company,
				"operation": entry.operation, "workstation": self.workstation,
				"operator_employee": self.employee, "status": "In Progress",
				"actual_start": frappe.utils.now_datetime(), "planned_qty_meter": entry.production_quantity,
			}).insert()
			self.assertFalse(frappe.db.exists("SmartEdge Production Lot", {"production_job": job.name}))
			result = complete_production_job(name, actual_qty_meter=10, actual_qty_kg=10, good_qty=10)
			lot = frappe.get_doc("SmartEdge Production Lot", result["production_lot"])
			lots.append(lot.name)
			self.assertEqual(lot.production_job, job.name)
			self.assertEqual(lot.operation, entry.operation)
			self.assertEqual(lot.finished_item, self.doc.finished_item)
			self.assertEqual(lot.finished_item_name, frappe.db.get_value("Item", self.doc.finished_item, "item_name"))
			self.assertEqual(lot.operation_sequence, entry.sequence)
			self.assertEqual(lot.planned_quantity, entry.production_quantity)
			self.assertEqual(lot.actual_output_quantity, 10)
			self.assertEqual(lot.input_production_lot, lots[index - 1] if index else None)
			self.assertEqual(frappe.db.get_value("SmartEdge Production Job", job.name, "status"), "Completed")
			# A new provider read models page refresh; no completion response is reused.
			start = get_week_start(today)
			cards = [make_operation_card(row) for row in get_plan_entries(start, start + timedelta(days=6),
				include_related_operations=True) if row.final_eb_bom_generator == self.doc.name]
			self.assertEqual(len(cards), 3)
			self.assertEqual({card["production_lot"] for card in cards if card["production_lot"]}, set(lots))
			stages.append(cards)
			before = {dt: frappe.db.count(dt) for dt in ("SmartEdge Production Lot", "Batch", "Stock Entry")}
			self.assertEqual(complete_production_job(name)["production_lot"], lot.name)
			self.assertEqual(before, {dt: frappe.db.count(dt) for dt in before})
			if index < 2:
				self.assertEqual(frappe.db.count("Stock Entry"), stock_count)
				self.assertEqual(ledger_counts, {dt: frappe.db.count(dt) for dt in ledger_counts})
				batch = frappe.get_doc("Batch", batch_for_lot(lot.name))
				self.assertEqual((batch.item, batch.item_name), (self.doc.finished_item, lot.finished_item_name))
				self.assertEqual((batch.custom_operation_name, batch.custom_operation_sequence), (entry.operation, entry.sequence))
				self.assertEqual((batch.reference_doctype, batch.reference_name), ("SmartEdge Production Job", job.name))
				self.assertEqual(batch.custom_operation_completed_at, lot.actual_end)
				self.assertEqual(batch.custom_operation_output_quantity, 10)
				self.assertEqual(batch.batch_qty, 0)
				self.assertEqual(batch.disabled, 0)
				self.assertEqual(batch.custom_previous_operation_batch, batch_for_lot(lots[index - 1]) if index else None)
		self.doc.reload()
		stock = frappe.get_doc("Stock Entry", self.doc.final_manufacturing_stock_entry)
		self.assertEqual(stock.docstatus, 1)
		self.assertEqual(frappe.db.count("Stock Entry"), stock_count + 1)
		self.assertEqual(frappe.db.count("Batch", {"item": self.doc.finished_item, "custom_batch_purpose": "Physical Stock"}), 1)
		self.assertEqual(frappe.db.count("Batch", {"item": self.doc.finished_item, "custom_batch_purpose": "Operation Traceability"}), 2)
		self.assertEqual(frappe.db.count("Item"), item_count)
		self.assertEqual(material_batch_counts, {item: frappe.db.count("Batch", {"item": item}) for item in material_batch_counts})
		# The standard Batch List uses get_list; active trace metadata remains visible.
		listed = frappe.get_list("Batch", filters={"item": self.doc.finished_item},
			fields=["name", "item_name", "custom_batch_purpose", "custom_operation_name", "batch_qty"])
		self.assertEqual(len(listed), 3)
		trace_names = [batch_for_lot(name) for name in lots[:2]]
		physical = next(row for row in listed if row.custom_batch_purpose == "Physical Stock")
		self.assertEqual(frappe.db.get_value("Batch", physical.name, "custom_previous_operation_batch"), trace_names[1])
		for name in trace_names:
			self.assertEqual(frappe.db.get_value("Batch", name, "custom_related_fg_batch"), physical.name)
			self.assertFalse(frappe.db.exists("Serial and Batch Entry", {"batch_no": name}))
			self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"batch_no": name}))
		from smart_edge_custom.manufacturing.batch_genealogy import get_genealogy
		for direction, name in (("Forward", trace_names[0]), ("Backward", trace_names[1])):
			self.assertTrue(get_genealogy(name, direction)["production_lot_roots"])
		print("STANDARD_OPERATION_BATCH_EVIDENCE", json.dumps(listed))
		self.assertTrue(next(card for card in stages[-1] if card["id"] == self.entries[-1])["related_finished_goods_batches"])
		root = get_production_lot_genealogy(lots[-1], "Backward")["roots"][0]
		self.assertEqual([root["production_lot"], root["children"][0]["production_lot"],
			root["children"][0]["children"][0]["production_lot"]], list(reversed(lots)))
		print("BOARD_OPERATION_LOT_EVIDENCE", json.dumps(stages))
