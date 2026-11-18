"""Quantity rules shared by SmartEdge manufacturing transaction boundaries."""

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP


WHOLE_METER = Decimal("1")


def _decimal(value):
	if value in (None, ""):
		return Decimal("0")
	try:
		return Decimal(str(value))
	except (InvalidOperation, TypeError, ValueError):
		return Decimal("0")


def round_meter_quantity(value):
	"""Round a recorded meter transaction half-up to a whole meter."""
	return float(_decimal(value).quantize(WHOLE_METER, rounding=ROUND_HALF_UP))


def whole_meter_not_exceeding(value):
	"""Return the greatest nonnegative whole meter within an exact availability."""
	quantity = max(_decimal(value), Decimal("0"))
	return float(quantity.quantize(WHOLE_METER, rounding=ROUND_FLOOR))
