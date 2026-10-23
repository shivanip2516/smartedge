from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.fifo_batch_selection import get_fifo_batch_allocations
from smart_edge_custom.manufacturing.final_bom_generator import calculate_batch_allocations
from smart_edge_custom.manufacturing.planning import (
	get_revalidated_consumption_batches,
	set_final_component_batch_bundles,
)


class TestFIFOBatchSelection(IntegrationTestCase):
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_first_receipt_order")
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.frappe.db.get_value")
	def test_selects_oldest_complete_batches(self, get_value, get_batch_qty, get_receipts):
		get_value.return_value = frappe._dict(
			{"has_batch_no": 1, "has_serial_no": 0, "stock_uom": "Kg"}
		)
		get_batch_qty.return_value = [
			frappe._dict({"batch_no": "CMP-003", "qty": 65}),
			frappe._dict({"batch_no": "CMP-001", "qty": 65}),
			frappe._dict({"batch_no": "CMP-002", "qty": 65}),
		]
		get_receipts.return_value = {
			"CMP-001": receipt("2026-10-01 09:00:00", "SLE-1"),
			"CMP-002": receipt("2026-10-02 09:00:00", "SLE-2"),
			"CMP-003": receipt("2026-10-03 09:00:00", "SLE-3"),
		}

		rows = get_fifo_batch_allocations("Compound 1", "Stores - SE", 2, 65, "Compound")

		self.assertEqual([row["batch_no"] for row in rows], ["CMP-001", "CMP-002"])
		self.assertEqual(sum(row["selected_qty"] for row in rows), 130)
		get_batch_qty.assert_called_once_with(item_code="Compound 1", warehouse="Stores - SE")
		get_receipts.assert_called_once_with(
			"Compound 1", "Stores - SE", ["CMP-003", "CMP-001", "CMP-002"]
		)

	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_first_receipt_order")
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.frappe.db.get_value")
	def test_selects_shade_batches_and_recalculates_count(self, get_value, get_batch_qty, get_receipts):
		get_value.return_value = frappe._dict(
			{"has_batch_no": 1, "has_serial_no": 0, "stock_uom": "Kg"}
		)
		get_batch_qty.return_value = [
			frappe._dict({"batch_no": f"SHD-00{index}", "qty": 50}) for index in range(1, 5)
		]
		get_receipts.return_value = {
			f"SHD-00{index}": receipt(f"2026-10-0{index} 09:00:00", f"SLE-{index}")
			for index in range(1, 5)
		}

		three_rows = get_fifo_batch_allocations("Shade 1", "Shade Stores - SE", 3, 50, "Shade")
		two_rows = get_fifo_batch_allocations("Shade 1", "Shade Stores - SE", 2, 50, "Shade")

		self.assertEqual([row["batch_no"] for row in three_rows], ["SHD-001", "SHD-002", "SHD-003"])
		self.assertEqual([row["batch_no"] for row in two_rows], ["SHD-001", "SHD-002"])
		self.assertEqual(sum(row["selected_qty"] for row in two_rows), 100)

	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_first_receipt_order")
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.frappe.db.get_value")
	def test_skips_zero_and_partial_batches(self, get_value, get_batch_qty, get_receipts):
		get_value.return_value = frappe._dict(
			{"has_batch_no": 1, "has_serial_no": 0, "stock_uom": "Kg"}
		)
		get_batch_qty.return_value = [
			frappe._dict({"batch_no": "CMP-ZERO", "qty": 0}),
			frappe._dict({"batch_no": "CMP-PARTIAL", "qty": 20}),
			frappe._dict({"batch_no": "CMP-FULL-1", "qty": 65}),
			frappe._dict({"batch_no": "CMP-FULL-2", "qty": 65}),
		]
		get_receipts.return_value = {
			"CMP-ZERO": receipt("2026-09-01 09:00:00", "SLE-0"),
			"CMP-PARTIAL": receipt("2026-09-02 09:00:00", "SLE-P"),
			"CMP-FULL-1": receipt("2026-09-03 09:00:00", "SLE-1"),
			"CMP-FULL-2": receipt("2026-09-04 09:00:00", "SLE-2"),
		}

		rows = get_fifo_batch_allocations("Compound 1", "Stores - SE", 2, 65, "Compound")

		self.assertEqual([row["batch_no"] for row in rows], ["CMP-FULL-1", "CMP-FULL-2"])

	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_first_receipt_order")
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.frappe.db.get_value")
	def test_insufficient_complete_batches_raises(self, get_value, get_batch_qty, get_receipts):
		get_value.return_value = frappe._dict(
			{"has_batch_no": 1, "has_serial_no": 0, "stock_uom": "Kg"}
		)
		get_batch_qty.return_value = [frappe._dict({"batch_no": "SHD-001", "qty": 50})]
		get_receipts.return_value = {"SHD-001": receipt("2026-10-01 09:00:00", "SLE-1")}

		with self.assertRaisesRegex(frappe.ValidationError, "Requested: 3 batches. Available: 1"):
			get_fifo_batch_allocations("Shade 1", "Stores - SE", 3, 50, "Shade")

	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_uom_conv_factor", return_value=1000)
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.get_first_receipt_order")
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	@patch("smart_edge_custom.manufacturing.fifo_batch_selection.frappe.db.get_value")
	def test_shade_fifo_compares_kg_requirement_with_gram_stock(
		self, get_value, get_batch_qty, get_receipts, _get_global_factor
	):
		get_value.side_effect = [
			frappe._dict({"has_batch_no": 1, "has_serial_no": 0, "stock_uom": "Gram"}),
			None,
		]
		get_batch_qty.return_value = [frappe._dict({"batch_no": "SHD-G-001", "qty": 300})]
		get_receipts.return_value = {
			"SHD-G-001": receipt("2026-10-01 09:00:00", "SLE-G-1")
		}

		rows = get_fifo_batch_allocations("Shade Gram", "Stores - SE", 1, 0.3, "Shade")

		self.assertEqual(rows[0]["batch_no"], "SHD-G-001")
		self.assertEqual(rows[0]["available_qty"], 0.3)
		self.assertEqual(rows[0]["selected_qty"], 0.3)

	@patch("smart_edge_custom.manufacturing.final_bom_generator.get_fifo_batch_allocations")
	@patch("smart_edge_custom.manufacturing.final_bom_generator.get_prepared_component_batch_weight")
	def test_explicit_refresh_reallocates_a_planned_record(self, get_batch_weight, get_allocations):
		get_batch_weight.return_value = 65
		get_allocations.return_value = [allocation("CMP-NEW", 65)]
		doc = frappe._dict(
			{
				"status": "Planned",
				"_refresh_batch_allocations": 1,
				"compound": "Compound 1",
				"compound_consumption_batches": 1,
				"compound_batch_allocations": [allocation("CMP-OLD", 65)],
				"shade_consumption_batches": 0,
				"source_warehouse": "Stores - SE",
			}
		)

		result = calculate_batch_allocations(
			doc, [{"component_type": "Compound", "item_code": "Compound 1"}]
		)

		self.assertEqual(result["compound_batch_allocations"][0].batch_no, "CMP-NEW")

	def test_completed_record_rejects_explicit_reallocation(self):
		doc = frappe._dict(
			{
				"status": "Completed",
				"_refresh_batch_allocations": 1,
				"compound_batch_allocations": [],
			}
		)

		with self.assertRaisesRegex(frappe.ValidationError, "cannot be refreshed"):
			calculate_batch_allocations(doc, [])

	@patch("smart_edge_custom.manufacturing.planning.get_kg_to_stock_factor", return_value=1)
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	def test_posting_revalidates_and_uses_saved_batches(self, get_batch_qty, _factor):
		get_batch_qty.return_value = [
			frappe._dict({"batch_no": "CMP-001", "qty": 65}),
			frappe._dict({"batch_no": "CMP-002", "qty": 65}),
		]
		allocations = [allocation("CMP-001", 65), allocation("CMP-002", 65)]
		component = {"item_code": "Compound 1", "s_warehouse": "Stores - SE", "qty": 100}

		batches = get_revalidated_consumption_batches(
			component, allocations, "Compound", "Kg"
		)

		self.assertEqual(batches, {"CMP-001": 65, "CMP-002": 35})

	@patch("smart_edge_custom.manufacturing.planning.get_kg_to_stock_factor", return_value=1)
	@patch("erpnext.stock.doctype.batch.batch.get_batch_qty")
	def test_posting_blocks_when_selected_stock_changed(self, get_batch_qty, _factor):
		get_batch_qty.return_value = [frappe._dict({"batch_no": "CMP-001", "qty": 40})]
		component = {"item_code": "Compound 1", "s_warehouse": "Stores - SE", "qty": 65}

		with self.assertRaisesRegex(frappe.ValidationError, "no longer has sufficient stock"):
			get_revalidated_consumption_batches(
				component, [allocation("CMP-001", 65)], "Compound", "Kg"
			)

	@patch("erpnext.stock.serial_batch_bundle.SerialBatchCreation")
	def test_stock_entry_uses_outward_serial_batch_bundle(self, serial_batch_creation):
		serial_batch_creation.return_value.make_serial_and_batch_bundle.return_value = frappe._dict(
			{"name": "SABB-OUT-1"}
		)
		row = frappe._dict(
			{
				"name": "SED-1",
				"item_code": "Compound 1",
				"s_warehouse": "Stores - SE",
				"transfer_qty": 130,
			}
		)
		stock_entry = MagicMock()
		stock_entry.items = [row]
		stock_entry.posting_date = "2026-10-19"
		stock_entry.posting_time = "12:00:00"
		stock_entry.doctype = "Stock Entry"
		stock_entry.name = "STE-1"
		stock_entry.company = "Smart Edge"

		set_final_component_batch_bundles(stock_entry, [{"CMP-001": 65, "CMP-002": 65}])

		serial_batch_creation.return_value.make_serial_and_batch_bundle.assert_called_once_with(
			batch_nos={"CMP-001": 65, "CMP-002": 65}
		)
		self.assertEqual(row.serial_and_batch_bundle, "SABB-OUT-1")


def receipt(posting_datetime, ledger):
	return frappe._dict(
		{
			"posting_datetime": posting_datetime,
			"creation": posting_datetime,
			"name": ledger,
		}
	)


def allocation(batch_no, qty):
	return frappe._dict(
		{
			"batch_no": batch_no,
			"item_code": "Compound 1",
			"warehouse": "Stores - SE",
			"selected_qty": qty,
		}
	)
