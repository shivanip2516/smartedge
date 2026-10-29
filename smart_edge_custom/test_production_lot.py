import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing.production_lot import (
	calculate_lot_balances,
	get_operation_lot_mode,
	get_operation_lot_prefix,
	get_final_stock_lot_names,
	make_movement_key,
)


class TestProductionLot(unittest.TestCase):
	def test_operation_prefixes(self):
		cases = (
			(frappe._dict(operation="Extrusion", planning_category="Extrusion"), "EXT"),
			(frappe._dict(operation="Integrated Online Printing", planning_category="Printing"), "OPR"),
			(frappe._dict(operation="Offline Printing", planning_category="Printing"), "OFP"),
			(frappe._dict(operation="Slitting", planning_category="Slitting"), "SLT"),
			(frappe._dict(operation="Packing", planning_category="Packing"), "PCK"),
		)
		for entry, expected in cases:
			with self.subTest(operation=entry.operation):
				self.assertEqual(get_operation_lot_prefix(entry), expected)

	def test_qc_preserves_incoming_lot(self):
		entry = frappe._dict(
			plan_type="Final EB",
			operation="QC After Extrusion",
			planning_category="QC",
			operation_row=None,
		)
		self.assertEqual(get_operation_lot_mode(entry), "Preserve Incoming Lot")

	def test_required_material_keeps_stock_batch_mode(self):
		entry = frappe._dict(plan_type="Required Material", operation="MIXING")
		self.assertEqual(get_operation_lot_mode(entry), "Stock Batch")

	def test_stable_movement_key_is_idempotent(self):
		self.assertEqual(
			make_movement_key("release", "JOB-1", "PLAN-2", "EXT-2026-00001"),
			make_movement_key("release", "JOB-1", "PLAN-2", "EXT-2026-00001"),
		)
		self.assertNotEqual(make_movement_key("output", "JOB-1"), make_movement_key("output", "JOB-2"))

	def test_partial_transfer_and_dispositions_are_not_double_counted(self):
		rows = [
			self.movement("Production Output", destination="LOT-1", destination_operation="Extrusion", quantity=4524),
			self.movement("Rejection", source="LOT-1", source_operation="Extrusion", quantity=10),
			self.movement("Scrap", source="LOT-1", source_operation="Extrusion", quantity=4),
			self.movement("Rework", source="LOT-1", source_operation="Extrusion", quantity=10),
			self.movement(
				"Transfer to Next Operation",
				source="LOT-1",
				destination="LOT-1",
				source_operation="Extrusion",
				destination_operation="QC After Extrusion",
				quantity=4500,
			),
		]
		balance = calculate_lot_balances(["LOT-1"], rows)["LOT-1"]
		self.assertEqual(balance["produced"], 4524)
		self.assertEqual(balance["available"], 4500)
		self.assertEqual(balance["transferred"]["Extrusion"], 4500)
		self.assertEqual(balance["stages"]["Extrusion"], 0)
		self.assertEqual(balance["stages"]["QC After Extrusion"], 4500)
		self.assertEqual(balance["rejected"], 10)
		self.assertEqual(balance["scrapped"], 4)
		self.assertEqual(balance["rework"], 10)

	def test_qc_hold_is_released_without_changing_total_available(self):
		rows = [
			self.movement("Production Output", destination="LOT-1", destination_operation="Extrusion", quantity=100),
			self.movement(
				"Transfer to Next Operation", source="LOT-1", destination="LOT-1",
				source_operation="Extrusion", destination_operation="QC", quantity=80,
			),
			self.movement("QC Hold", source="LOT-1", destination="LOT-1", source_operation="QC", destination_operation="QC", quantity=80),
			self.movement("QC Release", source="LOT-1", destination="LOT-1", source_operation="QC", destination_operation="Packing", quantity=75),
		]
		balance = calculate_lot_balances(["LOT-1"], rows)["LOT-1"]
		self.assertEqual(balance["available"], 100)
		self.assertEqual(balance["held_for_qc"], 5)
		self.assertEqual(balance["stages"]["Packing"], 75)

	def test_append_only_reversal_restores_available_without_inventing_stage_stock(self):
		rows = [
			self.movement("Production Output", destination="EXT-1", destination_operation="Extrusion", quantity=100),
			self.movement("Transfer to Finished Goods", source="EXT-1", source_operation="Extrusion", quantity=90),
			self.movement("Reversal / Correction", destination="EXT-1", quantity=90),
		]
		balance = calculate_lot_balances(["EXT-1"], rows)["EXT-1"]
		self.assertEqual(balance["available"], 100)
		self.assertEqual(balance["stages"]["Extrusion"], 10)

	@patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all")
	def test_final_reconciliation_waits_until_exact_final_job_has_output_lot(self, get_all):
		get_all.return_value = [frappe._dict(name="JOB-PACK", output_production_lot=None)]
		self.assertEqual(get_final_stock_lot_names("FEBG-1", "STE-1"), (set(), False))

		get_all.return_value = [frappe._dict(name="JOB-PACK", output_production_lot="PCK-1")]
		self.assertEqual(get_final_stock_lot_names("FEBG-1", "STE-1"), ({"PCK-1"}, True))

	@staticmethod
	def movement(movement_type, source=None, destination=None, source_operation=None, destination_operation=None, quantity=0):
		return frappe._dict(
			movement_type=movement_type,
			source_production_lot=source,
			destination_production_lot=destination,
			source_operation=source_operation,
			destination_operation=destination_operation,
			quantity=quantity,
		)


if __name__ == "__main__":
	unittest.main()
