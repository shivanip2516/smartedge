"""Batch projection must use actual handoff and stock evidence, without writes."""
import copy
import unittest

import frappe

from smart_edge_custom import test_final_completion_posting as posting_tests
from smart_edge_custom.manufacturing.batch_genealogy import get_genealogy, project_operation_batches
from smart_edge_custom.manufacturing.production_lot import get_production_lot_genealogy


class TestOperationBatchGenealogy(unittest.TestCase):
	make_bom = posting_tests.TestFinalCompletionPosting.make_bom
	make_finished_bom = posting_tests.TestFinalCompletionPosting.make_finished_bom
	source = posting_tests.TestFinalCompletionPosting.source
	generator = posting_tests.TestFinalCompletionPosting.generator
	complete = posting_tests.TestFinalCompletionPosting.complete
	setUp = posting_tests.TestFinalCompletionPosting.prepare_stock_fixture

	def test_actual_materials_operation_chain_and_final_output_are_read_only(self):
		actual = frappe.utils.flt(self.doc.production_quantity * 0.8, 6)
		first_job, first = self.complete(0, actual)
		second_job, second = self.complete(1, actual)
		# Planned materials are not proof of consumption before final stock posting.
		response = get_genealogy(first.name, "Forward")
		self.assertEqual(response["root"]["batch_status"], "Completed — Traceability")
		preview = response["operation_batch_trace"]
		self.assertTrue(all(row["consumption_status"] == "Not yet consumed" for row in preview["root"].get("input_materials", [])))
		self.assertFalse(any(e["basis"] == "Submitted manufacturing input" for e in preview["edges"]))
		final_job, final = self.complete(2, actual)
		self.doc.reload()
		stock = self.doc.final_manufacturing_stock_entry
		outputs = frappe.get_all("SmartEdge Batch Genealogy Movement", filters={"stock_entry": stock, "direction": "Output", "source_status": "Active"}, pluck="batch_no")
		inputs = frappe.get_all("SmartEdge Batch Genealogy Movement", filters={"stock_entry": stock, "direction": "Input", "source_status": "Active"}, fields=["batch_no", "quantity", "uom"])
		self.assertEqual(len(inputs), 5)
		self.assertTrue(any(row.quantity < 3 for row in inputs))
		before = {dt: frappe.get_all(dt, fields=["name", "modified"], order_by="name") for dt in ("Batch", "Stock Entry", "Stock Ledger Entry", "Serial and Batch Bundle", "SmartEdge WIP Ledger Entry", "SmartEdge Batch Genealogy Movement")}
		backward = get_genealogy(outputs[0], "Backward")["operation_batch_trace"]
		root = backward["root"]
		self.assertEqual(root["batch_no"], outputs[0])
		self.assertEqual(root["children"][0]["batch_no"], second.name)
		self.assertEqual(root["children"][0]["children"][0]["batch_no"], first.name)
		self.assertNotEqual(first_job.name, second_job.name)
		self.assertEqual(set(backward["parent_batches"]), {second.name})
		for row in inputs:
			forward = get_genealogy(row.batch_no, "Forward")["operation_batch_trace"]
			self.assertEqual(forward["child_batches"], [first.name])
			self.assertIn(outputs[0], forward["final_batches"])
			edge = next(e for e in forward["edges"] if e["source"] == row.batch_no)
			self.assertEqual((edge["input_quantity"], edge["input_uom"]), (row.quantity, row.uom))
			self.assertEqual((edge["quantity_type"], edge["source_document_type"], edge["source_document_id"]), ("Consumed", "Stock Entry", stock))
			self.assertTrue(edge["source_row_reference"])
			self.assertEqual(edge["stock_entry"], stock)
			self.assertEqual(len(forward["edges"]), len({(e["evidence"], e["source"], e["target"]) for e in forward["edges"]}))
		self.assertEqual(backward, get_genealogy(outputs[0], "Backward")["operation_batch_trace"])
		self.assertEqual(before, {dt: frappe.get_all(dt, fields=["name", "modified"], order_by="name") for dt in before})

	def test_cross_production_handoff_is_not_projected_from_names_or_metadata(self):
		_, first = self.complete(0, 10)
		_, second = self.complete(1, 10)
		original_doc, original_entries = self.doc, self.entries
		self.doc = self.generator()
		from smart_edge_custom.manufacturing.final_bom_generator import create_planning_entries
		self.entries = create_planning_entries(self.doc)
		other_job, other = self.complete(0, 10)
		self.doc, self.entries = original_doc, original_entries
		roots = copy.deepcopy(get_production_lot_genealogy(second.name, "Backward")["roots"])
		roots[0]["movements"].append({"name": "INVALID-CROSS-PRODUCTION", "source_production_lot": other.name,
			"destination_production_lot": second.name, "production_job": second.production_job,
			"quantity": 10, "uom": "Meter", "reference_doctype": "SmartEdge Production Job", "reference_name": second.production_job})
		projected = project_operation_batches(second.name, roots, "Backward")
		self.assertFalse(any(e["source"] == other.name for e in projected["edges"]))
		self.assertNotIn(other.name, {node["batch_no"] for node in projected["nodes"]})
		self.assertEqual(projected["parent_batches"], [first.name])
