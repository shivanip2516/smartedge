from datetime import date, datetime, time
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from smart_edge_custom.manufacturing import machine_capacity as capacity
from smart_edge_custom.manufacturing import planning
from smart_edge_custom.smart_edge_custom.doctype.smartedge_workstation_capacity_rate import (
	smartedge_workstation_capacity_rate as capacity_rate_config,
)


def setUpModule():
	global translation_patcher, rate_translation_patcher
	translation_patcher = patch.object(capacity, "_", side_effect=lambda message: message)
	rate_translation_patcher = patch.object(capacity_rate_config, "_", side_effect=lambda message: message)
	translation_patcher.start()
	rate_translation_patcher.start()


def tearDownModule():
	translation_patcher.stop()
	rate_translation_patcher.stop()


def rate(name, value, **selectors):
	row = frappe._dict(
		name=name,
		capacity_rate=value,
		operation=None,
		product_size=None,
		product_item=None,
		process_route=None,
		company=None,
	)
	row.update(selectors)
	return row


class TestCapacityRateConfiguration(TestCase):
	def test_size_rate_does_not_require_operation(self):
		doc = frappe._dict(
			operation=None, product_size="0.45x22", product_item=None,
			process_route=None, capacity_rate=35, capacity_uom="Kg/Hour",
		)
		capacity_rate_config.SmartEdgeWorkstationCapacityRate.validate_matching_scope(doc)

	@patch.object(capacity_rate_config.frappe, "throw", side_effect=frappe.ValidationError)
	def test_zero_rate_is_rejected(self, _throw):
		doc = frappe._dict(capacity_rate=0, capacity_uom="Kg/Hour")
		with self.assertRaises(frappe.ValidationError):
			capacity_rate_config.SmartEdgeWorkstationCapacityRate.validate_rate(doc)

	@patch.object(capacity_rate_config.frappe, "throw", side_effect=frappe.ValidationError)
	def test_unsupported_unit_is_rejected(self, _throw):
		doc = frappe._dict(capacity_rate=35, capacity_uom="Kg/Minute")
		with self.assertRaises(frappe.ValidationError):
			capacity_rate_config.SmartEdgeWorkstationCapacityRate.validate_rate(doc)


class TestMachineCapacityMatching(TestCase):
	def test_phase7_matching_priority(self):
		rows = [
			rate("WS-DEFAULT", 10),
			rate("OP-DEFAULT", 20, operation="Extrusion"),
			rate("PRODUCT", 30, operation="Extrusion", product_item="ITEM-1"),
			rate("SIZE", 40, operation="Extrusion", product_size="0.45x22"),
			rate(
				"ROUTE",
				50,
				operation="Extrusion",
				product_size="0.45x22",
				process_route="ROUTE-1",
			),
		]
		match = capacity.select_capacity_rate(
			rows,
			operation="Extrusion",
			product_size="0.45x22",
			product_item="ITEM-1",
			process_route="ROUTE-1",
		)
		self.assertEqual(match.name, "ROUTE")

	def test_machine_specific_rate_is_not_reused(self):
		rows = [
			rate("EXTRUDER-1-0.45", 35, operation="Extrusion", product_size="0.45x22"),
			rate("EXTRUDER-1-2", 25, operation="Extrusion", product_size="2x22"),
		]
		self.assertEqual(
			capacity.select_capacity_rate(rows, operation="Extrusion", product_size="0.45x22").name,
			"EXTRUDER-1-0.45",
		)
		self.assertEqual(
			capacity.select_capacity_rate(rows, operation="Extrusion", product_size="2x22").name,
			"EXTRUDER-1-2",
		)
		self.assertIsNone(capacity.select_capacity_rate(rows, operation="Extrusion", product_size="3x22"))

	def test_rate_rows_are_isolated_by_workstation_and_unit(self):
		rows = [
			frappe._dict(
				name="EXTRUDER-1", workstation="Extruder-1", operation="Extrusion",
				product_size="0.45x22", capacity_uom="Kg/Hour", capacity_rate=35,
			),
			frappe._dict(
				name="EXTRUDER-2", workstation="Extruder-2", operation="Extrusion",
				product_size="0.45x22", capacity_uom="Kg/Hour", capacity_rate=52,
			),
			frappe._dict(
				name="WRONG-UNIT", workstation="Extruder-1", operation="Extrusion",
				product_size="0.45x22", capacity_uom="Meter/Hour", capacity_rate=500,
			),
		]
		operation = frappe._dict(operation="Extrusion", custom_size="0.45x22")
		self.assertEqual(
			capacity.select_capacity_rate_for_row(rows, operation, "Extruder-1", "Kg/Hour").name,
			"EXTRUDER-1",
		)
		self.assertEqual(
			capacity.select_capacity_rate_for_row(rows, operation, "Extruder-2", "Kg/Hour").name,
			"EXTRUDER-2",
		)

	@patch.object(capacity, "_request_cache", side_effect=[{}, {}])
	@patch.object(capacity, "capacity_table_exists", return_value=True)
	@patch.object(capacity.frappe, "get_all")
	def test_subsequent_request_uses_updated_rate(self, get_all, _table_exists, _cache):
		get_all.side_effect = [
			[rate("RATE", 35, operation="Extrusion")],
			[],
			[rate("RATE", 48, operation="Extrusion")],
			[],
		]
		first = capacity.get_capacity_rate("Extruder-1", "Kg/Hour", operation="Extrusion")
		updated = capacity.get_capacity_rate("Extruder-1", "Kg/Hour", operation="Extrusion")
		self.assertEqual(first.capacity_rate, 35)
		self.assertEqual(updated.capacity_rate, 48)

	def test_company_specific_rate_precedes_shared_fallback(self):
		rows = [
			rate("SHARED", 100, operation="Printing"),
			rate("COMPANY", 120, operation="Printing", company="SmartEdge"),
		]
		match = capacity.select_capacity_rate(rows, operation="Printing", company="SmartEdge")
		self.assertEqual(match.name, "COMPANY")

	def test_size_matching_normalizes_whitespace_and_case(self):
		rows = [frappe._dict(name="SIZE", product_size=" 0.45X22 ", capacity_rate=35, capacity_uom="Kg/Hour")]
		self.assertEqual(capacity.select_capacity_rate(rows, product_size="0.45x22").name, "SIZE")

	def test_product_specific_rate_precedes_size_and_workstation_defaults(self):
		rows = [
			rate("WORKSTATION", 20),
			rate("SIZE", 35, operation="Extrusion", product_size="0.45x22"),
			rate(
				"PRODUCT-SIZE", 42, operation="Extrusion", product_size="0.45x22", product_item="ITEM-1"
			),
		]
		match = capacity.select_capacity_rate(
			rows, operation="Extrusion", product_size="0.45x22", product_item="ITEM-1"
		)
		self.assertEqual(match.name, "PRODUCT-SIZE")

	@patch.object(capacity.frappe, "throw", side_effect=frappe.ValidationError)
	def test_equally_specific_conflict_is_rejected(self, _throw):
		rows = [
			rate("A", 35, operation="Extrusion", product_size="0.45x22"),
			rate("B", 36, operation="Extrusion", product_size="0.45x22"),
		]
		with self.assertRaises(frappe.ValidationError):
			capacity.select_capacity_rate(rows, operation="Extrusion", product_size="0.45x22")


