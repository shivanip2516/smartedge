import unittest
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import operator_access


class TestOperatorAccess(unittest.TestCase):
	def setUp(self):
		frappe.local.session = frappe._dict(user="operator-a@example.com")
		frappe.local.db = Mock()
		self.addCleanup(delattr, frappe.local, "session")
		self.addCleanup(delattr, frappe.local, "db")

	def test_employee_mapping_uses_user_id_not_employee_name(self):
		with patch.object(operator_access.frappe, "get_meta") as get_meta, patch.object(
			operator_access.frappe, "get_all", return_value=["EMP-0007"]
		) as get_all:
			get_meta.return_value.has_field.return_value = True
			self.assertEqual(
				operator_access.get_user_employees("operator-a@example.com", "ACME"),
				{"EMP-0007"},
			)
		get_all.assert_called_once_with(
			"Employee",
			filters={"user_id": "operator-a@example.com", "status": "Active", "company": "ACME"},
			pluck="name",
			limit_page_length=100,
		)

	def test_assigned_operator_can_access_plan_entry(self):
		entry = frappe._dict(name="PLAN-1", final_eb_bom_generator="GEN-1", planned_employee="EMP-A")
		with patch.object(operator_access, "get_entry_company", return_value="ACME"), patch.object(
			operator_access, "can_access_company", return_value=True
		), patch.object(operator_access, "is_planning_supervisor", return_value=False), patch.object(
			operator_access, "get_effective_operator", return_value="EMP-A"
		), patch.object(operator_access, "get_user_employees", return_value={"EMP-A"}):
			self.assertTrue(operator_access.can_access_plan_entry(entry))

	def test_other_operator_and_unassigned_operation_are_denied(self):
		entry = frappe._dict(name="PLAN-1", final_eb_bom_generator="GEN-1", planned_employee="EMP-A")
		common = (
			patch.object(operator_access, "get_entry_company", return_value="ACME"),
			patch.object(operator_access, "can_access_company", return_value=True),
			patch.object(operator_access, "is_planning_supervisor", return_value=False),
			patch.object(operator_access, "get_user_employees", return_value={"EMP-B"}),
		)
		with common[0], common[1], common[2], common[3], patch.object(
			operator_access, "get_effective_operator", return_value="EMP-A"
		):
			self.assertFalse(operator_access.can_access_plan_entry(entry))
		with patch.object(operator_access, "get_entry_company", return_value="ACME"), patch.object(
			operator_access, "can_access_company", return_value=True
		), patch.object(operator_access, "is_planning_supervisor", return_value=False), patch.object(
			operator_access, "get_user_employees", return_value={"EMP-B"}
		), patch.object(operator_access, "get_effective_operator", return_value=None):
			self.assertFalse(operator_access.can_access_plan_entry(entry))

	def test_supervisor_sees_assigned_and_unassigned_entries(self):
		entry = frappe._dict(name="PLAN-1", final_eb_bom_generator="GEN-1", planned_employee=None)
		with patch.object(operator_access, "get_entry_company", return_value="ACME"), patch.object(
			operator_access, "can_access_company", return_value=True
		), patch.object(operator_access, "is_planning_supervisor", return_value=True):
			self.assertTrue(operator_access.can_access_plan_entry(entry))

	def test_company_restriction_still_applies_to_supervisor(self):
		entry = frappe._dict(name="PLAN-1", final_eb_bom_generator="GEN-1", planned_employee=None)
		with patch.object(operator_access, "get_entry_company", return_value="OTHER"), patch.object(
			operator_access, "can_access_company", return_value=False
		), patch.object(operator_access, "is_planning_supervisor", return_value=True):
			self.assertFalse(operator_access.can_access_plan_entry(entry))

	def test_running_job_operator_takes_precedence(self):
		with patch.object(
			operator_access.frappe.db,
			"get_value",
			return_value=frappe._dict(operator_employee="EMP-B", status="In Progress"),
		):
			self.assertEqual(operator_access.get_effective_operator("PLAN-1", "EMP-A"), "EMP-B")

	def test_multi_operator_board_returns_only_current_users_operations(self):
		from smart_edge_custom.manufacturing import planning

		rows = [
			frappe._dict(name="PLAN-A", final_eb_bom_generator="GEN-1", planned_employee="EMP-A"),
			frappe._dict(name="PLAN-B", final_eb_bom_generator="GEN-1", planned_employee="EMP-B"),
			frappe._dict(name="PLAN-U", final_eb_bom_generator="GEN-1", planned_employee=None),
		]
		generators = {"GEN-1": frappe._dict(company="ACME")}
		with patch.object(operator_access, "is_planning_supervisor", return_value=False), patch.object(
			operator_access, "get_user_employees", return_value={"EMP-A"}
		), patch.object(operator_access, "get_company_restrictions", return_value=set()), patch.object(
			planning.frappe, "get_all", return_value=[]
		):
			visible = planning.filter_authorized_plan_entries(rows, generators)
		self.assertEqual([row.name for row in visible], ["PLAN-A"])

	def test_unauthorized_operator_is_stopped_before_start_or_completion(self):
		from smart_edge_custom.manufacturing import planning

		entry = frappe._dict(name="PLAN-B", final_eb_bom_generator="GEN-1", planned_employee="EMP-B")
		denied = frappe.PermissionError("not assigned")
		with patch.object(planning.frappe, "get_doc", return_value=entry), patch.object(
			operator_access, "require_plan_entry_access", side_effect=denied
		):
			with self.assertRaises(frappe.PermissionError):
				planning.start_production_job("PLAN-B")
			with self.assertRaises(frappe.PermissionError):
				planning.complete_production_job("PLAN-B")

	def test_completed_operation_reassignment_is_rejected(self):
		from smart_edge_custom.manufacturing import planning

		entry = frappe._dict(
			name="PLAN-DONE",
			status="Completed",
			final_eb_bom_generator="GEN-1",
			planned_employee="EMP-A",
			check_permission=Mock(),
			reload=Mock(),
		)
		with patch.object(planning.frappe, "get_doc", return_value=entry), patch.object(
			planning, "lock_plan_entry"
		), patch.object(planning.frappe, "get_all", return_value=[]), patch.object(
			planning, "validate_employee"
		) as validate_employee, patch.object(
			planning.frappe.db, "get_value", return_value="ACME"
		), patch.object(planning.frappe, "throw", side_effect=frappe.ValidationError("completed")), patch(
			"smart_edge_custom.manufacturing.operator_access.require_planning_supervisor"
		):
			with self.assertRaises(frappe.ValidationError):
				planning.change_operator("PLAN-DONE", "EMP-B")
		validate_employee.assert_called_once()

	def test_running_reassignment_updates_current_assignment_without_resetting_history(self):
		from smart_edge_custom.manufacturing import planning

		entry = frappe._dict(
			name="PLAN-RUN",
			status="In Progress",
			final_eb_bom_generator="GEN-1",
			planned_employee="EMP-A",
			check_permission=Mock(),
			reload=Mock(),
			save=Mock(),
			add_comment=Mock(),
		)
		job_row = frappe._dict(name="JOB-1", status="In Progress", operator_employee="EMP-A", actual_start="2026-11-16 08:00:00")
		job = frappe._dict(
			name="JOB-1",
			status="In Progress",
			operator_employee="EMP-A",
			actual_start="2026-11-16 08:00:00",
			actual_end=None,
			productive_elapsed_seconds=321,
			reload=Mock(),
			check_permission=Mock(),
			save=Mock(),
		)
		with patch.object(planning.frappe, "get_doc", side_effect=[entry, job]), patch.object(
			planning.frappe, "get_all", return_value=[job_row]
		), patch.object(planning, "lock_plan_entry"), patch.object(
			planning, "lock_production_job"
		), patch.object(planning, "validate_employee"), patch.object(
			planning.frappe.db, "get_value", side_effect=["ACME", "Operator B"]
		), patch.object(planning, "update_generator_planning_status"), patch(
			"smart_edge_custom.manufacturing.operator_access.require_planning_supervisor"
		):
			result = planning.change_operator("PLAN-RUN", "EMP-B", "Shift change")

		self.assertEqual(result["operator_employee"], "EMP-B")
		self.assertEqual(job.operator_employee, "EMP-B")
		self.assertEqual(job.actual_start, "2026-11-16 08:00:00")
		self.assertEqual(job.productive_elapsed_seconds, 321)
		entry.add_comment.assert_called_once()


if __name__ == "__main__":
	unittest.main()
