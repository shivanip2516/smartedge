from unittest import TestCase
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing import sales_order_planning_status as planning_status


class TestSalesOrderPlanningStatus(TestCase):
	def test_statuses_are_derived_per_exact_sales_order_item(self):
		items = {
			"ROW-1": {"item_code": "FG-1", "required_qty": 100},
			"ROW-2": {"item_code": "FG-2", "required_qty": 50},
		}

		with patch.object(planning_status, "get_eligible_sales_order_items", return_value=items), patch.object(
			planning_status, "get_planned_quantities", return_value={}
		):
			self.assertEqual(
				planning_status.calculate_sales_order_planning_status(
					"SO-1", order=frappe._dict(docstatus=1, status="To Deliver")
				),
				"Not Planned",
			)

		with patch.object(planning_status, "get_eligible_sales_order_items", return_value=items), patch.object(
			planning_status, "get_planned_quantities", return_value={"ROW-1": 40}
		):
			self.assertEqual(
				planning_status.calculate_sales_order_planning_status(
					"SO-1", order=frappe._dict(docstatus=1, status="To Deliver")
				),
				"Partially Planned",
			)

		with patch.object(planning_status, "get_eligible_sales_order_items", return_value=items), patch.object(
			planning_status, "get_planned_quantities", return_value={"ROW-1": 100, "ROW-2": 50}
		):
			self.assertEqual(
				planning_status.calculate_sales_order_planning_status(
					"SO-1", order=frappe._dict(docstatus=1, status="To Deliver")
				),
				"Planned",
			)

	def test_child_reference_requires_exact_sales_order_and_item(self):
		planned = {}
		items = {"ROW-1": {"item_code": "FG-1", "required_qty": 100}}

		planning_status.add_reference_quantity(
			planned,
			frappe._dict(
				sales_order="SO-OTHER",
				sales_order_item="ROW-1",
				finished_item="FG-1",
				converted_production_quantity=100,
			),
			"SO-1",
			items,
		)
		planning_status.add_reference_quantity(
			planned,
			frappe._dict(
				sales_order="SO-1",
				sales_order_item="ROW-OTHER",
				finished_item="FG-1",
				converted_production_quantity=100,
			),
			"SO-1",
			items,
		)
		planning_status.add_reference_quantity(
			planned,
			frappe._dict(
				sales_order="SO-1",
				sales_order_item="ROW-1",
				finished_item="FG-2",
				converted_production_quantity=100,
			),
			"SO-1",
			items,
		)

		self.assertEqual(planned, {})

	def test_legacy_primary_reference_is_supported(self):
		planned = {}
		items = {"ROW-1": {"item_code": "FG-1", "required_qty": 100}}
		planning_status.add_legacy_quantity(
			planned,
			frappe._dict(
				sales_order="SO-1",
				sales_order_item="ROW-1",
				finished_item="FG-1",
				production_quantity=100,
			),
			"SO-1",
			items,
		)
		self.assertEqual(planned, {"ROW-1": 100})


if __name__ == "__main__":
	import unittest

	unittest.main()