class TestMachineCapacityIntervals(TestCase):
	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(
		capacity,
		"get_workstation_schedule",
		return_value=(
			[(datetime(2026, 11, 4, 9), datetime(2026, 11, 4, 19))],
			True,
			None,
			False,
		),
	)
	def test_time_window_must_fit_inside_workstation_hours(self, _get_schedule, _get_downtime):
		valid = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "09:00:00", 2
		)
		before_open = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "08:00:00", 2
		)
		after_close = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "18:00:00", 2
		)
		duration_warning_only = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "15:42:55", 3
		)
		duration_and_working_hours_failure = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "15:42:55", 4.35
		)
		self.assertEqual(valid["status"], "ok")
		self.assertEqual(before_open["status"], "before_open")
		self.assertEqual(after_close["status"], "after_close")
		self.assertEqual(duration_warning_only["status"], "ok")
		self.assertEqual(duration_and_working_hours_failure["status"], "after_close")
		self.assertEqual(duration_and_working_hours_failure["planned_end"], "20:03:55")

	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(capacity, "get_workstation_schedule")
	def test_time_window_cannot_cross_a_working_hours_break(self, get_schedule, _get_downtime):
		def schedule(_workstation, date_value):
			if date_value == date(2026, 11, 4):
				return (
					[
						(datetime(2026, 11, 4, 9), datetime(2026, 11, 4, 13)),
						(datetime(2026, 11, 4, 14), datetime(2026, 11, 4, 19)),
					],
					True,
					None,
					False,
				)
			return [], True, None, False

		get_schedule.side_effect = schedule
		result = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "12:00:00", 3
		)
		self.assertEqual(result["status"], "outside_working_hours")
		break_start = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "13:30:00", 0.5
		)
		self.assertEqual(break_start["status"], "outside_working_hours")

	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(capacity, "get_workstation_schedule")
	def test_overnight_working_hours_are_date_aware(self, get_schedule, _get_downtime):
		def schedule(_workstation, date_value):
			if date_value == date(2026, 11, 4):
				return (
					[(datetime(2026, 11, 4, 22), datetime(2026, 11, 5, 6))],
					True,
					None,
					False,
				)
			return [], True, None, False

		get_schedule.side_effect = schedule
		result = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "23:00:00", 2
		)
		self.assertEqual(result["status"], "ok")

	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(capacity, "get_workstation_schedule")
	def test_holiday_makes_workstation_unavailable(self, get_schedule, _get_downtime):
		def schedule(_workstation, date_value):
			if date_value == date(2026, 11, 4):
				return [], True, "Workstation holiday.", True
			return [], True, None, False

		get_schedule.side_effect = schedule
		result = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "09:00:00", 2
		)
		self.assertEqual(result["status"], "unavailable")

	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(capacity, "get_workstation_schedule")
	def test_overnight_interval_cannot_cross_into_a_holiday(self, get_schedule, _get_downtime):
		def schedule(_workstation, date_value):
			if date_value == date(2026, 11, 4):
				return (
					[(datetime(2026, 11, 4, 22), datetime(2026, 11, 5, 6))],
					True,
					None,
					False,
				)
			if date_value == date(2026, 11, 5):
				return [], True, "Workstation holiday.", True
			return [], True, None, False

		get_schedule.side_effect = schedule
		result = capacity.validate_workstation_time_window(
			"Extruder-1", date(2026, 11, 4), "23:00:00", 2
		)
		self.assertEqual(result["status"], "unavailable")

	def test_capacity_override_does_not_bypass_working_hours(self):
		entry = MagicMock()
		entry.name = "PLAN-1"
		entry.workstation = "Extruder-1"
		entry.get.side_effect = lambda fieldname: {
			"workstation": entry.workstation,
			"planned_shift": None,
		}.get(fieldname)
		with (
			patch.object(planning.frappe, "get_doc", return_value=entry),
			patch.object(planning, "lock_plan_entry"),
			patch.object(planning, "lock_workstation"),
			patch.object(planning, "validate_plan_entry"),
			patch.object(planning, "validate_workstation_reassignment_allowed"),
			patch.object(planning, "validate_material_plan_released"),
			patch.object(planning, "validate_material_scheduling_context"),
			patch.object(planning, "validate_selected_workstation"),
			patch.object(planning, "get_schedule_capacity_projection", return_value={"status": "overload"}),
			patch.object(
				planning,
				"get_working_hours_projection",
				return_value={
					"working_hours_status": "after_close",
					"working_hours_message": "The proposed operation is outside working hours.",
				},
			),
			patch.object(
				planning,
				"get_workstation_booking_conflict_projection",
				return_value={
					"scheduling_conflict_blocked": 0,
				},
			),
			patch.object(planning, "can_confirm_capacity_overload", return_value=True) as can_override,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "outside working hours"):
				planning.schedule_operation(
					plan_entry="PLAN-1",
					planned_date=date(2026, 11, 4),
					workstation="Extruder-1",
					planned_start="18:00:00",
					duration_hours=2,
					override_capacity=1,
				)
		can_override.assert_not_called()

	@staticmethod
	def _plan_booking(name="PLAN-OLD", start=datetime(2026, 11, 4, 9), end=datetime(2026, 11, 4, 15), status="Planned"):
		return frappe._dict(
			name=name,
			work_order=None,
			operation="Extrusion",
			status=status,
			planned_start_datetime=start,
			planned_end_datetime=end,
		)

	def test_workstation_overlap_rule_allows_adjacent_slots_and_excludes_self(self):
		booking = self._plan_booking()
		with patch.object(
			planning.frappe,
			"get_all",
			side_effect=lambda doctype, **kwargs: [booking]
			if doctype == "SmartEdge Production Plan Entry"
			else [],
		):
			self.assertEqual(
				len(
					planning.get_overlapping_entries(
						"Extruder-1", datetime(2026, 11, 4, 11), datetime(2026, 11, 4, 13)
					)
				),
				1,
			)
			self.assertEqual(
				planning.get_overlapping_entries(
					"Extruder-1", datetime(2026, 11, 4, 15), datetime(2026, 11, 4, 17)
				),
				[],
			)
			self.assertEqual(
				planning.get_overlapping_entries(
					"Extruder-1", datetime(2026, 11, 4, 11), datetime(2026, 11, 4, 13), "PLAN-OLD"
				),
				[],
			)

	def test_workstation_overlap_uses_active_statuses_and_full_datetimes(self):
		booking = self._plan_booking(
			name="PLAN-READY",
			start=datetime(2026, 11, 4, 23, 30),
			end=datetime(2026, 11, 5, 1, 30),
			status="Ready",
		)
		captured = {}

		def get_all(doctype, **kwargs):
			if doctype == "SmartEdge Production Plan Entry":
				captured.update(kwargs)
				return [booking]
			return []

		with patch.object(planning.frappe, "get_all", side_effect=get_all):
			conflicts = planning.get_overlapping_entries(
				"Extruder-1", datetime(2026, 11, 5, 0), datetime(2026, 11, 5, 1)
			)
		self.assertEqual(conflicts[0]["name"], "PLAN-READY")
		self.assertEqual(conflicts[0]["status"], "Ready")
		self.assertEqual(captured["filters"]["status"], ["in", planning.ACTIVE_PLAN_STATUSES])

	def test_workstation_booking_projection_contains_conflict_details(self):
		booking = self._plan_booking()
		with (
			patch.object(
				planning,
				"get_overlapping_entries",
				return_value=[
					{
						"name": booking.name,
						"operation": booking.operation,
						"status": booking.status,
						"start": booking.planned_start_datetime,
						"end": booking.planned_end_datetime,
						"source": "SmartEdge Production Plan Entry",
					}
				],
			),
			patch.object(planning, "get_capacity", return_value=1),
		):
			projection = planning.get_workstation_booking_conflict_projection(
				frappe._dict(name="PLAN-NEW"),
				"Extruder-1",
				date(2026, 11, 4),
				"11:00:00",
				2,
			)
		self.assertEqual(projection["scheduling_conflict"], 1)
		self.assertEqual(projection["scheduling_conflict_blocked"], 1)
		self.assertIn("PLAN-OLD", projection["scheduling_conflict_message"])
		self.assertIn("09:00:00", projection["conflicting_time_interval"])

	def test_single_capacity_overlap_cannot_be_overridden(self):
		conflict = {
			"name": "PLAN-OLD",
			"operation": "Extrusion",
			"status": "Planned",
			"start": datetime(2026, 11, 4, 9),
			"end": datetime(2026, 11, 4, 15),
		}
		with (
			patch.object(planning, "lock_workstation"),
			patch.object(planning, "get_workstation_capacity_hours", return_value=24),
			patch.object(planning, "get_workstation_planned_minutes", return_value=0),
			patch.object(planning, "get_overlapping_entries", return_value=[conflict]),
			patch.object(planning, "get_capacity", return_value=1),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Workstation Scheduling Conflict"):
				planning.validate_capacity(
					"Extruder-1",
					"PLAN-NEW",
					datetime(2026, 11, 4, 11),
					datetime(2026, 11, 4, 13),
					override=True,
				)

	def test_overlapping_blocked_intervals_are_counted_once(self):
		intervals = capacity.merge_intervals(
			[
				(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 10)),
				(datetime(2026, 11, 4, 9), datetime(2026, 11, 4, 12)),
			]
		)
		self.assertEqual(capacity.interval_hours(intervals), 4)

	def test_downtime_is_subtracted_without_double_counting(self):
		schedule = [(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 16))]
		blocked = [
			(datetime(2026, 11, 4, 10), datetime(2026, 11, 4, 12)),
			(datetime(2026, 11, 4, 11), datetime(2026, 11, 4, 13)),
		]
		self.assertEqual(capacity.interval_hours(capacity.subtract_intervals(schedule, blocked)), 5)

	def test_overnight_shift(self):
		intervals, message = capacity.make_shift_intervals(date(2026, 11, 4), time(22), time(6))
		self.assertIsNone(message)
		self.assertEqual(capacity.interval_hours(intervals), 8)
		self.assertEqual(intervals[0][1].date(), date(2026, 11, 5))

	def test_equal_shift_times_are_invalid(self):
		intervals, message = capacity.make_shift_intervals(date(2026, 11, 4), time(8), time(8))
		self.assertEqual(intervals, [])
		self.assertIn("greater than zero", message)

	@patch.object(capacity, "is_holiday", return_value=True)
	@patch.object(capacity.frappe, "get_doc")
	def test_workstation_holiday_has_zero_schedule(self, get_doc, _is_holiday):
		get_doc.return_value = frappe._dict(
			disabled=0,
			holiday_list="FACTORY-HOLIDAYS",
			working_hours=[frappe._dict(enabled=1, start_time=time(8), end_time=time(16))],
		)
		intervals, configured, message, holiday = capacity.get_workstation_schedule(
			"Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(intervals, [])
		self.assertTrue(configured)
		self.assertTrue(holiday)
		self.assertEqual(message, "Workstation holiday.")

	@patch.object(capacity, "_request_cache", return_value={})
	@patch.object(capacity.frappe, "get_doc")
	@patch.object(capacity, "get_workstation_schedule")
	@patch.object(capacity, "get_shift_schedule")
	@patch.object(capacity, "get_downtime_intervals")
	@patch.object(capacity, "get_reservation_intervals")
	def test_daily_and_shift_remaining_capacity(
		self,
		get_reservations,
		get_downtime,
		get_shift,
		get_schedule,
		get_doc,
		_request_cache,
	):
		workday = [(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 16))]
		get_doc.return_value = frappe._dict(holiday_list=None)
		get_schedule.return_value = (workday, True, None, False)
		get_shift.return_value = (workday, True, None, False)
		get_downtime.return_value = [(datetime(2026, 11, 4, 10), datetime(2026, 11, 4, 12))]
		get_reservations.return_value = [
			(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 10), "plan:A"),
			(datetime(2026, 11, 4, 14), datetime(2026, 11, 4, 17), "plan:B"),
		]
		result = capacity.get_capacity_availability("Extruder-1", date(2026, 11, 4), "Morning", 35)
		self.assertEqual(result["scheduled_hours"], 8)
		self.assertEqual(result["downtime_hours"], 2)
		self.assertEqual(result["net_hours"], 6)
		self.assertEqual(result["reserved_hours"], 4)
		self.assertEqual(result["remaining_hours"], 2)
		self.assertEqual(result["production_capacity"], 210)
		self.assertEqual(result["remaining_production_capacity"], 70)
		self.assertEqual(result["shift_remaining_hours"], 2)

	@patch.object(capacity, "_request_cache", return_value={})
	@patch.object(capacity.frappe, "get_doc", return_value=frappe._dict(holiday_list=None))
	@patch.object(capacity, "get_workstation_schedule")
	@patch.object(capacity, "get_shift_schedule", return_value=([], False, "Shift is not configured.", False))
	@patch.object(capacity, "get_downtime_intervals", return_value=[])
	@patch.object(capacity, "get_reservation_intervals")
	def test_overallocation_is_separate_from_zero_remaining(
		self,
		get_reservations,
		_get_downtime,
		_get_shift,
		get_schedule,
		_get_doc,
		_request_cache,
	):
		workday = [(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 16))]
		get_schedule.return_value = (workday, True, None, False)
		get_reservations.return_value = [
			(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 16), "plan:A"),
			(datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 16), "plan:B"),
		]
		result = capacity.get_capacity_availability("Extruder-1", date(2026, 11, 4))
		self.assertEqual(result["remaining_hours"], 0)
		self.assertEqual(result["overallocated_hours"], 8)


