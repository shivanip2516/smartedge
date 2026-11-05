from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import planning


def raise_validation_error(*args, **kwargs):
	raise frappe.ValidationError(args[0] if args else None)


class TestWorkstationReassignment(TestCase):
	def setUp(self):
		self.entry = frappe._dict(
			name="PLAN-1",
			status="Planned",
			workstation="Extruder-1",
		)

	@patch.object(planning.frappe, "get_all", return_value=[])
	def test_pending_planned_and_ready_can_change_before_start(self, _get_all):
		for status in ("Pending", "Planned", "Ready"):
			self.entry.status = status
			planning.validate_workstation_reassignment_allowed(self.entry, "Extruder-2")

	@patch.object(planning.frappe, "throw", side_effect=raise_validation_error)
	def test_execution_statuses_cannot_change(self, _throw):
		for status in ("In Progress", "Paused", "Completed", "Cancelled", "On Hold"):
			self.entry.status = status
			with self.assertRaises(frappe.ValidationError):
				planning.validate_workstation_reassignment_allowed(self.entry, "Extruder-2")

	@patch.object(planning.frappe, "throw", side_effect=raise_validation_error)
	@patch.object(planning.frappe, "get_all")
	def test_stale_planned_status_is_blocked_when_actual_start_exists(self, get_all, _throw):
		get_all.return_value = [
			frappe._dict(name="JOB-1", status="Draft", actual_start="2026-11-05 09:00:00")
		]
		with self.assertRaises(frappe.ValidationError):
			planning.validate_workstation_reassignment_allowed(self.entry, "Extruder-2")

	@patch.object(planning.frappe, "get_doc")
	@patch.object(planning, "lock_production_job")
	@patch.object(planning.frappe, "get_all", return_value=["JOB-1"])
	def test_existing_draft_job_is_updated_without_replacement(self, _get_all, lock_job, get_doc):
		job = Mock()
		job.status = "Draft"
		job.actual_start = None
		get_doc.return_value = job
		self.entry.planned_start_datetime = "2026-11-06 09:00:00"
		self.entry.planned_end_datetime = "2026-11-06 16:00:00"
		self.entry.workstation = "Extruder-2"

		planning.update_unstarted_production_jobs_for_schedule(self.entry, workstation_changed=True)

		lock_job.assert_called_once_with("JOB-1")
		get_doc.assert_called_once_with("SmartEdge Production Job", "JOB-1")
		job.reload.assert_called_once_with()
		self.assertEqual(job.workstation, "Extruder-2")
		self.assertEqual(job.planned_start, self.entry.planned_start_datetime)
		self.assertEqual(job.planned_end, self.entry.planned_end_datetime)
		job.save.assert_called_once_with()

	@patch.object(planning.frappe, "throw", side_effect=raise_validation_error)
	@patch.object(planning.frappe, "get_doc")
	@patch.object(planning, "lock_production_job")
	@patch.object(planning.frappe, "get_all", return_value=["JOB-1"])
	def test_job_start_race_blocks_reassignment(self, _get_all, _lock_job, get_doc, _throw):
		job = Mock()
		job.status = "In Progress"
		job.actual_start = "2026-11-05 09:00:00"
		get_doc.return_value = job

		with self.assertRaises(frappe.ValidationError):
			planning.update_unstarted_production_jobs_for_schedule(self.entry, workstation_changed=True)

		job.save.assert_not_called()

	@patch.object(planning.frappe, "get_all")
	def test_same_workstation_does_not_query_execution(self, get_all):
		planning.validate_workstation_reassignment_allowed(self.entry, "Extruder-1")
		get_all.assert_not_called()
