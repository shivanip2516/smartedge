import unittest
from datetime import datetime, time, timedelta
from unittest.mock import patch

import frappe

from smart_edge_custom.smart_edge_custom.report.scrap_and_recovery_analytics import scrap_and_recovery_analytics as report


class TestScrapRecoveryAnalytics(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(report, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"))

	def test_recovery_and_unusable_percentages(self):
		self.assertEqual(report.safe_percentage(80, 100), 80)
		self.assertEqual(report.safe_percentage(20, 100), 20)

	def test_zero_denominator_and_incomplete_conversion_are_na(self):
		self.assertIsNone(report.safe_percentage(0, 0))
		self.assertIsNone(report.safe_percentage(80, 100, complete=False))

	def test_recovery_utilization_uses_comparable_released_cohort(self):
		self.assertEqual(report.safe_percentage(150, 200), 75)
		self.assertEqual(report.safe_percentage(40, 100), 40)

	def test_stock_uom_converts_to_kg_only_with_factor(self):
		self.assertEqual(report.stock_quantity_to_kg(1000, 1000), 1)
		self.assertIsNone(report.stock_quantity_to_kg(1000, None))

	def test_overnight_shift_uses_prior_production_date(self):
		actual = datetime(2026, 11, 4, 2, 0)
		self.assertEqual(
			report.shift_production_date(actual, timedelta(hours=22), timedelta(hours=6)),
			datetime(2026, 11, 3).date(),
		)
		self.assertEqual(report.shift_production_date(actual, time(8), time(17)), actual.date())

	def test_workstation_group_totals_reconcile(self):
		rows = [self._row("JOB-1", "WS-1", 50, 10), self._row("JOB-2", "WS-1", 30, 15)]
		group = report.aggregate_rows(rows, "Workstation")[0]
		self.assertEqual(group.production_jobs, 2)
		self.assertEqual(group.usable_scrap_kg, 80)
		self.assertEqual(group.unusable_scrap_kg, 25)
		self.assertEqual(group.total_scrap_kg, 105)

	def test_unassigned_dimension_remains_visible(self):
		group = report.aggregate_rows([self._row("JOB-1", None, 10, 5)], "Workstation")[0]
		self.assertEqual(group.group_value, "Unassigned")

	def test_source_cohorts_are_not_duplicated_between_products(self):
		first = self._row("JOB-1", "WS-1", 10, 0, item="ITEM-A", released=20, reused=10)
		second = self._row("JOB-2", "WS-2", 10, 0, item="ITEM-B", released=30, reused=5)
		groups = {row.group_value: row for row in report.aggregate_rows([first, second], "Product")}
		self.assertEqual(groups["ITEM-A"].recovered_material_reused_kg, 10)
		self.assertEqual(groups["ITEM-B"].recovered_material_reused_kg, 5)

	def test_shift_groups_are_separated_by_production_date(self):
		first = self._row("JOB-1", "WS", 10, 0)
		second = self._row("JOB-2", "WS", 20, 0)
		first.production_date = "2026-11-03"
		second.production_date = "2026-11-04"
		groups = report.aggregate_rows([first, second], "Shift")
		self.assertEqual(len(groups), 2)
		self.assertEqual({row.production_date for row in groups}, {"2026-11-03", "2026-11-04"})

	def test_product_group_preserves_item_name(self):
		row = self._row("JOB-1", "WS", 10, 0, item="ITEM-A")
		row.item_name = "Product A"
		self.assertEqual(report.aggregate_rows([row], "Product")[0].item_name, "Product A")

	def test_zero_recovery_supply_summary_displays_na(self):
		summary = report._summary([self._row("JOB-1", "WS", 10, 0)])
		self.assertEqual(summary[2]["value"], "N/A")

	def test_invalid_date_range_is_rejected(self):
		filters = frappe._dict(from_date="2026-11-05", to_date="2026-11-04", company="ACME")
		with patch.object(report.frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				report._validate_filters(filters)

	@staticmethod
	def _row(name, workstation, usable, unusable, item="ITEM", released=0, reused=0):
		return frappe._dict(
			production_job=name, workstation=workstation, item_code=item, item_name=item, shift="SHIFT", operation="OP",
			production_date="2026-11-04", production_quantity_kg=100,
			usable_scrap_kg=usable, unusable_scrap_kg=unusable, scrap_kg_complete=True,
			recovered_material_produced_kg=released, recovered_material_reused_kg=reused,
			recovered_available_for_reuse_kg=released, recovered_closing_available_kg=released - reused,
			scrap_stored_kg=unusable, scrap_disposed_kg=0, scrap_pending_disposal_kg=unusable,
			recovery_kg_complete=True,
		)


if __name__ == "__main__":
	unittest.main()
