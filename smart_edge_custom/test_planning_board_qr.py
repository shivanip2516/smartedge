import unittest
from unittest.mock import patch

from smart_edge_custom.manufacturing import planning


class TestPlanningBoardQRFlag(unittest.TestCase):
	def test_qr_verification_is_disabled_by_default(self):
		with patch.object(planning.frappe, "conf", {}):
			self.assertFalse(planning.planning_board_qr_verification_enabled())

	def test_qr_verification_can_be_enabled_without_changing_other_workflows(self):
		with patch.object(planning.frappe, "conf", {"enable_planning_board_qr_verification": 1}):
			self.assertTrue(planning.planning_board_qr_verification_enabled())


if __name__ == "__main__":
	unittest.main()
