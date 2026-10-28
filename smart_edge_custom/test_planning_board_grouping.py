import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.planning import (
	assign_planning_execution_keys,
	get_planning_execution_key,
	get_released_productions,
	is_unreleased_material_entry,
)


class TestPlanningBoardGrouping(IntegrationTestCase):
	def test_sequence_reset_creates_separate_final_eb_execution_groups(self):
		rows = [
			self.make_row("PLAN-1", "2026-10-01 09:00:00", 1),
			self.make_row("PLAN-2", "2026-10-01 09:00:01", 2),
			self.make_row("PLAN-3", "2026-10-02 09:00:00", 1),
			self.make_row("PLAN-4", "2026-10-02 09:00:01", 2),
		]
		assign_planning_execution_keys(rows)
		self.assertEqual(rows[0].execution_key, rows[1].execution_key)
		self.assertEqual(rows[2].execution_key, rows[3].execution_key)
		self.assertNotEqual(rows[0].execution_key, rows[2].execution_key)

	def test_material_executions_are_separated_by_work_order(self):
		rows = [
			frappe._dict(
				{
					"name": "PLAN-MAT-1",
					"plan_type": "Required Material",
					"work_order": "WO-1",
				}
			),
			frappe._dict(
				{
					"name": "PLAN-MAT-2",
					"plan_type": "Required Material",
					"work_order": "WO-2",
				}
			),
		]
		assign_planning_execution_keys(rows)
		self.assertNotEqual(rows[0].execution_key, rows[1].execution_key)
		self.assertEqual(get_planning_execution_key(rows[0]), ("Required Material", "WO-1"))
		self.assertEqual(get_planning_execution_key(rows[1]), ("Required Material", "WO-2"))

	def test_released_materials_join_finished_product_execution_in_required_order(self):
		rows = [
			self.make_material_row("PLAN-SHADE", "Base Colour", "WO-SHADE"),
			self.make_row("PLAN-FINAL-1", "2026-10-01 09:00:02", 1),
			self.make_material_row("PLAN-COMPOUND", "Compound", "WO-COMPOUND"),
			self.make_row("PLAN-FINAL-2", "2026-10-01 09:00:03", 2),
		]

		assign_planning_execution_keys(rows)

		self.assertEqual(len({row.execution_key for row in rows}), 1)
		self.assertEqual(get_planning_execution_key(rows[0])[0], "Final EB")
		sequences = {row.name: row.board_sequence for row in rows}
		self.assertEqual(sequences["PLAN-COMPOUND"], 1)
		self.assertEqual(sequences["PLAN-SHADE"], 2)
		self.assertEqual(sequences["PLAN-FINAL-1"], 3)
		self.assertEqual(sequences["PLAN-FINAL-2"], 4)

	def test_linked_material_is_hidden_until_finished_product_release(self):
		material = self.make_material_row("PLAN-COMPOUND", "Compound", "WO-COMPOUND")
		released = get_released_productions([material])

		self.assertTrue(is_unreleased_material_entry(material, released))

		final = self.make_row("PLAN-FINAL-1", "2026-10-01 09:00:02", 1)
		released = get_released_productions([material, final])
		self.assertFalse(is_unreleased_material_entry(material, released))

	def make_row(self, name, creation, sequence):
		return frappe._dict(
			{
				"name": name,
				"creation": creation,
				"sequence": sequence,
				"plan_type": "Final EB",
				"final_eb_bom_generator": "FEBG-1",
			}
		)

	def make_material_row(self, name, material_type, work_order):
		return frappe._dict(
			{
				"name": name,
				"creation": "2026-10-01 09:00:00",
				"sequence": 0,
				"plan_type": "Required Material",
				"material_type": material_type,
				"work_order": work_order,
				"final_eb_bom_generator": "FEBG-1",
			}
		)
