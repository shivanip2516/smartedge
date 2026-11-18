import unittest
from types import SimpleNamespace
from unittest.mock import patch

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


if __name__ == "__main__":
	unittest.main()
