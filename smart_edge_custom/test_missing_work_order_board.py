"""Rollback-only coverage of cleanup leaving a material plan's Work Order dangling."""
import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom import test_finished_operation_batches as operation_tests
from smart_edge_custom.manufacturing.batch_manufacturing import make_batch_work_order
from smart_edge_custom.manufacturing.planning import (
	complete_production_job, get_weekly_planning_board,
	validate_material_scheduling_context, validate_start_eligible,
)


class TestMissingWorkOrderBoard(unittest.TestCase):
	make_bom = operation_tests.TestFinishedOperationBatches.make_bom
	make_finished_bom = operation_tests.TestFinishedOperationBatches.make_finished_bom
	source = operation_tests.TestFinishedOperationBatches.source
	generator = operation_tests.TestFinishedOperationBatches.generator

	def setUp(self):
		operation_tests.TestFinishedOperationBatches.setUp(self)
		self.stack.close()
		self.valid_wo = make_batch_work_order(self.boms["Compounds"], self.items["Compounds"], 1, company=self.company)
		self.valid_wo.source_warehouse = self.warehouse
		self.valid_wo.wip_warehouse = self.warehouse
		self.valid_wo.fg_warehouse = self.warehouse
		self.valid_wo.insert().submit()
		self.valid = self.material_plan("Compound", "Compounds", self.valid_wo.name)
		self.missing_name = "DELETED-WO-" + frappe.generate_hash(length=10)
		self.missing = self.material_plan("Base Colour", "Base Colour", self.missing_name)

	def material_plan(self, material_type, group, work_order):
		entry = frappe.get_doc({"doctype": "SmartEdge Production Plan Entry",
			"final_eb_bom_generator": self.doc.name, "plan_type": "Required Material",
			"material_type": material_type, "material_item": self.items[group],
			"bom_no": self.boms[group], "work_order": work_order,
			"operation": "Mixing", "workstation": self.workstation, "planning_category": "Mixing",
			"production_quantity": 3, "planned_batches": 1, "weight_per_batch": 3,
			"quantity_uom": "Kg", "status": "Pending",
			"source_warehouse": self.warehouse, "wip_warehouse": self.warehouse, "target_warehouse": self.warehouse,
		})
		# Simulate the dangling link left by cleanup; production code never bypasses links.
		entry.flags.ignore_links = True
		return entry.insert()

	def test_board_keeps_valid_cards_and_reports_dangling_link_without_repair(self):
		work_orders = frappe.get_all("Work Order", fields=["name", "modified"], order_by="name")
		valid_before = frappe.get_doc("Work Order", self.valid_wo.name).as_dict()
		missing_before = frappe.get_doc("SmartEdge Production Plan Entry", self.missing.name).as_dict()
		board = get_weekly_planning_board()
		warnings = [row for row in board["work_order_warnings"] if row["work_order"] == self.missing_name]
		self.assertEqual(len(warnings), 1)
		self.assertIn(self.missing.name, warnings[0]["plan_entries"])
		cards = [card for card in board["all_operations"] if card["production"] == self.doc.name]
		self.assertEqual(len(cards), 5)
		self.assertEqual(len({card["execution_key"] for card in cards}), 1)
		bad = next(card for card in cards if card["id"] == self.missing.name)
		self.assertTrue(bad["work_order_missing"])
		self.assertIn(self.missing_name, bad["work_order_warning"])
		self.assertEqual(bad["status"], "Pending")
		good = next(card for card in cards if card["id"] == self.valid.name)
		self.assertFalse(good["work_order_missing"])
		self.assertEqual(good["work_order"], self.valid_wo.name)
		self.assertEqual(work_orders, frappe.get_all("Work Order", fields=["name", "modified"], order_by="name"))
		self.assertEqual(valid_before, frappe.get_doc("Work Order", self.valid_wo.name).as_dict())
		self.assertEqual(missing_before, frappe.get_doc("SmartEdge Production Plan Entry", self.missing.name).as_dict())

	def test_missing_mandatory_reference_blocks_planning_start_and_completion(self):
		for validator in (validate_material_scheduling_context, validate_start_eligible):
			with self.subTest(validator=validator.__name__):
				with self.assertRaisesRegex(frappe.ValidationError, self.missing.name):
					validator(self.missing)
		job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": self.missing.name,
			"final_eb_bom_generator": self.doc.name, "operation": "Mixing", "status": "In Progress",
			"workstation": self.workstation, "operator_employee": self.employee,
			"actual_start": frappe.utils.now_datetime(), "planned_qty_meter": 1,
		}).insert()
		with patch("smart_edge_custom.manufacturing.material_production.complete_material_production") as manufacture:
			with self.assertRaisesRegex(frappe.ValidationError, self.missing_name):
				complete_production_job(self.missing.name, actual_qty_meter=1)
			manufacture.assert_not_called()
		job.reload()
		self.assertEqual(job.status, "In Progress")
		self.assertFalse(job.actual_end)

	def test_orphaned_plan_is_excluded_from_board_reconciliation_without_data_repair(self):
		from smart_edge_custom.manufacturing.work_order_sync import sync_work_order_to_planning
		self.missing.final_eb_bom_generator = "DELETED-GENERATOR-" + frappe.generate_hash(length=8)
		self.missing.flags.ignore_links = True
		self.missing.save()
		before = frappe.get_doc("SmartEdge Production Plan Entry", self.missing.name).as_dict()
		with patch("smart_edge_custom.manufacturing.work_order_sync.sync_work_order_to_planning", wraps=sync_work_order_to_planning) as sync:
			board = get_weekly_planning_board()
			self.assertNotIn(self.missing_name, [call.args[0] for call in sync.call_args_list])
		self.assertFalse(any(row["work_order"] == self.missing_name for row in board["work_order_warnings"]))
		self.assertEqual(before, frappe.get_doc("SmartEdge Production Plan Entry", self.missing.name).as_dict())
		self.assertTrue(any(card["id"] == self.valid.name for card in board["all_operations"]))