class TestOperationCapacityPayload(TestCase):
	def test_printing_and_slitting_use_each_workstations_configured_meter_rate(self):
		rates = [
			frappe._dict(
				name="PRINT-1", workstation="Printer-1", operation="Offline Printing",
				capacity_uom="Meter/Hour", capacity_rate=100,
			),
			frappe._dict(
				name="PRINT-2", workstation="Printer-2", operation="Offline Printing",
				capacity_uom="Meter/Hour", capacity_rate=125,
			),
			frappe._dict(
				name="SLIT-1", workstation="Slitter-1", operation="Slitting",
				capacity_uom="Meter/Hour", capacity_rate=80,
			),
		]
		printing_row = frappe._dict(
			planning_category="Printing", operation="Offline Printing", planned_meter=1000
		)
		slitting_row = frappe._dict(
			planning_category="Slitting", operation="Slitting", planned_meter=800
		)
		self.assertEqual(capacity.get_rate_payload(printing_row, "Printer-1", rates)["required_hours"], 10)
		self.assertEqual(capacity.get_rate_payload(printing_row, "Printer-2", rates)["required_hours"], 8)
		self.assertEqual(capacity.get_rate_payload(slitting_row, "Slitter-1", rates)["required_hours"], 10)

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "get_capacity_rate")
	def test_extruder_uses_planned_kg(self, get_rate, get_availability):
		get_rate.return_value = rate("EXTRUDER-RATE", 35)
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"remaining_hours": 4,
			"shift_configured": 1,
			"shift_net_hours": 8,
			"shift_remaining_hours": 4,
		}
		payload = capacity.get_operation_capacity_payload(
			frappe._dict(
				planning_category="Extrusion",
				operation="Extrusion",
				workstation="Extruder-1",
				planned_kg=350,
				planned_meter=999,
				planned_date=date(2026, 11, 4),
			)
		)
		self.assertEqual(payload["capacity_uom"], "Kg/Hour")
		self.assertEqual(payload["planned_quantity"], 350)
		self.assertEqual(payload["required_hours"], 10)

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "get_capacity_rate")
	def test_extruder_falls_back_to_generator_total_weight(self, get_rate, get_availability):
		get_rate.return_value = rate("EXTRUDER-RATE", 35)
		get_availability.return_value = {"configured": 1, "shift_configured": 1, "net_hours": 8}
		payload = capacity.get_operation_capacity_payload(
			frappe._dict(
				planning_category="Extrusion",
				operation="Single Mould Extrusion",
				workstation="Single Mould Extrusion",
				planned_kg=0,
				total_eb_weight_kg=173.3,
				planned_date=date(2026, 11, 6),
			)
		)
		self.assertEqual(payload["planned_quantity"], 173.3)
		self.assertAlmostEqual(payload["required_hours"], 173.3 / 35, places=6)

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "get_capacity_rate")
	def test_printing_uses_planned_meter(self, get_rate, get_availability):
		get_rate.return_value = rate("PRINT-RATE", 100)
		get_availability.return_value = {"configured": 0, "shift_configured": 0}
		payload = capacity.get_operation_capacity_payload(
			frappe._dict(
				planning_category="Printing",
				operation="Offline Printing",
				workstation="Offline Printing Machine",
				planned_kg=25,
				planned_meter=800,
				planned_date=date(2026, 11, 4),
			)
		)
		self.assertEqual(payload["capacity_uom"], "Meter/Hour")
		self.assertEqual(payload["planned_quantity"], 800)
		self.assertEqual(payload["required_hours"], 8)
		self.assertEqual(capacity.get_expected_capacity("Slitting"), ("Meter/Hour", "planned_meter", "Meter"))

	@patch.object(capacity, "get_capacity_rate", return_value=None)
	def test_missing_rate_has_clear_message(self, _get_rate):
		payload = capacity.get_operation_capacity_payload(
			frappe._dict(
				planning_category="Slitting",
				operation="Slitting",
				workstation="Slitter-1",
				planned_meter=800,
			)
		)
		self.assertFalse(payload["configured"])
		self.assertEqual(
			payload["message"],
			"Capacity not configured for this Workstation and production specification.",
		)

	def test_configured_rate_without_quantity_is_not_available(self):
		rows = [
			frappe._dict(
				name="RATE",
				workstation="Extruder-1",
				operation="Extrusion",
				capacity_uom="Kg/Hour",
				capacity_rate=35,
			)
		]
		payload = capacity.get_rate_payload(
			frappe._dict(
				planning_category="Extrusion",
				operation="Extrusion",
				workstation="Extruder-1",
				planned_kg=None,
			),
			rate_rows=rows,
		)
		self.assertTrue(payload["configured"])
		self.assertIsNone(payload["required_hours"])
		self.assertEqual(payload["planned_hours_status"], "Not Available")

	def test_invalid_rate_does_not_divide(self):
		rows = [
			frappe._dict(
				name="INVALID-RATE",
				workstation="Extruder-1",
				operation="Extrusion",
				capacity_uom="Kg/Hour",
				capacity_rate=0,
			)
		]
		payload = capacity.get_rate_payload(
			frappe._dict(
				planning_category="Extrusion",
				operation="Extrusion",
				workstation="Extruder-1",
				planned_kg=350,
			),
			rate_rows=rows,
		)
		self.assertFalse(payload["configured"])
		self.assertNotIn("required_hours", payload)


