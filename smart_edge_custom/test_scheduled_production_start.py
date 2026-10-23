from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.planning import (
	get_scheduled_start_eligibility,
	start_production_job,
)


class TestScheduledProductionStart(IntegrationTestCase):
	def test_future_scheduled_date_is_blocked(self):
		entry = frappe._dict({"planned_date": "2026-10-24"})
		result = get_scheduled_start_eligibility(entry, current_date="2026-10-23")
		self.assertFalse(result["allowed"])
		self.assertIn("24-10-2026", result["message"])

	def test_same_scheduled_date_is_allowed(self):
		entry = frappe._dict({"planned_date": "2026-10-24"})
		self.assertTrue(
			get_scheduled_start_eligibility(entry, current_date="2026-10-24")["allowed"]
		)

	def test_past_scheduled_date_is_allowed(self):
		entry = frappe._dict({"planned_date": "2026-10-24"})
		self.assertTrue(
			get_scheduled_start_eligibility(entry, current_date="2026-10-25")["allowed"]
		)

	def test_missing_scheduled_date_is_blocked(self):
		result = get_scheduled_start_eligibility(
			frappe._dict({"planned_date": None}), current_date="2026-10-23"
		)
		self.assertFalse(result["allowed"])
		self.assertIn("Schedule the operation", result["message"])

	def test_rescheduling_to_today_allows_start(self):
		entry = frappe._dict({"planned_date": "2026-10-24"})
		self.assertFalse(
			get_scheduled_start_eligibility(entry, current_date="2026-10-23")["allowed"]
		)
		entry.planned_date = "2026-10-23"
		self.assertTrue(
			get_scheduled_start_eligibility(entry, current_date="2026-10-23")["allowed"]
		)

	def test_direct_api_is_blocked_before_capacity_or_material_transfer(self):
		entry = MagicMock()
		entry.name = "PLAN-FUTURE-1"
		entry.status = "Planned"
		entry.final_eb_bom_generator = "FEBG-1"
		entry.workstation = "mixing"
		entry.planned_date = "2026-10-24"
		entry.planned_start_datetime = "2026-10-24 10:00:00"
		entry.planned_end_datetime = "2026-10-24 11:00:00"
		entry.get.side_effect = lambda fieldname: {
			"planned_date": entry.planned_date,
			"plan_type": "Required Material",
		}.get(fieldname)

		with (
			patch("smart_edge_custom.manufacturing.planning.frappe.get_doc", return_value=entry),
			patch("smart_edge_custom.manufacturing.planning.frappe.db.get_value", return_value="SmartEdge"),
			patch("smart_edge_custom.manufacturing.planning.validate_employee"),
			patch("smart_edge_custom.manufacturing.planning.validate_selected_workstation"),
			patch("smart_edge_custom.manufacturing.planning.validate_capacity") as validate_capacity,
			patch("smart_edge_custom.manufacturing.planning.nowdate", return_value="2026-10-23"),
			patch(
				"smart_edge_custom.manufacturing.material_production.ensure_material_transfer"
			) as ensure_material_transfer,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "24-10-2026"):
				start_production_job("PLAN-FUTURE-1", operator_employee="EMP-1")

		validate_capacity.assert_not_called()
		ensure_material_transfer.assert_not_called()
		entry.reload.assert_not_called()
