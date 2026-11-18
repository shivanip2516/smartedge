import unittest

from smart_edge_custom.manufacturing.quantity import round_meter_quantity, whole_meter_not_exceeding


class TestMeterQuantity(unittest.TestCase):
	def test_half_up_rounding(self):
		self.assertEqual(round_meter_quantity("3289.898990"), 3290)
		self.assertEqual(round_meter_quantity("2.5"), 3)
		self.assertEqual(round_meter_quantity("9856.060606"), 9856)

	def test_availability_never_rounds_above_exact_quantity(self):
		self.assertEqual(whole_meter_not_exceeding("3289.898990"), 3289)
		self.assertEqual(whole_meter_not_exceeding("3290.000000"), 3290)


if __name__ == "__main__":
	unittest.main()