class TestBoardCapacityMetrics(TestCase):
	def test_configured_allocation_uses_saved_slot_duration(self):
		row = frappe._dict(
			name="PLAN-A",
			workstation="Extruder-1",
			planning_category="Extrusion",
			operation="Extrusion",
			planned_kg=70,
			planned_date=date(2026, 11, 4),
			planned_start_datetime=datetime(2026, 11, 4, 8),
			planned_end_datetime=datetime(2026, 11, 4, 14),
			planned_shift=None,
		)
		rate_rows = [
			frappe._dict(
				name="RATE",
				workstation="Extruder-1",
				operation="Extrusion",
				capacity_uom="Kg/Hour",
				capacity_rate=35,
			)
		]
		allocation = capacity.prepare_capacity_allocations([row], rate_rows)["PLAN-A"]
		self.assertEqual(allocation.allocation_hours, 6)

	def test_missing_applicable_rate_never_falls_back_to_scheduled_duration(self):
		rows = [
			frappe._dict(
				name="PLAN-A",
				workstation="Extruder-1",
				planning_category="Extrusion",
				operation="Extrusion",
				planned_kg=350,
				planned_date=date(2026, 11, 4),
				planned_start_datetime=datetime(2026, 11, 4, 8),
				planned_end_datetime=datetime(2026, 11, 4, 18),
				planned_shift=None,
			)
		]
		allocation = capacity.prepare_capacity_allocations(rows, [])["PLAN-A"]
		self.assertFalse(allocation.capacity_configured)
		self.assertIsNone(allocation.allocation_hours)

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "get_capacity_allocation_rows")
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	def test_board_preserves_clear_missing_rate_status(
		self, _get_rates, get_allocation_rows, get_availability
	):
		row = frappe._dict(
			name="PLAN-A", plan_type="Final EB", planning_category="Extrusion",
			operation="Extrusion", workstation="Extruder-1", planned_kg=350,
			planned_date=date(2026, 11, 4), planned_shift=None,
		)
		get_allocation_rows.return_value = [row]
		get_availability.return_value = {
			"configured": 1, "net_hours": 8, "reservation_hours_by_reference": {"plan:PLAN-A": 1},
		}
		capacity.attach_board_capacity_details([row])
		self.assertEqual(
			row.machine_capacity["message"],
			"Capacity not configured for this Workstation and production specification.",
		)
		self.assertIsNone(row.machine_capacity["workstation_utilization"])
		self.assertIsNone(row.machine_capacity["available_capacity"])

	@patch.object(capacity, "get_capacity_availability")
	def test_unconfigured_allocation_makes_shared_utilization_unverified(self, get_availability):
		get_availability.return_value = {
			"configured": 1, "net_hours": 8, "reservation_hours_by_reference": {}
		}
		allocations = {
			"PLAN-A": frappe._dict(
				name="PLAN-A", workstation="Extruder-1", planned_date="2026-11-04",
				shift=None, allocation_hours=None, capacity_configured=0,
			)
		}
		metrics = capacity.get_context_capacity_metrics(("Extruder-1", "2026-11-04", None), allocations)
		self.assertFalse(metrics["allocation_configured"])
		self.assertIsNone(metrics["total_allocated_hours"])
		self.assertIsNone(metrics["utilization"])
		self.assertIn("not configured", metrics["calculation_message"])

	def test_superseded_duplicate_planning_row_is_not_counted(self):
		rows = [
			frappe._dict(
				name="OLD",
				creation=datetime(2026, 11, 4, 8),
				final_eb_bom_generator="FEBG-1",
				operation_row="OP-ROW-1",
			),
			frappe._dict(
				name="NEW",
				creation=datetime(2026, 11, 4, 9),
				final_eb_bom_generator="FEBG-1",
				operation_row="OP-ROW-1",
			),
		]
		result = capacity.remove_superseded_allocation_rows(rows)
		self.assertEqual([row.name for row in result], ["NEW"])

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "get_capacity_allocation_rows")
	@patch.object(capacity, "get_capacity_rate_rows")
	def test_shared_utilization_and_own_reservation_exclusion(
		self, get_rate_rows, get_allocation_rows, get_availability
	):
		rows = [
			frappe._dict(
				name="PLAN-A",
				plan_type="Final EB",
				planning_category="Extrusion",
				operation="Extrusion",
				workstation="Extruder-1",
				custom_size="0.45x22",
				planned_kg=70,
				planned_meter=1000,
				planned_date=date(2026, 11, 4),
				planned_shift="Morning",
			),
			frappe._dict(
				name="PLAN-B",
				plan_type="Final EB",
				planning_category="Extrusion",
				operation="Extrusion",
				workstation="Extruder-1",
				custom_size="2x22",
				planned_kg=100,
				planned_meter=1000,
				planned_date=date(2026, 11, 4),
				planned_shift="Morning",
			),
		]
		get_rate_rows.return_value = [
			frappe._dict(
				name="RATE-A",
				workstation="Extruder-1",
				operation="Extrusion",
				product_size="0.45x22",
				capacity_uom="Kg/Hour",
				capacity_rate=35,
			),
			frappe._dict(
				name="RATE-B",
				workstation="Extruder-1",
				operation="Extrusion",
				product_size="2x22",
				capacity_uom="Kg/Hour",
				capacity_rate=50,
			),
		]
		get_allocation_rows.return_value = rows
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"reservation_hours_by_reference": {"plan:PLAN-A": 1, "plan:PLAN-B": 1},
			"shift_configured": 1,
			"shift_net_hours": 8,
			"shift_reservation_hours_by_reference": {"plan:PLAN-A": 1, "plan:PLAN-B": 1},
		}

		capacity.attach_board_capacity_details(rows)

		self.assertEqual(rows[0].machine_capacity["required_hours"], 2)
		self.assertEqual(rows[1].machine_capacity["required_hours"], 2)
		self.assertEqual(rows[0].machine_capacity["total_allocated_hours"], 4)
		self.assertEqual(rows[1].machine_capacity["total_allocated_hours"], 4)
		self.assertEqual(rows[0].machine_capacity["workstation_utilization"], 50)
		self.assertEqual(rows[1].machine_capacity["workstation_utilization"], 50)
		self.assertEqual(rows[0].machine_capacity["other_allocated_hours"], 2)
		self.assertEqual(rows[0].machine_capacity["remaining_available_hours"], 6)
		self.assertEqual(rows[0].machine_capacity["available_capacity"], 210)
		self.assertEqual(rows[1].machine_capacity["available_capacity"], 300)

	@patch.object(capacity, "get_capacity_availability")
	def test_utilization_can_exceed_100_and_zero_hours_is_na(self, get_availability):
		allocations = {
			"A": frappe._dict(
				name="A", workstation="Extruder-1", planned_date="2026-11-04", shift=None, allocation_hours=10
			)
		}
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"reservation_hours_by_reference": {"plan:A": 8},
		}
		metrics = capacity.get_context_capacity_metrics(("Extruder-1", "2026-11-04", None), allocations)
		self.assertEqual(metrics["utilization"], 125)
		self.assertEqual(metrics["overallocated_hours"], 2)

		get_availability.return_value = {
			"configured": 0,
			"net_hours": 0,
			"reservation_hours_by_reference": {},
		}
		metrics = capacity.get_context_capacity_metrics(("Extruder-1", "2026-11-04", None), allocations)
		self.assertIsNone(metrics["utilization"])

	@patch.object(capacity, "get_capacity_availability")
	def test_projection_exclusion_does_not_readd_own_reservation(self, get_availability):
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"reservation_hours_by_reference": {"plan:A": 2, "external:X": 1},
		}
		metrics = capacity.get_context_capacity_metrics(
			("Extruder-1", "2026-11-04", None), {}, excluded_plan_entries={"A"}
		)
		self.assertEqual(metrics["total_allocated_hours"], 1)


