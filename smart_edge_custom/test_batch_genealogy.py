import unittest

import frappe

from smart_edge_custom.manufacturing.batch_genealogy import (
	build_operation_context,
	build_genealogy_tree,
	extract_batch_movements,
	make_source_key,
)


class TestBatchGenealogy(unittest.TestCase):
	def test_final_event_attaches_inputs_to_extrusion_and_output_to_final_operation(self):
		jobs = [
			self.make_job("JOB-EXT", "Extrusion", "Extrusion", 1),
			self.make_job("JOB-PRINT", "Offline Printing", "Printing", 2),
			self.make_job("JOB-PACK", "Packing", "Packing", 3),
		]
		movements = [
			{
				"direction": "Input",
				"batch_no": "CMP-001",
				"item_code": "COMPOUND",
				"quantity": 65,
				"uom": "Kg",
				"warehouse": "Stores - SE",
			},
			{
				"direction": "Input",
				"batch_no": "SHD-001",
				"item_code": "SHADE",
				"quantity": 300,
				"uom": "Gram",
				"warehouse": "Stores - SE",
			},
			{
				"direction": "Output",
				"batch_no": "FG-001",
				"item_code": "FINISHED-EB",
				"quantity": 1000,
				"uom": "Meter",
				"warehouse": "Finished Goods - SE",
			},
		]
		stock_entry = frappe._dict({"name": "STE-FINAL", "work_order": None})

		context = build_operation_context(jobs, stock_entry, movements, [jobs[-1]])

		self.assertEqual(
			[row["batch_no"] for row in context[0]["input_batches"]],
			["CMP-001", "SHD-001"],
		)
		self.assertEqual(context[0]["tracking_mode"], "Posted Input Consumption")
		self.assertEqual(context[1]["tracking_mode"], "Operation History — Same Physical Identity")
		self.assertEqual(context[1]["input_batches"], [])
		self.assertEqual(context[2]["output_batches"][0]["batch_no"], "FG-001")
		self.assertEqual(context[2]["tracking_mode"], "Posted Output Receipt")
		self.assertFalse(any(row["physical_batch_transition"] for row in context))

	def test_operation_stock_entry_records_a_real_wip_batch_transition(self):
		job = self.make_job("JOB-EXT", "Extrusion", "Extrusion", 1)
		movements = [
			{"direction": "Input", "batch_no": "CMP-001", "item_code": "COMPOUND", "quantity": 65, "uom": "Kg"},
			{"direction": "Output", "batch_no": "EXT-001", "item_code": "WIP", "quantity": 500, "uom": "Meter"},
		]
		stock_entry = frappe._dict({"name": "STE-EXT", "work_order": "WO-EXT"})

		context = build_operation_context([job], stock_entry, movements, [job])

		self.assertEqual(context[0]["tracking_mode"], "Physical Batch Movement")
		self.assertEqual(context[0]["physical_batch_transition"], 1)
		self.assertEqual(context[0]["output_batches"][0]["batch_no"], "EXT-001")

	def test_submitted_operation_preserves_same_physical_batch_identity(self):
		job = self.make_job("JOB-PRINT", "Offline Printing", "Printing", 1)
		movements = [
			{"direction": "Input", "batch_no": "WIP-001", "item_code": "WIP", "quantity": 500, "uom": "Meter"},
			{"direction": "Output", "batch_no": "WIP-001", "item_code": "WIP", "quantity": 490, "uom": "Meter"},
		]

		context = build_operation_context(
			[job], frappe._dict({"name": "STE-PRINT", "work_order": "WO-PRINT"}), movements, [job]
		)

		self.assertEqual(
			context[0]["tracking_mode"],
			"Submitted Operation Movement — Same Physical Batch",
		)
		self.assertEqual(context[0]["physical_batch_transition"], 0)

	def test_extracts_multiple_actual_inputs_and_outputs_without_allocating_them(self):
		stock_entry = frappe._dict(
			{
				"name": "STE-0001",
				"items": [
					frappe._dict(
						{
							"name": "ROW-IN-1",
							"item_code": "COMPOUND",
							"stock_uom": "Kg",
							"s_warehouse": "Stores - SE",
							"serial_and_batch_bundle": "BUNDLE-IN-1",
						}
					),
					frappe._dict(
						{
							"name": "ROW-IN-2",
							"item_code": "BASE-COLOUR",
							"stock_uom": "Gram",
							"s_warehouse": "Stores - SE",
							"serial_and_batch_bundle": "BUNDLE-IN-2",
						}
					),
					frappe._dict(
						{
							"name": "ROW-OUT",
							"item_code": "WIP",
							"stock_uom": "Meter",
							"t_warehouse": "WIP - SE",
							"is_finished_item": 1,
							"serial_and_batch_bundle": "BUNDLE-OUT",
						}
					),
					frappe._dict(
						{
							"name": "ROW-SCRAP",
							"item_code": "RECOVERED-WIP",
							"stock_uom": "Kg",
							"t_warehouse": "Scrap - SE",
							"is_legacy_scrap_item": 1,
							"batch_no": "REC-001",
							"transfer_qty": 2,
						}
					),
				],
			}
		)
		bundles = {
			"BUNDLE-IN-1": [
				frappe._dict({"batch_no": "CMP-001", "qty": -40}),
				frappe._dict({"batch_no": "CMP-002", "qty": -25}),
			],
			"BUNDLE-IN-2": [frappe._dict({"batch_no": "BC-001", "qty": -3000})],
			"BUNDLE-OUT": [
				frappe._dict({"batch_no": "WIP-001", "qty": 500}),
				frappe._dict({"batch_no": "WIP-002", "qty": 300}),
			],
		}

		rows = extract_batch_movements(stock_entry, bundle_loader=lambda name: bundles[name])

		self.assertEqual(len(rows), 6)
		self.assertEqual(
			[(row["batch_no"], row["quantity"], row["uom"], row["direction"]) for row in rows],
			[
				("CMP-001", 40, "Kg", "Input"),
				("CMP-002", 25, "Kg", "Input"),
				("BC-001", 3000, "Gram", "Input"),
				("WIP-001", 500, "Meter", "Output"),
				("WIP-002", 300, "Meter", "Output"),
				("REC-001", 2, "Kg", "Scrap / Recovery"),
			],
		)

	def test_source_key_is_stable_and_distinguishes_direction(self):
		first = make_source_key("STE-1", "ROW-1", "BATCH-1", "Input")
		self.assertEqual(first, make_source_key("STE-1", "ROW-1", "BATCH-1", "Input"))
		self.assertNotEqual(first, make_source_key("STE-1", "ROW-1", "BATCH-1", "Output"))

	def test_tree_traversal_protects_cycles(self):
		left = ("BATCH-A", "ITEM-A")
		right = ("BATCH-B", "ITEM-B")
		base_event = {"stock_entry": "STE-1", "target_keys": [right]}
		return_event = {"stock_entry": "STE-2", "target_keys": [left]}
		graph = {left: [base_event], right: [return_event]}

		tree = build_genealogy_tree(left, graph, {}, set(), [0])

		cycle = tree["events"][0]["children"][0]["events"][0]["children"][0]
		self.assertTrue(cycle["cycle"])

	def make_job(self, name, operation, category, sequence):
		return frappe._dict(
			{
				"name": name,
				"plan_entry": f"PLAN-{name}",
				"operation": operation,
				"planning_category": category,
				"sequence": sequence,
				"workstation": f"WS-{name}",
				"operator_employee": "EMP-001",
				"actual_start": "2026-10-28 09:00:00",
				"actual_end": "2026-10-28 10:00:00",
				"actual_qty_meter": 500,
				"actual_qty_kg": 65,
				"good_qty": 500,
				"rejected_qty": 0,
				"rework_qty": 0,
				"usable_scrap": 0,
				"unusable_scrap": 0,
			}
		)

if __name__ == "__main__":
	unittest.main()
