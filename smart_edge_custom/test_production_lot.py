import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from smart_edge_custom.manufacturing.production_lot import (
	calculate_lot_balances,
	get_operation_lot_mode,
	get_operation_lot_prefix,
	get_operation_lot_name,
	get_final_stock_lot_names,
	get_input_production_lot,
	is_packing_operation,
	get_unprocessed_qc_input_quantity,
	make_movement_key,
	record_release_movement,
)


class TestProductionLot(unittest.TestCase):
	def test_operation_prefixes(self):
		cases = (
			(frappe._dict(operation="Extrusion", planning_category="Extrusion"), "EXT"),
			(frappe._dict(operation="Integrated Online Printing", planning_category="Printing"), "OPR"),
			(frappe._dict(operation="Offline Printing", planning_category="Printing"), "OFP"),
			(frappe._dict(operation="Slitting", planning_category="Slitting"), "SLT"),
			(frappe._dict(operation="Packing", planning_category="Packing"), "PCK"),
			(frappe._dict(operation="QC After Extrusion", planning_category="Extrusion"), "QC"),
		)
		for entry, expected in cases:
			with self.subTest(operation=entry.operation):
				self.assertEqual(get_operation_lot_prefix(entry), expected)

	def test_fg_qc_has_its_own_operation_lot_without_a_bom_reference(self):
		entry = frappe._dict(
			plan_type="Final EB",
			operation="QC After Extrusion",
			planning_category="QC",
			operation_row=None,
		)
		self.assertEqual(get_operation_lot_mode(entry), "New Production Lot")

	def test_operation_id_uses_actual_job_and_sequence_and_handles_long_names(self):
		self.assertEqual(get_operation_lot_name("EXT", "SE-JOB-2026-00034", 1), "EXT-SE-JOB-2026-00034-01")
		self.assertNotEqual(get_operation_lot_name("EXT", "JOB-1", 1), get_operation_lot_name("EXT", "JOB-2", 1))
		self.assertNotEqual(get_operation_lot_name("EXT", "JOB-1", 1), get_operation_lot_name("EXT", "JOB-1", 2))
		long_job = "J" * 140
		first = get_operation_lot_name("EXT", long_job, 1)
		self.assertEqual(len(first), 140)
		self.assertEqual(first, get_operation_lot_name("EXT", long_job, 1))
		self.assertNotEqual(first, get_operation_lot_name("EXT", long_job + "2", 1))

	def test_required_material_keeps_stock_batch_mode(self):
		entry = frappe._dict(plan_type="Required Material", operation="MIXING")
		self.assertEqual(get_operation_lot_mode(entry), "Stock Batch")

	def test_packing_input_lot_resolves_from_released_upstream_job(self):
		entry = frappe._dict(released_from_production_job="QC-JOB")
		fake_db = MagicMock()
		fake_db.get_value.return_value = "QC-LOT"
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", SimpleNamespace(db=fake_db)):
			self.assertEqual(get_input_production_lot(entry), "QC-LOT")
		fake_db.get_value.assert_called_once_with("SmartEdge Production Job", "QC-JOB", "output_production_lot")

	def test_packing_input_lot_falls_back_to_completed_route_predecessor_when_qc_has_no_output(self):
		entry = frappe._dict(
			released_from_production_job="QC-JOB", final_eb_bom_generator="GEN",
			plan_type="Final EB", sequence=3,
		)
		fake_db = MagicMock()
		fake_db.get_value.side_effect = [None, None, "EXT-LOT"]
		fake_frappe = SimpleNamespace(
			db=fake_db,
			get_all=MagicMock(return_value=[frappe._dict(name="EXT-PLAN")]),
		)
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", fake_frappe):
			self.assertEqual(get_input_production_lot(entry), "EXT-LOT")

	def test_partial_packing_uses_execution_output_not_full_release_pool(self):
		self.assertTrue(is_packing_operation(frappe._dict(operation="Packing", planning_category="Packing")))
		self.assertFalse(is_packing_operation(frappe._dict(operation="Extrusion", planning_category="Extrusion")))

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

	def test_qc_release_appends_initial_accepted_quantity(self):
		job = frappe._dict(
			name="JOB-QC", operation="QC After Extrusion", released_to_plan_entry="PLAN-PACK",
			released_qty_meter=10000, released_qty_kg=50, final_eb_bom_generator="GEN",
		)
		fake_frappe = MagicMock()
		fake_frappe.db.get_value.return_value = frappe._dict(operation="Packing")
		fake_frappe.db.exists.return_value = False
		fake_frappe.get_all.return_value = []
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.production_lot.ensure_available_for_transfer") as ensure, \
			patch("smart_edge_custom.manufacturing.production_lot.record_movement", return_value="LEDGER-1") as record:
			result = record_release_movement(
				job, frappe._dict(name="PLAN-QC"), frappe._dict(finished_item="FG", stock_uom="Meter"), "LOT-1", qc_release=True
			)
		self.assertEqual(result, "LEDGER-1")
		self.assertEqual(record.call_args.args[0], "QC Release")
		self.assertEqual(record.call_args.kwargs["quantity"], 10000)
		self.assertEqual(record.call_args.kwargs["quantity_kg"], 50)
		ensure.assert_called_once_with("LOT-1", "QC After Extrusion", 10000)

	def test_qc_release_appends_only_new_reinspection_acceptance(self):
		job = frappe._dict(
			name="JOB-QC", operation="QC After Extrusion", released_to_plan_entry="PLAN-PACK",
			released_qty_meter=13000, released_qty_kg=65, final_eb_bom_generator="GEN",
		)
		fake_frappe = MagicMock()
		fake_frappe.db.get_value.return_value = frappe._dict(operation="Packing")
		fake_frappe.db.exists.return_value = False
		fake_frappe.get_all.return_value = [frappe._dict(name="LEDGER-1", quantity=10000, quantity_kg=50)]
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.production_lot.ensure_available_for_transfer") as ensure, \
			patch("smart_edge_custom.manufacturing.production_lot.record_movement", return_value="LEDGER-2") as record:
			record_release_movement(
				job, frappe._dict(name="PLAN-QC"), frappe._dict(finished_item="FG", stock_uom="Meter"), "LOT-1", qc_release=True
			)
		self.assertEqual(record.call_args.kwargs["quantity"], 3000)
		self.assertEqual(record.call_args.kwargs["quantity_kg"], 15)
		ensure.assert_called_once_with("LOT-1", "QC After Extrusion", 3000)

	def test_partial_qc_completion_uses_unreleased_input_only(self):
		job = frappe._dict(name="JOB-QC", released_to_plan_entry="PLAN-PACK")
		with patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all", return_value=[
			frappe._dict(quantity=10000), frappe._dict(quantity=407),
		]):
			remaining = get_unprocessed_qc_input_quantity(job, "LOT-1", 16407)
		self.assertEqual(remaining, 6000)

	def test_fully_reconciled_qc_input_is_a_zero_remainder(self):
		job = frappe._dict(name="JOB-QC", released_to_plan_entry="PLAN-PACK")
		with patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all", return_value=[
			frappe._dict(quantity=10000), frappe._dict(quantity=6407),
		]):
			remaining = get_unprocessed_qc_input_quantity(job, "LOT-1", 16407)
		self.assertEqual(remaining, 0)

	def test_negative_qc_input_quantity_is_rejected(self):
		job = frappe._dict(name="JOB-QC", released_to_plan_entry="PLAN-PACK")
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)
		with patch("smart_edge_custom.manufacturing.production_lot._", side_effect=lambda text: text), \
			patch("smart_edge_custom.manufacturing.production_lot.frappe.throw", side_effect=throw):
			with self.assertRaisesRegex(frappe.ValidationError, "QC input quantity cannot be negative"):
				get_unprocessed_qc_input_quantity(job, "LOT-1", -1)

	def test_qc_release_remainder_cannot_be_negative(self):
		job = frappe._dict(name="JOB-QC", released_to_plan_entry="PLAN-PACK")
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)
		with patch("smart_edge_custom.manufacturing.production_lot._", side_effect=lambda text: text), \
			patch("smart_edge_custom.manufacturing.production_lot.frappe.throw", side_effect=throw), \
			patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all", return_value=[
				frappe._dict(quantity=101),
			]):
			with self.assertRaisesRegex(frappe.ValidationError, "exceed the released input quantity"):
				get_unprocessed_qc_input_quantity(job, "LOT-1", 100)

	def test_qc_release_appends_only_partial_reinspection_acceptance(self):
		job = frappe._dict(
			name="JOB-QC", operation="QC After Extrusion", released_to_plan_entry="PLAN-PACK",
			released_qty_meter=12500, released_qty_kg=62.5, final_eb_bom_generator="GEN",
		)
		fake_frappe = MagicMock()
		fake_frappe.db.get_value.return_value = frappe._dict(operation="Packing")
		fake_frappe.db.exists.return_value = False
		fake_frappe.get_all.return_value = [frappe._dict(name="LEDGER-1", quantity=10000, quantity_kg=50)]
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.production_lot.ensure_available_for_transfer") as ensure, \
			patch("smart_edge_custom.manufacturing.production_lot.record_movement", return_value="LEDGER-2") as record:
			record_release_movement(
				job, frappe._dict(name="PLAN-QC"), frappe._dict(finished_item="FG", stock_uom="Meter"), "LOT-1", qc_release=True
			)
		self.assertEqual(record.call_args.kwargs["quantity"], 2500)
		self.assertEqual(record.call_args.kwargs["quantity_kg"], 12.5)
		ensure.assert_called_once_with("LOT-1", "QC After Extrusion", 2500)

	def test_repeated_qc_release_does_not_append_duplicate_movement(self):
		job = frappe._dict(
			name="JOB-QC", operation="QC After Extrusion", released_to_plan_entry="PLAN-PACK",
			released_qty_meter=13000, released_qty_kg=65, final_eb_bom_generator="GEN",
		)
		fake_frappe = MagicMock()
		fake_frappe.db.get_value.return_value = frappe._dict(operation="Packing")
		fake_frappe.get_all.return_value = [
			frappe._dict(name="LEDGER-1", quantity=10000, quantity_kg=50),
			frappe._dict(name="LEDGER-2", quantity=3000, quantity_kg=15),
		]
		with patch("smart_edge_custom.manufacturing.production_lot.frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.production_lot.record_movement") as record:
			result = record_release_movement(
				job, frappe._dict(name="PLAN-QC"), frappe._dict(finished_item="FG", stock_uom="Meter"), "LOT-1", qc_release=True
			)
		self.assertEqual(result, "LEDGER-2")
		record.assert_not_called()

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
