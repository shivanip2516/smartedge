from datetime import date
from unittest import TestCase

from smart_edge_custom.manufacturing import planning


class TestCapacityConfirmationScope(TestCase):
	def test_confirmation_fingerprint_is_scoped_to_exact_move(self):
		original = planning.make_capacity_move_fingerprint(
			"PLAN-A", "Extruder-1", date(2026, 11, 4), "09:00", 2
		)
		changed_workstation = planning.make_capacity_move_fingerprint(
			"PLAN-A", "Extruder-2", date(2026, 11, 4), "09:00", 2
		)
		changed_start = planning.make_capacity_move_fingerprint(
			"PLAN-A", "Extruder-1", date(2026, 11, 4), "10:00", 2
		)
		self.assertNotEqual(original, changed_workstation)
		self.assertNotEqual(original, changed_start)

	def test_material_capacity_change_invalidates_projection_signature(self):
		projection = {
			"status": "overload",
			"workstation": "Extruder-1",
			"planned_date": "2026-11-04",
			"shift": None,
			"available_hours": 8,
			"already_planned_hours": 6,
			"new_operation_hours": 3,
			"projected_total_hours": 9,
			"overload_hours": 1,
		}
		original = planning.get_capacity_projection_signature(projection)
		projection["already_planned_hours"] = 7
		projection["projected_total_hours"] = 10
		projection["overload_hours"] = 2
		self.assertNotEqual(original, planning.get_capacity_projection_signature(projection))