class TestScheduleCapacityProjection(TestCase):
	def make_entry(self, **values):
		entry = frappe._dict(
			name="PLAN-A",
			plan_type="Final EB",
			planning_category="Extrusion",
			operation="Extrusion",
			workstation="Extruder-OLD",
			planned_date=date(2026, 11, 3),
			planned_shift=None,
			planned_kg=105,
			final_eb_bom_generator=None,
		)
		entry.update(values)
		return entry

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "prepare_capacity_allocations")
	@patch.object(capacity, "get_capacity_allocation_rows", return_value=[])
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload")
	def test_move_that_causes_overload_has_complete_projection(
		self, get_rate_payload, _get_rates, _get_rows, prepare_allocations, get_availability
	):
		get_rate_payload.return_value = {
			"applicable": 1,
			"configured": 1,
			"capacity_uom": "Kg/Hour",
			"capacity_rate": 35,
			"required_hours": 3,
		}
		prepare_allocations.return_value = {
			"PLAN-B": frappe._dict(
				name="PLAN-B", workstation="Extruder-1", planned_date="2026-11-04", shift=None, allocation_hours=6
			)
		}
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"reservation_hours_by_reference": {"plan:PLAN-B": 6},
		}
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(), "Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(result["status"], "overload")
		self.assertTrue(result["requires_confirmation"])
		self.assertEqual(result["available_hours"], 8)
		self.assertEqual(result["already_planned_hours"], 6)
		self.assertEqual(result["new_operation_hours"], 3)
		self.assertEqual(result["projected_total_hours"], 9)
		self.assertEqual(result["utilization"], 112.5)
		self.assertEqual(result["overload_hours"], 1)

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "prepare_capacity_allocations")
	@patch.object(capacity, "get_capacity_allocation_rows", return_value=[])
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload")
	def test_unconfigured_existing_allocation_prevents_false_fit_result(
		self, get_rate_payload, _get_rates, _get_rows, prepare_allocations, get_availability
	):
		get_rate_payload.return_value = {
			"applicable": 1, "configured": 1, "capacity_rate": 35, "required_hours": 2
		}
		prepare_allocations.return_value = {
			"PLAN-B": frappe._dict(
				name="PLAN-B", workstation="Extruder-1", planned_date="2026-11-04",
				shift=None, allocation_hours=None, capacity_configured=0,
			)
		}
		get_availability.return_value = {
			"configured": 1, "net_hours": 8, "reservation_hours_by_reference": {"plan:PLAN-B": 4}
		}
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(), "Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(result["status"], "schedule_not_configured")
		self.assertIn("not configured", result["configuration_message"])

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "prepare_capacity_allocations", return_value={})
	@patch.object(capacity, "get_capacity_allocation_rows", return_value=[])
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload")
	def test_missing_shift_availability_is_not_reported_as_missing_rate(
		self, get_rate_payload, _get_rates, _get_rows, _prepare_allocations, get_availability
	):
		get_rate_payload.return_value = {
			"applicable": 1, "configured": 1, "capacity_rate": 35, "required_hours": 4.95,
		}
		get_availability.return_value = {
			"configured": 0, "message": "Workstation schedule is not configured.",
			"net_hours": 0, "reservation_hours_by_reference": {},
		}
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(), "Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(result["status"], "availability_not_configured")
		self.assertEqual(result["message"], "Workstation schedule is not configured.")

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "prepare_capacity_allocations")
	@patch.object(capacity, "get_capacity_allocation_rows", return_value=[])
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload")
	def test_same_destination_does_not_warn_for_unchanged_existing_overload(
		self, get_rate_payload, _get_rates, _get_rows, prepare_allocations, get_availability
	):
		get_rate_payload.return_value = {
			"applicable": 1, "configured": 1, "capacity_rate": 35, "required_hours": 3
		}
		prepare_allocations.return_value = {
			"PLAN-A": frappe._dict(
				name="PLAN-A", workstation="Extruder-1", planned_date="2026-11-04", shift=None, allocation_hours=3
			),
			"PLAN-B": frappe._dict(
				name="PLAN-B", workstation="Extruder-1", planned_date="2026-11-04", shift=None, allocation_hours=6
			),
		}
		get_availability.return_value = {
			"configured": 1,
			"net_hours": 8,
			"reservation_hours_by_reference": {"plan:PLAN-A": 3, "plan:PLAN-B": 6},
		}
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(workstation="Extruder-1", planned_date=date(2026, 11, 4)),
			"Extruder-1",
			date(2026, 11, 4),
		)
		self.assertEqual(result["already_planned_hours"], 6)
		self.assertEqual(result["projected_total_hours"], 9)
		self.assertFalse(result["requires_confirmation"])
		self.assertEqual(result["availability_status"], "overloaded")

	@patch.object(capacity, "get_capacity_availability")
	@patch.object(capacity, "prepare_capacity_allocations")
	@patch.object(capacity, "get_capacity_allocation_rows", return_value=[])
	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload")
	def test_zero_available_is_overload_with_na_utilization(
		self, get_rate_payload, _get_rates, _get_rows, prepare_allocations, get_availability
	):
		get_rate_payload.return_value = {
			"applicable": 1, "configured": 1, "capacity_rate": 35, "required_hours": 2
		}
		prepare_allocations.return_value = {}
		get_availability.return_value = {
			"configured": 1, "net_hours": 0, "reservation_hours_by_reference": {}
		}
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(), "Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(result["status"], "overload")
		self.assertEqual(result["availability_status"], "unavailable")
		self.assertIsNone(result["utilization"])
		self.assertEqual(result["overload_hours"], 2)

	@patch.object(capacity, "get_capacity_rate_rows", return_value=[])
	@patch.object(capacity, "get_rate_payload", return_value={"applicable": 1, "configured": 0})
	def test_missing_configuration_uses_required_separate_message(self, _payload, _rates):
		result = capacity.get_schedule_capacity_projection(
			self.make_entry(), "Extruder-1", date(2026, 11, 4)
		)
		self.assertEqual(result["status"], "not_configured")
		self.assertEqual(
			result["message"],
			"Capacity cannot be verified for this Workstation, product size and selected date/shift.",
		)


