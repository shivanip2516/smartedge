import unittest
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import scrap_recovery


class TestScrapRecovery(unittest.TestCase):
	def setUp(self):
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)

		self.enterContext(patch.object(scrap_recovery, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(scrap_recovery.frappe, "throw", side_effect=throw))
		self.enterContext(
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)")
		)
		frappe.local.db = Mock()
		frappe.local.session = frappe._dict(user="operator@example.com")
		self.addCleanup(delattr, frappe.local, "db")
		self.addCleanup(delattr, frappe.local, "session")

	def test_grinder_and_pulverizer_balance_accept_actual_loss(self):
		self.assertEqual(scrap_recovery._validate_balance(100, 95, 5, "Grinder"), (100, 95, 5))
		self.assertEqual(scrap_recovery._validate_balance(95, 90, 5, "Pulverizer"), (95, 90, 5))

	def test_unexplained_recovery_difference_is_blocked(self):
		with self.assertRaisesRegex(frappe.ValidationError, r"Input = Output \+ Process Loss"):
			scrap_recovery._validate_balance(100, 94, 5, "Grinder")

	def test_negative_and_non_numeric_quantities_are_blocked(self):
		for value in (-1, "invalid", "NaN"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				scrap_recovery._positive(value, "Quantity")

	def test_selected_recovered_item_batch_flag_is_authoritative(self):
		item = frappe._dict(name="RECOVERED-ITEM", disabled=0, is_stock_item=1, has_batch_no=1, stock_uom="Kg")
		with patch.object(scrap_recovery.frappe.db, "get_value", return_value=item):
			self.assertEqual(scrap_recovery._stock_item("RECOVERED-ITEM", "Recovered Material Item").name, "RECOVERED-ITEM")

	def test_recovered_item_without_batch_flag_remains_blocked(self):
		item = frappe._dict(name="RECOVERED-ITEM", disabled=0, is_stock_item=1, has_batch_no=0, stock_uom="Kg")
		with patch.object(scrap_recovery.frappe.db, "get_value", return_value=item):
			with self.assertRaisesRegex(frappe.ValidationError, "Recovered Material Item .*batch tracking enabled"):
				scrap_recovery._stock_item("RECOVERED-ITEM", "Recovered Material Item")

	def test_completed_grinder_retry_reuses_existing_posting(self):
		doc = frappe._dict(
			name="REC-1", status="Ground", grinder_stock_entry="STE-GRINDER",
			ground_material_batch="GROUND-1",
		)
		doc.check_permission = Mock()
		with patch.object(scrap_recovery, "_require_recovery_user"), patch.object(
			scrap_recovery.frappe.db, "get_value", return_value="REC-1"
		), patch.object(scrap_recovery.frappe, "get_doc", return_value=doc), patch.object(
			scrap_recovery, "_stock_entry"
		) as stock_entry:
			result = scrap_recovery.complete_grinder("REC-1")
		self.assertEqual(result["stock_entry"], "STE-GRINDER")
		stock_entry.assert_not_called()

	def test_existing_scrap_collection_is_idempotent(self):
		job = frappe._dict(name="JOB-1", usable_scrap=40)
		lot = frappe._dict(name="LOT-1", quantity=40, status="Active", stock_entry="STE-1")
		with patch.object(scrap_recovery.frappe.db, "get_value", side_effect=["LOT-1", 1]), patch.object(
			scrap_recovery.frappe, "get_doc", return_value=lot
		), patch.object(scrap_recovery, "_stock_entry") as stock_entry:
			self.assertEqual(scrap_recovery.register_usable_scrap(job, frappe._dict()), "LOT-1")
		stock_entry.assert_not_called()

	def test_zero_usable_scrap_does_not_require_configuration(self):
		with patch.object(scrap_recovery, "_material_config") as material_config:
			self.assertIsNone(
				scrap_recovery.register_usable_scrap(
					frappe._dict(name="JOB-1", usable_scrap=0),
					frappe._dict(finished_item="IT-000002"),
				)
			)
		material_config.assert_not_called()

	def test_positive_usable_scrap_requires_source_item_mapping(self):
		with patch.object(scrap_recovery.frappe.db, "get_value", return_value=None), patch.object(
			scrap_recovery, "_material_config", return_value=None
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Recovery Material mapping"):
				scrap_recovery.register_usable_scrap(
					frappe._dict(name="JOB-1", usable_scrap=1),
					frappe._dict(finished_item="IT-000002"),
				)

	def test_partial_processing_is_allowed_and_insufficient_stock_is_blocked(self):
		with patch.object(scrap_recovery, "_batch_available", return_value=100):
			self.assertEqual(scrap_recovery._assert_available("SCRAP", "BATCH", "WH", 40, "Scrap"), 100)
		with patch.object(scrap_recovery, "_batch_available", return_value=30):
			with self.assertRaisesRegex(frappe.ValidationError, "only 30"):
				scrap_recovery._assert_available("SCRAP", "BATCH", "WH", 50, "Scrap")

	def test_pulverizer_cannot_skip_grinder(self):
		doc = frappe._dict(name="REC-1", status="Draft", grinder_stock_entry=None, ground_material_batch=None)
		doc.check_permission = Mock()
		with patch.object(scrap_recovery, "_require_recovery_user"), patch.object(
			scrap_recovery.frappe.db, "get_value", return_value="REC-1"
		), patch.object(scrap_recovery.frappe, "get_doc", return_value=doc):
			with self.assertRaisesRegex(frappe.ValidationError, "successfully posted Grinder output"):
				scrap_recovery.complete_pulverizer("REC-1")

	def test_reuse_requires_an_explicit_matching_rule(self):
		rule = frappe._dict(
			name="RR-1", recovered_material_item="RECOVERED", recovered_item_group=None,
			target_item="FG-1", target_item_group=None, operation="Extrusion",
		)
		with patch.object(scrap_recovery.frappe, "get_all", return_value=[rule]):
			self.assertEqual(scrap_recovery._matching_rule("RECOVERED", "Extrusion", "FG-1").name, "RR-1")
			self.assertIsNone(scrap_recovery._matching_rule("OTHER", "Extrusion", "FG-1"))


if __name__ == "__main__":
	unittest.main()
