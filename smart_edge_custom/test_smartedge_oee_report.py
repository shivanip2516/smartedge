import unittest
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.smart_edge_custom.report.smartedge_oee_and_machine_kpi_report import (
	smartedge_oee_and_machine_kpi_report as report,
)


class TestSmartEdgeOEEReport(unittest.TestCase):
	def test_report_uses_safe_module_name(self):
		root = Path(__file__).resolve().parent / "smart_edge_custom" / "report" / "smartedge_oee_and_machine_kpi_report"
		metadata = json.loads((root / "smartedge_oee_and_machine_kpi_report.json").read_text())
		self.assertEqual(metadata["name"], "SmartEdge OEE and Machine KPI Report")
		script = (root / "smartedge_oee_and_machine_kpi_report.js").read_text()
		self.assertIn('frappe.query_reports["SmartEdge OEE and Machine KPI Report"]', script)

	def test_overlapping_downtime_intervals_are_merged(self):
		intervals = report._merge_intervals([
			(datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 11)),
			(datetime(2026, 1, 1, 10, 30), datetime(2026, 1, 1, 12)),
		])
		self.assertEqual(report._hours(intervals), 2)

	def test_missing_denominator_is_unavailable(self):
		self.assertIsNone(report._safe_pct(10, None))
		self.assertEqual(report._safe_pct(5, 10), 50)

	def test_job_context_skips_absent_plan_size_field(self):
		meta = frappe._dict(fields=[frappe._dict(fieldname=name) for name in (
			"planning_category", "material_item", "quantity_uom", "production_quantity", "planned_meter", "planned_kg",
		)])
		plan_values = frappe._dict(planning_category="Extrusion", material_item="ITEM-1", planned_kg=10)
		frappe.local.db = Mock()
		self.addCleanup(delattr, frappe.local, "db")
		with patch.object(report.frappe, "get_meta", return_value=meta), patch.object(
			report.frappe.db, "get_value", return_value=plan_values
		) as get_value:
			plan, production, product = report._job_context(frappe._dict(plan_entry="PLAN-1"))
		self.assertEqual(product, "ITEM-1")
		self.assertNotIn("custom_size", plan)
		self.assertEqual(get_value.call_args_list[0].args[2], [
			"planning_category", "material_item", "quantity_uom", "production_quantity", "planned_meter", "planned_kg",
		])

	def test_output_falls_back_to_validated_completion_components(self):
		job = frappe._dict(actual_qty_meter=0, actual_qty_kg=0, good_qty=233, rejected_qty=0, rework_qty=0,
			planned_qty_meter=0, planned_qty_kg=0)
		uom, actual, planned = report._resolve_output(job, frappe._dict(quantity_uom="Kg"), frappe._dict())
		self.assertEqual((uom, actual), ("Kg", 233))

	def test_mixed_measured_units_are_unavailable(self):
		job = frappe._dict(actual_qty_meter=10, actual_qty_kg=2, good_qty=10, rejected_qty=0, rework_qty=0)
		self.assertEqual(report._resolve_output(job, frappe._dict(quantity_uom="Meter"), frappe._dict()), (None, None, None))


if __name__ == "__main__":
	unittest.main()
