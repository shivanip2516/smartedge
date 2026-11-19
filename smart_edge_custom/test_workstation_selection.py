from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import planning


def raise_validation_error(*args, **kwargs):
	raise frappe.ValidationError(args[0] if args else None)


def make_fake_frappe(exists=None, get_value=None, get_all=None, get_list=None, get_doc=None):
	db = SimpleNamespace(
		exists=Mock(side_effect=exists),
		get_value=Mock(side_effect=get_value),
	)
	return SimpleNamespace(
		db=db,
		has_permission=Mock(),
		get_all=Mock(side_effect=get_all),
		get_list=Mock(side_effect=get_list),
		get_doc=Mock(side_effect=get_doc),
		throw=Mock(side_effect=raise_validation_error),
	)


class TestWorkstationOptionResolution(TestCase):
	def test_every_operation_exposes_all_active_workstations(self):
		fake_frappe = make_fake_frappe(
			exists=lambda doctype, name: doctype == "Operation",
			get_value=lambda doctype, name, fieldname, *args: "Mixer-1" if doctype == "Operation" else None,
		)
		with (
			patch.object(planning, "frappe", fake_frappe),
			patch.object(planning, "get_active_workstations", return_value=["Mixer-2", "Mixer-1"]),
		):
			workstations = planning.get_compatible_workstations("Mixing", include_all_active=1)

		self.assertEqual(workstations, ["Mixer-2", "Mixer-1"])

	def test_route_mapping_does_not_restrict_active_workstations(self):
		fake_frappe = make_fake_frappe(
			exists=lambda doctype, name: doctype == "Operation"
			or (doctype == "DocType" and name == "SmartEdge Operation Workstation"),
			get_value=lambda doctype, name, fieldname, *args: "Mixer-1" if doctype == "Operation" else None,
		)
		with (
			patch.object(planning, "frappe", fake_frappe),
			patch.object(planning, "get_active_workstations", return_value=["Extruder-1", "Single Mould Extrusion"]),
		):
			workstations = planning.get_compatible_workstations("Single Mould Extrusion", include_all_active=1)

		self.assertEqual(workstations, ["Extruder-1", "Single Mould Extrusion"])

	def test_operation_row_and_workstation_type_do_not_restrict_results(self):
		fake_frappe = make_fake_frappe(
			exists=lambda doctype, name: doctype == "Operation",
		)
		with (
			patch.object(planning, "frappe", fake_frappe),
			patch.object(planning, "get_active_workstations", return_value=["Mixer-1", "Extruder-1"]),
		):
			workstations = planning.get_compatible_workstations(
				"Single Mould Extrusion", operation_row="FEB-OP-1", include_all_active=1
			)

		self.assertEqual(workstations, ["Mixer-1", "Extruder-1"])

	def test_disabled_or_inaccessible_workstations_are_filtered(self):
		fake_frappe = make_fake_frappe(get_list=lambda doctype, **kwargs: ["Mixer-2"])
		with patch.object(planning, "frappe", fake_frappe):
			self.assertEqual(
				planning.filter_enabled_workstations(["Mixer-1", "Mixer-2"]),
				["Mixer-2"],
			)
		fake_frappe.get_list.assert_called_once()

	def test_server_accepts_any_active_selected_workstation(self):
		class Workstation:
			disabled = 0

			def get(self, fieldname):
				return getattr(self, fieldname, None)

			def check_permission(self, permission):
				return None

		workstation = Workstation()
		fake_frappe = make_fake_frappe(
			exists=lambda doctype, name: True,
			get_doc=lambda doctype, name: workstation,
		)
		with patch.object(planning, "frappe", fake_frappe):
			planning.validate_workstation_compatibility("Mixing", None, "Mixer-2")

	def test_schedule_persists_the_selected_workstation(self):
		class PlanEntry:
			name = "PLAN-1"
			workstation = "Mixer-1"
			operation = "Mixing"
			operation_row = None
			status = "Pending"
			final_eb_bom_generator = "FEBG-1"
			planned_shift = None
			saved = False

			def check_permission(self, permission):
				return None

			def get(self, fieldname, default=None):
				return getattr(self, fieldname, default)

			def reload(self):
				return None

			def save(self):
				self.saved = True

		entry = PlanEntry()
		fake_frappe = SimpleNamespace(get_doc=Mock(return_value=entry))
		with (
			patch("smart_edge_custom.manufacturing.operator_access.require_planning_supervisor"),
			patch.object(planning, "frappe", fake_frappe),
			patch.object(planning, "lock_plan_entry"),
			patch.object(planning, "lock_workstation"),
			patch.object(planning, "validate_plan_entry"),
			patch.object(planning, "validate_workstation_reassignment_allowed"),
			patch.object(planning, "validate_material_plan_released"),
			patch.object(planning, "validate_material_scheduling_context"),
			patch.object(planning, "validate_selected_workstation"),
			patch.object(planning, "get_schedule_capacity_projection", return_value={"status": "ok"}),
			patch.object(planning, "get_working_hours_projection", return_value={"working_hours_status": "ok"}),
			patch.object(planning, "get_workstation_booking_conflict_projection", return_value={}),
			patch.object(planning, "validate_future_schedule"),
			patch.object(planning, "validate_sequence"),
			patch.object(planning, "validate_capacity", return_value=None),
			patch.object(planning, "update_unstarted_production_jobs_for_schedule"),
			patch.object(planning, "update_generator_planning_status"),
		):
			planning.schedule_operation(
				plan_entry="PLAN-1",
				planned_date="2026-11-20",
				workstation="Mixer-2",
				planned_start="09:00",
				duration_hours=2,
			)

		self.assertTrue(entry.saved)
		self.assertEqual(entry.workstation, "Mixer-2")
