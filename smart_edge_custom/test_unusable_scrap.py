import unittest
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import unusable_scrap


class TestUnusableScrap(unittest.TestCase):
	def setUp(self):
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)

		self.enterContext(patch.object(unusable_scrap, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(unusable_scrap.frappe, "throw", side_effect=throw))
		self.enterContext(patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"))
		frappe.local.db = Mock()
		frappe.local.session = frappe._dict(user="operator@example.com")
		self.addCleanup(delattr, frappe.local, "db")
		self.addCleanup(delattr, frappe.local, "session")

	def test_unusable_configuration_requires_item_and_warehouse_together(self):
		config = frappe._dict(unusable_scrap_item="UNUSABLE", unusable_scrap_warehouse=None)
		with self.assertRaisesRegex(frappe.ValidationError, "Configure both"):
			unusable_scrap.validate_unusable_configuration(config)

	def test_unusable_item_cannot_be_a_recovery_item(self):
		config = frappe._dict(
			company="ACME", unusable_scrap_item="SCRAP", unusable_scrap_warehouse="SCRAP-WH",
			usable_scrap_item="SCRAP", ground_material_item="GROUND", recovered_material_item="RECOVERED",
		)
		with patch.object(unusable_scrap, "_item", return_value=frappe._dict(name="SCRAP")), patch.object(
			unusable_scrap, "_warehouse", return_value="SCRAP-WH"
		):
			with self.assertRaisesRegex(frappe.ValidationError, "must be separate"):
				unusable_scrap.validate_unusable_configuration(config)

	def test_existing_scrap_lot_is_idempotent(self):
		job = frappe._dict(name="JOB-1", unusable_scrap=25)
		lot = frappe._dict(name="UNS-1", quantity=25)
		with patch.object(unusable_scrap.frappe.db, "get_value", return_value="UNS-1"), patch.object(
			unusable_scrap.frappe, "get_doc", return_value=lot
		):
			self.assertEqual(unusable_scrap.prepare_unusable_scrap(job, frappe._dict()), "UNS-1")

	def test_zero_unusable_scrap_does_not_require_configuration(self):
		with patch.object(unusable_scrap, "_config") as config:
			self.assertIsNone(
				unusable_scrap.prepare_unusable_scrap(
					frappe._dict(name="JOB-1", unusable_scrap=0),
					frappe._dict(finished_item="IT-000002"),
				)
			)
		config.assert_not_called()

	def test_positive_unusable_scrap_requires_item_and_warehouse_mapping(self):
		job = frappe._dict(name="JOB-1", unusable_scrap=1)
		with patch.object(unusable_scrap.frappe.db, "get_value", return_value=None), patch.object(
			unusable_scrap, "_config", return_value=None
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Unusable Scrap Item and authorized Warehouse"):
				unusable_scrap.prepare_unusable_scrap(
					job,
					frappe._dict(finished_item="IT-000002"),
				)

	def test_retry_with_changed_quantity_is_blocked(self):
		job = frappe._dict(name="JOB-1", unusable_scrap=26)
		lot = frappe._dict(name="UNS-1", quantity=25)
		with patch.object(unusable_scrap.frappe.db, "get_value", return_value="UNS-1"), patch.object(
			unusable_scrap.frappe, "get_doc", return_value=lot
		):
			with self.assertRaisesRegex(frappe.ValidationError, "differs"):
				unusable_scrap.prepare_unusable_scrap(job, frappe._dict())

	def test_unusable_warehouse_is_blocked_from_normal_consumption(self):
		doc = frappe._dict(custom_smartedge_scrap_movement_type=None)
		doc.items = [frappe._dict(s_warehouse="UNUSABLE-WH", batch_no=None)]
		doc.get = lambda key, default=None: doc.items if key == "items" else dict.get(doc, key, default)
		with patch.object(unusable_scrap.frappe, "get_all", return_value=["UNUSABLE-WH"]):
			with self.assertRaisesRegex(frappe.ValidationError, "restricted"):
				unusable_scrap.reject_unusable_scrap_consumption(doc)

	def test_disposal_requires_manager_authority(self):
		with patch.object(unusable_scrap.frappe, "get_roles", return_value=["Manufacturing User"]):
			with self.assertRaises(frappe.PermissionError):
				unusable_scrap._require_disposal_authority()

	def test_disposal_cannot_exceed_available_stock(self):
		lot = frappe._dict(
			name="UNS-1", status="Pending Disposal", company="ACME", scrap_batch="B-1",
			scrap_item="UNUSABLE", warehouse="SCRAP-WH", uom="Kg",
		)
		lot.check_permission = Mock()
		with patch.object(unusable_scrap, "_require_disposal_authority"), patch.object(
			unusable_scrap.frappe.db, "get_value", side_effect=["UNS-1", None, "B-1"]
		), patch.object(unusable_scrap.frappe, "get_doc", return_value=lot), patch.object(
			unusable_scrap.frappe.db, "exists", return_value=None
		), patch.object(unusable_scrap, "_validate_employee"), patch.object(
			unusable_scrap, "_available", return_value=40
		):
			with self.assertRaisesRegex(frappe.ValidationError, "exceeds"):
				unusable_scrap.dispose_unusable_scrap("UNS-1", 41, "Damaged", "EMP-1", request_id="REQ-1")

	def test_disposal_request_requires_stored_stock(self):
		lot = frappe._dict(name="UNS-1", status="Pending Storage", storage_stock_entry=None)
		lot.check_permission = Mock()
		with patch.object(unusable_scrap, "_require_disposal_authority"), patch.object(
			unusable_scrap.frappe.db, "get_value", return_value="UNS-1"
		), patch.object(unusable_scrap.frappe, "get_doc", return_value=lot):
			with self.assertRaisesRegex(frappe.ValidationError, "successfully stored"):
				unusable_scrap.request_disposal("UNS-1")

	def test_partial_disposal_balance_uses_lot_and_physical_stock(self):
		lot = frappe._dict(
			name="UNS-1", stored_quantity=100, scrap_batch="B-1",
			scrap_item="UNUSABLE", warehouse="SCRAP-WH",
		)
		with patch.object(unusable_scrap.frappe, "get_all", side_effect=[["STE-1"], [60]]), patch(
			"erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=50
		):
			self.assertEqual(unusable_scrap._available(lot), 40)

	def test_submitted_disposal_retry_is_idempotent(self):
		existing = frappe._dict(name="STE-DISPOSAL", docstatus=1)
		with patch.object(unusable_scrap, "_require_disposal_authority"), patch.object(
			unusable_scrap.frappe.db, "get_value", side_effect=["UNS-1", existing]
		):
			result = unusable_scrap.dispose_unusable_scrap(
				"UNS-1", 10, "Damaged", "EMP-1", request_id="REQ-1"
			)
		self.assertEqual(result, {"stock_entry": "STE-DISPOSAL", "idempotent": 1})


if __name__ == "__main__":
	unittest.main()
