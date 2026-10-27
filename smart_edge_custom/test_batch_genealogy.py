import unittest

import frappe

from smart_edge_custom.manufacturing.batch_genealogy import (
	build_genealogy_tree,
	extract_batch_movements,
	make_source_key,
)


class TestBatchGenealogy(unittest.TestCase):
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

if __name__ == "__main__":
	unittest.main()
