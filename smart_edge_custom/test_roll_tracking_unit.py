import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from smart_edge_custom.manufacturing import roll_tracking


class FakeRoll:
	def __init__(self, name="", qty=0, status="In Rework", qc="Rework Required"):
		self.name = name
		self.current_meter_quantity = qty
		self.original_meter_quantity = qty
		self.process_status = status
		self.qc_status = qc
		self.packing_status = "Not Available"
		self.operation_history = []
		self.source_operation = "Extrusion"
		self.source_production_job = "SE-JOB-1"
		self.source_plan_entry = "SE-PLAN-1"

	def append(self, fieldname, value):
		getattr(self, fieldname).append(value)

	def save(self, **kwargs):
		return self

	def insert(self, **kwargs):
		if not self.name:
			self.name = f"SE-ROLL-{self.roll_sequence:05d}"
		return self


class TestRollTrackingUnit(unittest.TestCase):
	def test_extrusion_creates_three_rolls(self):
		created = []
		entry = SimpleNamespace(name="SE-PLAN-EX", operation="Extrusion", planning_category="Extrusion", final_eb_bom_generator="GEN-1", work_order="WO-1", get=lambda key: {"planning_category": "Extrusion", "work_order": "WO-1"}.get(key))
		job = SimpleNamespace(name="SE-JOB-EX", output_production_lot="LOT-1", get=lambda key: "LOT-1" if key == "output_production_lot" else None)
		def new_doc(_doctype):
			roll = FakeRoll()
			roll.roll_sequence = len(created) + 1
			created.append(roll)
			return roll
		fake_db = SimpleNamespace(get_value=lambda *args, **kwargs: "ITEM-1")
		with patch.object(roll_tracking.frappe, "get_all", return_value=[]), patch.object(roll_tracking.frappe, "new_doc", side_effect=new_doc), patch.object(roll_tracking.frappe, "db", fake_db), patch.object(roll_tracking, "_source_batch", return_value=None), patch.object(roll_tracking, "now_datetime", return_value="2026-11-17 00:00:00"):
			result = roll_tracking.create_rolls_for_extrusion(job, entry, [{"roll_no": 1, "meter_quantity": 4000}, {"roll_no": 2, "meter_quantity": 3000}, {"roll_no": 3, "meter_quantity": 3000}])
		self.assertEqual(len(result), 3)
		self.assertEqual([roll.current_meter_quantity for roll in created], [4000, 3000, 3000])
		self.assertEqual([len(roll.operation_history) for roll in created], [1, 1, 1])

	def test_rework_preserves_id_and_original_quantity(self):
		roll = FakeRoll("SE-ROLL-1", 3000)
		job = SimpleNamespace(name="SE-JOB-REWORK", status="Completed", plan_entry="SE-PLAN-REWORK", check_permission=lambda action: None)
		with patch.object(roll_tracking.frappe, "get_doc", side_effect=[roll, job]), patch.object(roll_tracking, "_save_roll"), patch.object(roll_tracking, "now_datetime", return_value="2026-11-17 00:00:00"):
			result = roll_tracking.record_rework_completion("SE-ROLL-1", "SE-JOB-REWORK", "2,950")
		self.assertEqual(result["roll"], "SE-ROLL-1")
		self.assertEqual(result["original_meter_quantity"], 3000)
		self.assertEqual(result["current_meter_quantity"], 2950)
		self.assertEqual(result["meter_loss"], 50)
		self.assertEqual(roll.process_status, "Awaiting Re-QC")

	def test_rework_cannot_increase_quantity(self):
		roll = FakeRoll("SE-ROLL-1", 3000)
		job = SimpleNamespace(name="SE-JOB-REWORK", status="Completed", check_permission=lambda action: None)
		with patch.object(roll_tracking.frappe, "get_doc", side_effect=[roll, job]), patch.object(roll_tracking.frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				roll_tracking.record_rework_completion("SE-ROLL-1", "SE-JOB-REWORK", 3001)

	def test_roll_qc_pass_releases_whole_roll_for_packing(self):
		roll = FakeRoll("SE-ROLL-1", 4000, status="Available for QC", qc="Pending")
		with patch.object(roll_tracking.frappe, "get_doc", return_value=roll), patch.object(roll_tracking, "_save_roll"), patch.object(roll_tracking, "now_datetime", return_value="2026-11-17 00:00:00"):
			roll_tracking.set_roll_qc_decision("SE-ROLL-1", "Passed", "QI-1")
		self.assertEqual(roll.qc_status, "Passed")
		self.assertEqual(roll.process_status, "Available for Packing")
		self.assertEqual(roll.packing_status, "Available")
		self.assertEqual(len(roll.operation_history), 1)

	def test_quantity_reinspection_reduces_same_roll_and_releases_accepted_portion(self):
		roll = FakeRoll("SE-ROLL-1", 6407)
		with patch.object(roll_tracking, "get_rolls_for_qc_source", return_value=[roll]), patch.object(roll_tracking.frappe, "get_doc", return_value=roll), patch.object(roll_tracking, "_save_roll"), patch.object(roll_tracking, "now_datetime", return_value="2026-11-18 18:46:48"):
			result = roll_tracking.reconcile_quantity_qc_decision(
				"MAT-QA-1", "SE-JOB-1", "LOT-1", 6407, 6000, 407, 0,
				reason="Reinspection rejection", remarks="Partial loss",
			)
		self.assertEqual(result, "SE-ROLL-1")
		self.assertEqual(roll.original_meter_quantity, 6407)
		self.assertEqual(roll.current_meter_quantity, 6000)
		self.assertEqual(roll.qc_status, "Passed")
		self.assertEqual(roll.process_status, "Available for Packing")
		self.assertEqual(roll.packing_status, "Available")
		self.assertEqual(roll.operation_history[-1]["meter_loss"], 407)

	def test_quantity_reinspection_is_idempotent(self):
		roll = FakeRoll("SE-ROLL-1", 6000, status="Available for Packing", qc="Passed")
		roll.operation_history.append({"related_quality_inspection": "MAT-QA-1"})
		with patch.object(roll_tracking, "get_rolls_for_qc_source", return_value=[roll]), patch.object(roll_tracking.frappe, "get_doc", return_value=roll):
			result = roll_tracking.reconcile_quantity_qc_decision("MAT-QA-1", "SE-JOB-1", "LOT-1", 6407, 6000, 407, 0)
		self.assertEqual(result, "SE-ROLL-1")
		self.assertEqual(len(roll.operation_history), 1)

	def test_quantity_reinspection_with_unresolved_rework_does_not_release_roll(self):
		roll = FakeRoll("SE-ROLL-1", 6407)
		with patch.object(roll_tracking, "get_rolls_for_qc_source", return_value=[roll]):
			result = roll_tracking.reconcile_quantity_qc_decision("MAT-QA-1", "SE-JOB-1", "LOT-1", 6407, 6000, 0, 407)
		self.assertIsNone(result)
		self.assertEqual(roll.process_status, "In Rework")

	def test_quantity_reinspection_rejects_unbalanced_values(self):
		roll = FakeRoll("SE-ROLL-1", 6407)
		with patch.object(roll_tracking, "get_rolls_for_qc_source", return_value=[roll]):
			result = roll_tracking.reconcile_quantity_qc_decision("MAT-QA-1", "SE-JOB-1", "LOT-1", 6407, 6000, 406, 0)
		self.assertIsNone(result)
		self.assertEqual(roll.process_status, "In Rework")

	def test_final_fg_batch_is_linked_only_from_submitted_receipt(self):
		set_value = MagicMock()
		fake_db = SimpleNamespace(
			exists=lambda doctype, name: True,
			get_value=lambda doctype, name, fields, **kwargs: (
				1 if doctype == "Stock Entry" and fields == "docstatus"
				else "FG-ITEM" if doctype == "Final EB BOM Generator"
				else frappe._dict(item="FG-ITEM", reference_name="STE-1")
			),
			set_value=set_value,
		)
		meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "final_fg_batch")
		job = frappe._dict(name="PACK-JOB", operation="Packing", final_eb_bom_generator="GEN")
		roll = frappe._dict(
			name="SE-ROLL-1", finished_item="FG-ITEM", final_fg_batch=None,
			qc_status="Passed", process_status="Packed", packing_status="Packed",
		)
		# Include a dict-like row without operation; it must be ignored rather
		# than raising AttributeError.
		with patch.object(roll_tracking.frappe, "db", fake_db), \
			patch.object(roll_tracking.frappe, "get_meta", return_value=meta), \
			patch.object(roll_tracking.frappe, "get_doc", return_value=frappe._dict(name="STE-1", items=[])), \
			patch.object(roll_tracking.frappe, "get_all", side_effect=[[job, {"name": "OTHER-JOB"}], [roll]]), \
			patch("smart_edge_custom.manufacturing.batch_genealogy.extract_batch_movements", return_value=[
				{"direction": "Output", "batch_no": "FG-BATCH-1", "item_code": "FG-ITEM"}
			]):
			result = roll_tracking.link_packed_rolls_to_finished_batch("STE-1", "GEN")
		self.assertEqual(result["batch"], "FG-BATCH-1")
		self.assertEqual(result["linked"], 1)
		set_value.assert_called_once_with("SmartEdge Roll", "SE-ROLL-1", "final_fg_batch", "FG-BATCH-1", update_modified=False)

	def test_stock_movement_history_is_idempotent_and_keeps_transaction_links(self):
		roll = FakeRoll("SE-ROLL-1", 800, status="Available for Packing", qc="Passed")
		with patch.object(roll_tracking.frappe, "get_doc", return_value=roll), patch.object(roll_tracking, "_save_roll") as save, patch.object(roll_tracking, "now_datetime", return_value="2026-11-24 00:00:00"):
			roll_tracking.append_stock_movement_history(
				roll.name, operation="Printing", operation_sequence=3,
				production_job="SE-JOB-2", plan_entry="SE-PLAN-2", quantity=800,
				source_batch="QC-BATCH", output_batch="PRINT-BATCH",
				source_warehouse="WIP", target_warehouse="WIP",
				stock_entry="MAT-STE-1", movement_type="Repack",
			)
			roll_tracking.append_stock_movement_history(
				roll.name, operation="Printing", operation_sequence=3,
				production_job="SE-JOB-2", plan_entry="SE-PLAN-2", quantity=800,
				source_batch="QC-BATCH", output_batch="PRINT-BATCH",
				stock_entry="MAT-STE-1", movement_type="Repack",
			)
		self.assertEqual(len(roll.operation_history), 1)
		self.assertEqual(roll.operation_history[0]["stock_entry"], "MAT-STE-1")
		self.assertEqual(roll.operation_history[0]["output_batch"], "PRINT-BATCH")
		save.assert_called_once()


if __name__ == "__main__":
	unittest.main()
