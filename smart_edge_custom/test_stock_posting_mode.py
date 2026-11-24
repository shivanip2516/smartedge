import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing import physical_operation_stock
from smart_edge_custom.manufacturing import final_bom_generator
from smart_edge_custom import setup as smartedge_setup
from smart_edge_custom.manufacturing.final_bom_generator import (
	LEGACY_NON_STOCK,
	PHYSICAL_WIP,
	get_stock_posting_mode,
	validate_stock_posting_mode,
)


class TestStockPostingMode(unittest.TestCase):
	def test_missing_mode_is_legacy(self):
		self.assertEqual(get_stock_posting_mode({"stock_posting_mode": None}), LEGACY_NON_STOCK)
		self.assertEqual(get_stock_posting_mode({}), LEGACY_NON_STOCK)

	def test_valid_modes_are_preserved(self):
		self.assertEqual(get_stock_posting_mode({"stock_posting_mode": LEGACY_NON_STOCK}), LEGACY_NON_STOCK)
		self.assertEqual(get_stock_posting_mode({"stock_posting_mode": PHYSICAL_WIP}), PHYSICAL_WIP)

	def test_locked_mode_rejects_change_after_activity(self):
		doc = frappe._dict(name="FEBG-TEST", stock_posting_mode=PHYSICAL_WIP, production_status="In Production")
		class FakeDB:
			def get_value(self, *args, **kwargs):
				return LEGACY_NON_STOCK

		fake_frappe = type("FakeFrappe", (), {"db": FakeDB(), "throw": staticmethod(frappe.throw)})
		with patch.object(final_bom_generator, "frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.final_bom_generator._posting_activity_exists", return_value=True):
			with self.assertRaises(Exception):
				validate_stock_posting_mode(doc)

	def test_physical_posting_does_not_depend_on_current_site_flag(self):
		entry = frappe._dict(stock_movement_type="Stockable WIP Output", final_eb_bom_generator="FEBG-TEST")
		with patch("smart_edge_custom.manufacturing.physical_operation_stock.production_uses_physical_stock", return_value=True), \
			patch("smart_edge_custom.manufacturing.physical_operation_stock.enabled", return_value=False), \
			patch("smart_edge_custom.manufacturing.planning.is_final_stock_posting_entry", return_value=False):
			self.assertTrue(physical_operation_stock.should_post_operation(entry))

	def test_legacy_and_missing_modes_never_post_physical_operation(self):
		with patch("smart_edge_custom.manufacturing.physical_operation_stock.production_uses_physical_stock", return_value=False), \
			patch("smart_edge_custom.manufacturing.physical_operation_stock.enabled", return_value=True):
			self.assertFalse(physical_operation_stock.should_post_operation(
				frappe._dict(stock_movement_type="Stockable WIP Output", final_eb_bom_generator="FEBG-TEST")
			))

	def test_physical_first_material_operation_uses_dynamic_route_gate(self):
		entry = frappe._dict(
			name="PLAN-1",
			final_eb_bom_generator="FEBG-TEST",
			operation="Configured Material Step",
			operation_row="ROW-1",
			stock_movement_type="Stockable WIP Output",
		)
		with patch("smart_edge_custom.manufacturing.final_bom_generator.get_stored_stock_posting_mode", return_value=PHYSICAL_WIP), \
			patch.object(physical_operation_stock, "_is_first_material_consumption_entry", return_value=True), \
			patch.object(physical_operation_stock, "_has_material_requirements", return_value=True), \
			patch("smart_edge_custom.manufacturing.planning.is_final_stock_posting_entry", return_value=False):
			self.assertTrue(physical_operation_stock.should_post_operation(entry))

	def test_physical_material_operation_rejects_nonphysical_route_snapshot(self):
		entry = frappe._dict(
			name="PLAN-1",
			final_eb_bom_generator="FEBG-TEST",
			operation="Configured Material Step",
			operation_row="ROW-1",
			stock_movement_type="Non-Stock Operation",
		)
		with patch("smart_edge_custom.manufacturing.final_bom_generator.get_stored_stock_posting_mode", return_value=PHYSICAL_WIP), \
			patch.object(physical_operation_stock, "_is_first_material_consumption_entry", return_value=True), \
			patch.object(physical_operation_stock, "_has_material_requirements", return_value=True):
			with self.assertRaisesRegex(Exception, "locked route snapshot"):
				physical_operation_stock.should_post_operation(entry)

	def test_physical_intermediate_operation_does_not_silently_skip_stock(self):
		entry = frappe._dict(
			name="PLAN-2",
			final_eb_bom_generator="FEBG-TEST",
			operation="Intermediate Extrusion",
			planning_category="Manufacturing",
			stock_movement_type="Non-Stock Operation",
		)
		with patch("smart_edge_custom.manufacturing.final_bom_generator.get_stored_stock_posting_mode", return_value=PHYSICAL_WIP), \
			patch.object(physical_operation_stock, "_is_first_material_consumption_entry", return_value=False), \
			patch("smart_edge_custom.manufacturing.planning.is_final_stock_posting_entry", return_value=False):
			with self.assertRaisesRegex(Exception, "non-physical route snapshot"):
				physical_operation_stock.should_post_operation(entry)

	def test_physical_qc_handoff_is_posted_when_route_is_explicitly_physical(self):
		"""QC checkpoints use the same physical handoff path when configured as WIP."""
		for category in ("QC", "Printing", "Manufacturing"):
			entry = frappe._dict(
				name="PLAN-2",
				final_eb_bom_generator="FEBG-TEST",
				operation="QC After Extrusion" if category == "QC" else category,
				planning_category=category,
				stock_movement_type="WIP Transfer / Processing",
			)
			with patch("smart_edge_custom.manufacturing.final_bom_generator.get_stored_stock_posting_mode", return_value=PHYSICAL_WIP), \
				patch("smart_edge_custom.manufacturing.planning.is_final_stock_posting_entry", return_value=False):
				self.assertTrue(physical_operation_stock.should_post_operation(entry))

	def test_physical_qc_with_posted_predecessor_cannot_silently_skip(self):
		entry = frappe._dict(
			name="PLAN-QC",
			final_eb_bom_generator="FEBG-TEST",
			operation="QC After Extrusion",
			planning_category="QC",
			stock_movement_type="Non-Stock Operation",
		)
		with patch("smart_edge_custom.manufacturing.final_bom_generator.get_stored_stock_posting_mode", return_value=PHYSICAL_WIP), \
			patch.object(physical_operation_stock, "_is_first_material_consumption_entry", return_value=False), \
			patch.object(physical_operation_stock, "_has_physical_predecessor", return_value=True):
			with self.assertRaisesRegex(Exception, "non-physical route snapshot"):
				physical_operation_stock.should_post_operation(entry)

	def test_physical_route_uses_company_wip_warehouse_without_hardcoding(self):
		doc = frappe._dict(company="Company - SE", stock_posting_mode=PHYSICAL_WIP)
		with patch.object(final_bom_generator, "get_stock_posting_mode", return_value=PHYSICAL_WIP), \
			patch.object(final_bom_generator.frappe.db, "get_value", return_value="Work In Progress - SE"):
			self.assertEqual(
				final_bom_generator._route_wip_warehouse(
					doc, frappe._dict(stock_movement_type="WIP Transfer / Processing"),
					"WIP Transfer / Processing",
				),
				"Work In Progress - SE",
			)

	def test_default_route_movement_mapping_uses_sequence_and_operation_role(self):
		self.assertEqual(smartedge_setup.infer_stock_movement_type("Extrusion", 1), "Stockable WIP Output")
		self.assertEqual(smartedge_setup.infer_stock_movement_type("QC After Extrusion", 2), "WIP Transfer / Processing")
		self.assertEqual(smartedge_setup.infer_stock_movement_type("Printing", 3), "WIP Transfer / Processing")
		self.assertEqual(smartedge_setup.infer_stock_movement_type("Packing", 4), "Non-Stock Operation")


if __name__ == "__main__":
	unittest.main()
