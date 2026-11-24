import unittest
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import planning


class TestPlanningDanglingProductionJob(unittest.TestCase):
	def setUp(self):
		self.db = Mock()
		frappe.local.db = self.db

	def tearDown(self):
		del frappe.local.db

	def test_missing_linked_job_has_actionable_error(self):
		entry = frappe._dict(production_job="SE-JOB-MISSING", operation="QC After Extrusion")
		def capture_throw(message, **kwargs):
			self.message = str(message)
			raise frappe.ValidationError(self.message)

		with patch.object(planning.frappe, "throw", side_effect=capture_throw), patch.object(
			self.db,
			"get_value",
			return_value=entry,
		), patch.object(self.db, "exists", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				planning.get_open_job("SE-PLAN-MISSING")
		self.assertIn("SE-JOB-MISSING", self.message)
		self.assertIn("controlled replanning workflow", self.message)

	def test_existing_linked_job_uses_normal_lookup(self):
		entry = frappe._dict(production_job="SE-JOB-1", operation="QC After Extrusion")
		job_row = frappe._dict(name="SE-JOB-1", status="In Progress")
		with patch.object(self.db, "get_value", return_value=entry), patch.object(
			self.db, "exists", return_value=True
		), patch.object(planning.frappe, "get_all", return_value=[job_row]), patch.object(
			planning.frappe, "get_doc", return_value=frappe._dict(check_permission=Mock())
		) as get_doc:
			planning.get_open_job("SE-PLAN-1")
		get_doc.assert_called_once_with("SmartEdge Production Job", "SE-JOB-1")

	def test_physical_packing_rejects_partial_reserved_roll_set(self):
		entry = frappe._dict(final_eb_bom_generator="GEN-1", planning_category="Packing", operation="Packing")
		job = frappe._dict(name="SE-JOB-1", good_qty=3236, actual_qty_meter=3236)
		with patch.object(planning, "physical_operation_route_active", return_value=True), patch.object(
			planning.frappe, "get_all", return_value=[frappe._dict(name="SE-ROLL-1", current_meter_quantity=1000)]
		), patch.object(planning.frappe, "throw", side_effect=frappe.ValidationError) as throw:
			with self.assertRaises(frappe.ValidationError):
				planning.validate_packing_rolls_for_completion(entry, job, "Full Completion")
		self.assertIn("reserved QC-approved Rolls", str(throw.call_args.args[0]))

	def test_physical_packing_accepts_reconciled_roll_set(self):
		entry = frappe._dict(final_eb_bom_generator="GEN-1", planning_category="Packing", operation="Packing")
		job = frappe._dict(name="SE-JOB-1", good_qty=3236, actual_qty_meter=3236)
		rolls = [frappe._dict(name="SE-ROLL-1", current_meter_quantity=2000), frappe._dict(name="SE-ROLL-2", current_meter_quantity=1236)]
		with patch.object(planning, "physical_operation_route_active", return_value=True), patch.object(
			planning.frappe, "get_all", return_value=rolls
		):
			self.assertIsNone(planning.validate_packing_rolls_for_completion(entry, job, "Full Completion"))

