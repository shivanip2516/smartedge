from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.final_bom_generator import apply_item_configuration_defaults
from smart_edge_custom.manufacturing.material_production import (
	build_material_planning_context,
	complete_material_production,
	create_material_plan,
	ensure_work_order_stock_entry,
	get_material_operations,
	get_material_workflow_status,
	get_material_workstation_context,
	get_raw_material_availability,
	get_linked_material_plans,
	get_material_status,
	get_overall_material_status,
	reconcile_work_order_plan,
)


class TestRequiredMaterialProduction(IntegrationTestCase):
	def test_material_workflow_status_tracks_internal_plan_and_release(self):
		doc = frappe._dict(
			{
				"compound": "TEST-COMPOUND",
				"base_colour": "TEST-SHADE",
				"compound_consumption_batches": 14,
				"shade_consumption_batches": 5,
			}
		)
		requirements = [
			{
				"status": "Planned",
				"already_planned_batches": 14,
				"completed_batches": 0,
				"batches_to_plan": 0,
			},
			{
				"status": "Planned",
				"already_planned_batches": 5,
				"completed_batches": 0,
				"batches_to_plan": 0,
			},
		]

		self.assertEqual(
			get_material_workflow_status(doc, requirements),
			"Material Production Planned",
		)
		self.assertEqual(
			get_material_workflow_status(doc, requirements, released_to_planning=True),
			"Sent to Planning",
		)

	@patch(
		"smart_edge_custom.manufacturing.final_bom_generator.get_finished_item_configuration",
		return_value={"custom_base_colour": "American Walnut"},
	)
	def test_finished_product_colour_does_not_select_prepared_base_colour(self, _configuration):
		doc = frappe._dict(
			{
				"finished_item": "TEST-FINISHED-ITEM",
				"custom_base_colour": None,
				"base_colour": None,
			}
		)

		apply_item_configuration_defaults(doc)

		self.assertEqual(doc.custom_base_colour, "American Walnut")
		self.assertIsNone(doc.base_colour)

	@patch(
		"smart_edge_custom.manufacturing.material_production.now_datetime",
		return_value="2026-10-22 09:00:00",
	)
	@patch("smart_edge_custom.manufacturing.material_production.create_material_plan_entry")
	@patch("smart_edge_custom.manufacturing.material_production.validate_material_workstation")
	@patch("smart_edge_custom.manufacturing.material_production.get_material_workstation_context")
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	@patch("smart_edge_custom.manufacturing.material_production.frappe.db.exists")
	def test_reconciliation_reuses_linked_work_order_without_creating_another(
		self, exists, get_doc, operation_context, _validate_workstation, create_entry, _now_datetime
	):
		exists.side_effect = [True, False]
		work_order = MagicMock()
		work_order.docstatus = 1
		work_order.status = "Not Started"
		work_order.company = "SmartEdge"
		work_order.production_item = "Compound 3"
		work_order.bom_no = "BOM-COMPOUND-1"
		work_order.source_warehouse = "Raw - SE"
		work_order.wip_warehouse = "WIP - SE"
		work_order.fg_warehouse = "Prepared - SE"
		work_order.get.side_effect = lambda fieldname: {
			"custom_final_eb_bom_generator": "FEBG-0001",
			"custom_material_type": "Compound",
			"custom_material_operation": "Mixing",
			"custom_material_workstation": "mixing",
		}.get(fieldname)
		get_doc.return_value = work_order
		operation_context.return_value = {
			"workstations": ["mixing"], "restricted": 0, "duration_hours": 1
		}
		entry = MagicMock()
		entry.name = "PLAN-RECOVERED-1"
		create_entry.return_value = entry

		result = reconcile_work_order_plan(
			frappe._dict({"name": "FEBG-0001", "company": "SmartEdge"}),
			{
				"material_type": "Compound",
				"item_code": "Compound 3",
				"bom_no": "BOM-COMPOUND-1",
			},
			"WO-EXISTING-1",
		)

		self.assertEqual(result.name, "PLAN-RECOVERED-1")
		create_entry.assert_called_once()

	@patch("smart_edge_custom.manufacturing.material_production.validate_raw_material_availability")
	@patch("smart_edge_custom.manufacturing.material_production.validate_warehouse")
	@patch("smart_edge_custom.manufacturing.planning.validate_employee")
	@patch("smart_edge_custom.manufacturing.material_production.validate_material_workstation")
	@patch("smart_edge_custom.manufacturing.material_production.get_material_workstation_context")
	@patch("smart_edge_custom.manufacturing.material_production.make_batch_work_order")
	@patch("smart_edge_custom.manufacturing.material_production.frappe.new_doc")
	def test_selected_operation_and_workstation_are_stored_on_plan(
		self,
		new_doc,
		make_work_order,
		operation_context,
		_validate_workstation,
		_validate_employee,
		_validate_warehouse,
		_validate_stock,
	):
		work_order = MagicMock()
		work_order.name = "WO-MATERIAL-1"
		work_order.qty = 20
		work_order.get.side_effect = lambda fieldname: {"custom_no_of_batches": 2}.get(fieldname)
		work_order.meta.has_field.return_value = False
		make_work_order.return_value = work_order
		operation_context.return_value = {
			"workstations": ["mixing"],
			"restricted": 0,
			"duration_hours": 1,
		}
		entry = MagicMock()
		entry.name = "PLAN-MATERIAL-1"
		new_doc.return_value = entry
		doc = frappe._dict({"name": "FEBG-0001", "company": "SmartEdge"})
		requirement = {
			"material_type": "Compound",
			"batches_to_plan": 2,
			"bom_no": "BOM-COMPOUND-1",
			"item_code": "Compound 3",
			"weight_per_batch": 10,
			"stock_uom": "Kg",
		}
		values = {
			"operation": "Mixing",
			"workstation": "mixing",
			"employee": "EMP-0001",
			"planned_start": "2026-10-22 09:00:00",
			"source_warehouse": "Raw - SE",
			"wip_warehouse": "WIP - SE",
			"target_warehouse": "Prepared - SE",
		}

		result = create_material_plan(doc, requirement, values)

		self.assertEqual(result["plan_entry"], "PLAN-MATERIAL-1")
		self.assertEqual(entry.operation, "Mixing")
		self.assertEqual(entry.workstation, "mixing")
		self.assertEqual(entry.status, "Pending")
		self.assertIsNone(entry.planned_date)
		work_order.set_required_items.assert_called_once_with(reset_source_warehouse=True)

	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.material_production.get_bom_operations", return_value=[])
	def test_bom_without_operations_falls_back_to_standard_operations(self, _bom_operations, get_all):
		get_all.return_value = [
			frappe._dict({"operation": "Mixing", "workstation": "mixing"}),
			frappe._dict({"operation": "Packing", "workstation": "Packing"}),
		]

		operations = get_material_operations("BOM-COMPOUND-1")

		self.assertEqual([row.operation for row in operations], ["Mixing", "Packing"])

	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.material_production.get_bom_operations")
	def test_missing_bom_does_not_fall_back_to_standard_operations(self, get_bom_operations, get_all):
		self.assertEqual(get_material_operations(None), [])
		get_bom_operations.assert_not_called()
		get_all.assert_not_called()

	@patch("smart_edge_custom.manufacturing.material_production.get_default_warehouses", return_value={})
	@patch("smart_edge_custom.manufacturing.material_production.get_linked_material_plans", return_value=[])
	@patch("smart_edge_custom.manufacturing.material_production.get_item_batch_details", return_value={})
	@patch("smart_edge_custom.manufacturing.material_production.get_material_workstation_context")
	def test_incomplete_generator_context_defers_bom_lookup(
		self, workstation_context, _item_details, _linked_plans, _warehouses
	):
		doc = frappe._dict(
			{
				"name": "FEBG-INCOMPLETE",
				"source_warehouse": None,
				"compound": None,
				"base_colour": None,
				"compound_consumption_batches": 0,
				"shade_consumption_batches": 0,
			}
		)

		context = build_material_planning_context(doc)

		self.assertFalse(context["has_shortage"])
		self.assertEqual([row["bom_no"] for row in context["requirements"]], [None, None])
		self.assertEqual([row["operation"] for row in context["requirements"]], [None, None])
		workstation_context.assert_not_called()

	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	def test_workstation_context_rejects_missing_bom_clearly(self, get_doc):
		with self.assertRaisesRegex(frappe.ValidationError, "active submitted batch BOM is required"):
			get_material_workstation_context(None, "Mixing")
		get_doc.assert_not_called()

	@patch("smart_edge_custom.manufacturing.planning.get_compatible_workstations", return_value=[])
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.material_production.get_bom_operations", return_value=[])
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	def test_unmapped_operation_allows_all_enabled_workstations(
		self, get_doc, _bom_operations, get_all, _compatible
	):
		bom = MagicMock()
		operation = MagicMock()
		operation.get.return_value = "mixing"
		get_doc.side_effect = [bom, operation]
		get_all.return_value = ["mixing", "Packing"]

		context = get_material_workstation_context("BOM-COMPOUND-1", "Mixing")

		self.assertEqual(context["workstations"], ["mixing", "Packing"])
		self.assertEqual(context["default_workstation"], "mixing")
		self.assertEqual(context["restricted"], 0)

	@patch(
		"smart_edge_custom.manufacturing.planning.get_compatible_workstations",
		return_value=["Dedicated Mixer"],
	)
	@patch(
		"smart_edge_custom.manufacturing.material_production.filter_enabled_workstations",
		return_value=["Dedicated Mixer"],
	)
	@patch("smart_edge_custom.manufacturing.material_production.get_bom_operations", return_value=[])
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	def test_explicit_workstation_mapping_remains_restricted(
		self, get_doc, _bom_operations, _enabled, _compatible
	):
		operation = MagicMock()
		operation.get.return_value = "mixing"
		get_doc.side_effect = [MagicMock(), operation]

		context = get_material_workstation_context("BOM-BASE-1", "Mixing")

		self.assertEqual(context["workstations"], ["Dedicated Mixer"])
		self.assertEqual(context["default_workstation"], "Dedicated Mixer")
		self.assertEqual(context["restricted"], 1)

	@patch("smart_edge_custom.manufacturing.material_production.frappe.db.get_value")
	@patch("smart_edge_custom.manufacturing.material_production.make_batch_work_order")
	def test_raw_stock_validation_is_shared_by_compound_and_base_colour(self, make_work_order, get_value):
		work_order = MagicMock()
		work_order.required_items = [
			frappe._dict(
				{
					"item_code": "RAW-1",
					"item_name": "Raw Material",
					"stock_uom": "Gram",
					"required_qty": 900,
					"include_item_in_manufacturing": 1,
				}
			)
		]
		make_work_order.return_value = work_order
		get_value.return_value = frappe._dict({"actual_qty": 1000, "reserved_qty_for_production": 200})
		doc = frappe._dict({"company": "SmartEdge"})

		for material_type in ("Compound", "Base Colour"):
			with self.subTest(material_type=material_type):
				stock = get_raw_material_availability(
					doc,
					{
						"material_type": material_type,
						"bom_no": "BOM-1",
						"item_code": "FINISHED-1",
						"batches_to_plan": 3,
					},
					"Raw Materials - SE",
				)
				self.assertEqual(stock["available"], 0)
				self.assertEqual(stock["materials"][0]["shortage_qty"], 100)

	@patch("smart_edge_custom.manufacturing.material_production.is_active_work_order", return_value=True)
	@patch("smart_edge_custom.manufacturing.material_production.get_work_order_completed_batches", return_value=0)
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	def test_only_plans_targeting_required_warehouse_reduce_shortage(
		self, get_all, _completed_batches, _active_work_order
	):
		get_all.return_value = [
			frappe._dict(
				{
					"name": "PLAN-RIGHT",
					"status": "Planned",
					"work_order": "WO-RIGHT",
					"planned_batches": 2,
					"weight_per_batch": 10,
					"target_warehouse": "Prepared Materials - SE",
				}
			),
			frappe._dict(
				{
					"name": "PLAN-WRONG",
					"status": "Planned",
					"work_order": "WO-WRONG",
					"planned_batches": 3,
					"weight_per_batch": 10,
					"target_warehouse": "Other Warehouse - SE",
				}
			),
		]

		plans = get_linked_material_plans(
			"FEBG-0001", "Compound", "Prepared Materials - SE"
		)

		self.assertEqual(plans[0].outstanding_batches, 2)
		self.assertEqual(plans[1].outstanding_batches, 0)

	@patch("smart_edge_custom.manufacturing.material_production.is_active_work_order", return_value=True)
	@patch("smart_edge_custom.manufacturing.material_production.get_work_order_completed_batches", return_value=0)
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	def test_duplicate_plan_rows_do_not_count_the_same_work_order_twice(
		self, get_all, _completed_batches, _active_work_order
	):
		get_all.return_value = [
			frappe._dict(
				{
					"name": "PLAN-NEW",
					"status": "Planned",
					"work_order": "WO-ONE",
					"planned_batches": 2,
					"weight_per_batch": 10,
					"target_warehouse": "Prepared Materials - SE",
				}
			),
			frappe._dict(
				{
					"name": "PLAN-OLD",
					"status": "Pending",
					"work_order": "WO-ONE",
					"planned_batches": 2,
					"weight_per_batch": 10,
					"target_warehouse": "Prepared Materials - SE",
				}
			),
		]

		plans = get_linked_material_plans("FEBG-0001", "Compound", "Prepared Materials - SE")

		self.assertEqual(len(plans), 1)
		self.assertEqual(plans[0].outstanding_batches, 2)

	def test_material_status_distinguishes_stock_and_production(self):
		self.assertEqual(get_material_status(4, 4, 0, 0, []), "Stock Available")
		self.assertEqual(get_material_status(4, 1, 2, 0, []), "Planned")
		self.assertEqual(get_material_status(4, 1, 1, 2, []), "Partially Completed")
		self.assertEqual(get_material_status(4, 0, 0, 4, []), "Partially Completed")

	def test_overall_status_keeps_materials_independent(self):
		self.assertEqual(
			get_overall_material_status(
				[
					{"required_batches": 4, "status": "Stock Available"},
					{"required_batches": 3, "status": "In Progress"},
				]
			),
			"Waiting for Raw Material",
		)
		self.assertEqual(
			get_overall_material_status(
				[
					{"required_batches": 4, "status": "Stock Available"},
					{"required_batches": 3, "status": "Shortage"},
				]
			),
			"Waiting for Raw Material",
		)
		self.assertEqual(
			get_overall_material_status(
				[
					{"required_batches": 4, "status": "Stock Available"},
					{"required_batches": 3, "status": "Stock Available"},
				]
			),
			"Ready to Plan",
		)

	@patch("smart_edge_custom.manufacturing.material_production.ensure_work_order_stock_entry")
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	def test_base_colour_completion_uses_stock_uom_quantity(self, get_doc, ensure_entry):
		get_doc.return_value = frappe._dict(
			{"name": "WO-BASE-1", "qty": 1.5, "produced_qty": 0}
		)
		ensure_entry.return_value = "STE-MANUFACTURE-1"
		entry = frappe._dict({"work_order": "WO-BASE-1", "weight_per_batch": 0.3})

		result = complete_material_production(entry, 3)

		self.assertEqual(result["quantity"], 0.9)
		ensure_entry.assert_called_once_with(entry, "Manufacture", 0.9)

	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_doc")
	@patch("smart_edge_custom.manufacturing.material_production.frappe.get_all")
	def test_material_transfer_retry_reuses_submitted_entry(self, get_all, get_doc):
		get_all.return_value = [
			frappe._dict({"name": "STE-TRANSFER-1", "docstatus": 1, "fg_completed_qty": 259.4})
		]
		stock_entry = MagicMock()
		stock_entry.name = "STE-TRANSFER-1"
		stock_entry.docstatus = 1
		stock_entry.fg_completed_qty = 259.4
		get_doc.return_value = stock_entry
		entry = MagicMock()
		entry.work_order = "WO-COMPOUND-1"

		result = ensure_work_order_stock_entry(entry, "Material Transfer for Manufacture", 259.4)

		self.assertEqual(result, "STE-TRANSFER-1")
		stock_entry.submit.assert_not_called()
		entry.db_set.assert_called_once_with(
			"material_transfer_stock_entry", "STE-TRANSFER-1", update_modified=False
		)
