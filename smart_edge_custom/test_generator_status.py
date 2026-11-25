import json
import unittest
from pathlib import Path

from smart_edge_custom.manufacturing.final_bom_generator import (
	GENERATOR_VISIBLE_STATUSES,
	LEGACY_TO_VISIBLE_STATUS,
)


class TestGeneratorVisibleStatusContract(unittest.TestCase):
	def test_visible_statuses_are_exactly_the_supported_lifecycle(self):
		path = Path(__file__).parent / "smart_edge_custom" / "doctype" / "final_eb_bom_generator" / "final_eb_bom_generator.json"
		definition = json.loads(path.read_text())
		field = next(row for row in definition["fields"] if row["fieldname"] == "status")
		self.assertEqual(field["options"].splitlines(), list(GENERATOR_VISIBLE_STATUSES))
		self.assertEqual(field["default"], "Material Planning Pending")

	def test_legacy_material_labels_map_to_visible_lifecycle_labels(self):
		self.assertEqual(LEGACY_TO_VISIBLE_STATUS["Material Production In Progress"], "Material Production Pending")
		self.assertEqual(LEGACY_TO_VISIBLE_STATUS["Production Completed"], "Completed")
		self.assertEqual(LEGACY_TO_VISIBLE_STATUS["Planned"], "Fully Planned")


if __name__ == "__main__":
	unittest.main()
