"""Three-operation BOM/lot checks use rollback-only records and never post inventory."""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import now_datetime

from smart_edge_custom import test_bom_item_groups as bom_tests
from smart_edge_custom import test_generator_item_links as generator_tests
from smart_edge_custom.manufacturing.final_bom_generator import (
	create_planning_entries, get_route_operations, preview_generator,
)
from smart_edge_custom.manufacturing.planning import assign_planning_execution_keys
from smart_edge_custom.manufacturing.production_lot import (
	get_production_lot_genealogy, get_operation_lot_mode, record_completed_job,
)


class TestFinishedOperationBatches(unittest.TestCase):
	make_bom = bom_tests.TestBOMItemGroups.make_bom
	make_finished_bom = generator_tests.TestGeneratorItemLinks.make_finished_bom
	source = generator_tests.TestGeneratorItemLinks.source
	generator = generator_tests.TestGeneratorItemLinks.generator

	def setUp(self):
		generator_tests.TestGeneratorItemLinks.setUp(self)
		self.stack = __import__("contextlib").ExitStack()
		self.addCleanup(self.stack.close)
		# Exercise non-stock completion without invoking live final stock posting.
		self.stack.enter_context(patch("smart_edge_custom.manufacturing.planning.update_generator_planning_status"))
		self.stack.enter_context(patch("smart_edge_custom.manufacturing.planning.validate_final_completion_stock_entry_setup"))
		self.workstation = frappe.db.get_value("Workstation", {"disabled": 0}, "name")
		self.employee = frappe.db.get_value("Employee", {"company": self.company, "status": "Active"}, "name")
		if not self.employee:
			self.skipTest("An active Employee is required for operation completion fixtures")
		item = frappe.get_doc("Item", self.items["Finished Goods"])
		item.has_batch_no = item.create_new_batch = 1
		item.save()
		# Keep batch lifecycle fixtures independent of the site's configured routes.
		route = frappe.get_doc("SmartEdge Process Route", "Solid EB - Single Mould")
		route.set("operations", [{"operation": row.operation, "sequence": row.sequence_id,
			"workstation": row.workstation, "mandatory": 1}
			for row in frappe.get_doc("BOM", self.finished_bom).operations])
		self.stack.enter_context(patch("smart_edge_custom.manufacturing.final_bom_generator.resolve_process_route", return_value=(route, None)))
		self.doc = self.generator()
		self.doc.finished_bom = self.finished_bom
		self.doc.save()
		self.entries = create_planning_entries(self.doc)

	def complete(self, index, actual, good=None, **quality):
		entry = frappe.get_doc("SmartEdge Production Plan Entry", self.entries[index])
		# Deliberately differs from Generator and predecessor release quantities.
		entry.production_quantity = 500
		entry.save()
		job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": entry.name,
			"final_eb_bom_generator": self.doc.name, "company": self.company,
			"operation": entry.operation, "workstation": self.workstation, "operator_employee": self.employee,
			"status": "In Progress", "actual_start": now_datetime(),
			"planned_qty_meter": 500, "actual_qty_meter": actual, "good_qty": actual if good is None else good, **quality,
		}).insert()
		result = job.complete_job()
		return job, frappe.get_doc("SmartEdge Production Lot", result["production_lot"])

	def test_exactly_three_bom_operations_and_separate_material_processes(self):
		self.assertEqual(self.doc.finished_bom, self.finished_bom)
		self.assertEqual([(row.operation, row.sequence) for row in self.doc.operation_preview], [("Extrusion", 1), ("Printing", 2), ("Packing", 3)])
		self.assertEqual(len(self.entries), 3)
		self.assertEqual(create_planning_entries(self.doc), self.entries)
		rows = frappe.get_all("SmartEdge Production Plan Entry", filters={"name": ["in", self.entries]}, fields=["name", "plan_type", "sequence", "creation", "final_eb_bom_generator", "bom_no"])
		rows += [frappe._dict(name="MAT-1", plan_type="Required Material", work_order="WO-1", final_eb_bom_generator=self.doc.name), frappe._dict(name="MAT-2", plan_type="Required Material", work_order="WO-2", final_eb_bom_generator=self.doc.name)]
		assign_planning_execution_keys(rows)
		self.assertEqual(len({row.execution_key for row in rows}), 1)
		self.assertEqual([row.sequence for row in rows[:3]], [1, 2, 3])
		self.assertTrue(all(row.bom_no == self.finished_bom for row in rows[:3]))

	def test_three_completions_are_idempotent_and_preserve_quantities_without_stock(self):
		counts = {dt: frappe.db.count(dt) for dt in ("Batch", "Stock Entry", "Work Order")}
		lots = []
		for index, actual in enumerate((490, 480, 470)):
			job, lot = self.complete(index, actual)
			lots.append(lot.name)
			self.assertEqual(lot.finished_item, self.doc.finished_item)
			self.assertEqual(lot.finished_item_name, frappe.db.get_value("Item", self.doc.finished_item, "item_name"))
			self.assertEqual(lot.planned_quantity, 500)
			self.assertEqual(lot.actual_output_quantity, actual)
			self.assertEqual(lot.operation_sequence, index + 1)
			self.assertIn(job.name, lot.name)
			self.assertTrue(lot.name.endswith(f"-{index + 1:02d}"))
			self.assertFalse(lot.is_stock_inventory_batch)
			self.assertEqual(lot.input_production_lot, lots[index - 1] if index else None)
			ledger_count = frappe.db.count("SmartEdge WIP Ledger Entry")
			self.assertEqual(job.complete_job()["production_lot"], lot.name)
			self.assertEqual(record_completed_job(job)["production_lot"], lot.name)
			self.assertEqual(frappe.db.count("SmartEdge WIP Ledger Entry"), ledger_count)
		self.assertEqual(frappe.db.count("SmartEdge Production Lot", {"final_eb_bom_generator": self.doc.name}), 3)
		self.assertEqual({dt: qty + (2 if dt == "Batch" else 0) for dt, qty in counts.items()}, {dt: frappe.db.count(dt) for dt in counts})
		forward = get_production_lot_genealogy(lots[0], "Forward")
		backward = get_production_lot_genealogy(lots[-1], "Backward")
		self.assertEqual(self.chain(forward["roots"][0]), lots)
		self.assertEqual(self.chain(backward["roots"][0]), list(reversed(lots)))

	def test_actual_output_and_quality_are_not_replaced_by_planned_quantity(self):
		job, lot = self.complete(0, 490, good=480, usable_scrap=2, unusable_scrap=8)
		self.assertEqual(lot.planned_quantity, 500)
		self.assertEqual(lot.actual_output_quantity, 490)
		self.assertEqual(lot.good_quantity, 480)
		self.assertEqual(lot.produced_quantity, 480)
		self.assertEqual(lot.usable_scrap, 2)
		self.assertEqual(lot.unusable_scrap, 8)

	def test_invalid_or_incomplete_jobs_create_no_operation_lot(self):
		entry = frappe.get_doc("SmartEdge Production Plan Entry", self.entries[0])
		for status in ("Pending", "Planned", "In Progress", "Paused"):
			with self.assertRaisesRegex(frappe.ValidationError, "requires a completed"):
				record_completed_job(frappe._dict(name="UNSAVED", status=status))
		job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": entry.name,
			"final_eb_bom_generator": self.doc.name, "operation": entry.operation, "status": "In Progress",
			"workstation": self.workstation, "operator_employee": self.employee, "actual_start": now_datetime(),
		}).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Actual Completed Qty"):
			job.complete_job()
		self.assertFalse(frappe.db.exists("SmartEdge Production Lot", {"production_job": job.name}))

	def test_bom_qc_is_an_operation_batch_but_material_mixing_is_not(self):
		self.assertEqual(get_operation_lot_mode(frappe._dict(plan_type="Final EB", bom_no=self.finished_bom, operation="QC", planning_category="QC")), "New Production Lot")
		self.assertEqual(get_operation_lot_mode(frappe._dict(plan_type="Required Material", bom_no=self.boms["Compounds"], operation="Mixing")), "Stock Batch")

	def configure_route_qc(self):
		self.doc.operation_preview[1].operation = "QC After Extrusion"
		self.doc.operation_preview[1].planning_category = "QC"
		self.doc.save()
		for name in self.entries:
			entry = frappe.get_doc("SmartEdge Production Plan Entry", name)
			entry.bom_no = None
			if entry.sequence == 2:
				entry.operation = "QC After Extrusion"
				entry.planning_category = "QC"
			entry.save()

	def test_route_qc_without_bom_has_distinct_id_and_preserves_the_chain(self):
		from smart_edge_custom.manufacturing.production_lot import record_qc_hold, attach_lot_details
		self.configure_route_qc()
		counts = {dt: frappe.db.count(dt) for dt in ("Item", "Batch", "Stock Entry", "Work Order")}
		_, first = self.complete(0, 490)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", self.entries[1])
		job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": entry.name,
			"final_eb_bom_generator": self.doc.name, "company": self.company,
			"operation": entry.operation, "workstation": self.workstation, "operator_employee": self.employee,
			"status": "In Progress", "actual_start": now_datetime(), "actual_qty_meter": 480, "good_qty": 480,
		}).insert()
		record_qc_hold(job, entry)
		self.assertFalse(job.output_production_lot)
		active_rows = [frappe._dict(production_job=job.name)]
		attach_lot_details(active_rows)
		self.assertFalse(active_rows[0].production_lot)
		self.assertEqual(active_rows[0].previous_operation_lot, first.name)
		qc = frappe.get_doc("SmartEdge Production Lot", job.complete_job()["production_lot"])
		self.assertNotEqual(qc.name, first.name)
		self.assertEqual(qc.input_production_lot, first.name)
		self.assertEqual(qc.operation_sequence, 2)
		self.assertEqual(job.complete_job()["production_lot"], qc.name)
		_, final = self.complete(2, 470)
		self.assertEqual(final.input_production_lot, qc.name)
		self.assertEqual(self.chain(get_production_lot_genealogy(final.name, "Backward")["roots"][0]), [final.name, qc.name, first.name])
		rows = [frappe.get_doc("SmartEdge Production Plan Entry", name).as_dict() for name in self.entries]
		attach_lot_details(rows)
		self.assertEqual(rows[1].production_lot, qc.name)
		self.assertEqual(rows[1].operation_trace_id, qc.name)
		self.assertEqual(rows[1].previous_operation_lot, first.name)
		self.assertEqual(rows[1].lot_actual_output_quantity, 480)
		self.assertEqual({dt: qty + (2 if dt == "Batch" else 0) for dt, qty in counts.items()}, {dt: frappe.db.count(dt) for dt in counts})

	def make_historical_qc(self):
		self.configure_route_qc()
		_, first = self.complete(0, 490)
		# Reproduce a historical completion under the former QC reuse policy.
		with patch("smart_edge_custom.manufacturing.production_lot.get_operation_lot_mode", return_value="Preserve Incoming Lot"):
			job, old_output = self.complete(1, 480)
		self.assertEqual(old_output.name, first.name)
		return job, first

	def test_historical_qc_reuse_is_not_renamed_or_duplicated_on_retry(self):
		job, first = self.make_historical_qc()
		counts = {dt: frappe.db.count(dt) for dt in ("SmartEdge Production Lot", "SmartEdge WIP Ledger Entry", "Batch", "Stock Entry")}
		self.assertTrue(job.complete_job()["backfill_required"])
		self.assertEqual(record_completed_job(job)["production_lot"], first.name)
		self.assertEqual(counts, {dt: frappe.db.count(dt) for dt in counts})
		from smart_edge_custom.manufacturing.production_lot import attach_lot_details
		rows = [frappe.get_doc("SmartEdge Production Plan Entry", name).as_dict() for name in self.entries[:2]]
		attach_lot_details(rows)
		self.assertEqual(rows[0].production_lot, first.name)
		self.assertFalse(rows[1].production_lot)
		self.assertFalse(rows[1].operation_trace_id)
		self.assertTrue(rows[1].operation_lot_missing)

	def test_approved_historical_backfill_adds_only_one_record_and_preserves_execution(self):
		from smart_edge_custom.manufacturing.production_lot import backfill_completed_job, attach_lot_details
		job, first = self.make_historical_qc()
		_, final = self.complete(2, 470)
		before = {dt: frappe.db.count(dt) for dt in ("Item", "Batch", "Stock Entry", "SmartEdge WIP Ledger Entry", "SmartEdge Production Lot")}
		before_job = frappe.get_doc("SmartEdge Production Job", job.name).as_dict()
		preview = backfill_completed_job(job.name, dry_run=True)
		self.assertEqual(preview["action"], "Create historical QC trace record")
		self.assertEqual(before, {dt: frappe.db.count(dt) for dt in before})
		result = backfill_completed_job(job.name)
		qc = frappe.get_doc("SmartEdge Production Lot", result["production_lot"])
		self.assertEqual(qc.name, "QC-" + job.name + "-02")
		self.assertEqual(qc.input_production_lot, first.name)
		self.assertEqual(qc.actual_end, job.actual_end)
		self.assertEqual(qc.actual_output_quantity, job.actual_qty_meter)
		self.assertEqual(before_job, frappe.get_doc("SmartEdge Production Job", job.name).as_dict())
		self.assertEqual(backfill_completed_job(job.name)["action"], "Reuse")
		self.assertEqual(job.complete_job()["production_lot"], qc.name)
		for dt, count in before.items():
			self.assertEqual(frappe.db.count(dt), count + (1 if dt == "SmartEdge Production Lot" else 0))
		rows = [frappe.get_doc("SmartEdge Production Plan Entry", name).as_dict() for name in self.entries]
		attach_lot_details(rows)
		self.assertEqual([r.production_lot for r in rows], [first.name, qc.name, final.name])
		self.assertEqual(self.chain(get_production_lot_genealogy(final.name, "Backward")["roots"][0]), [final.name, qc.name, first.name])
		self.assertEqual(self.chain(get_production_lot_genealogy(first.name, "Forward")["roots"][0]), [first.name, qc.name, final.name])

	def test_backfill_before_downstream_completion_does_not_change_wip_consumption(self):
		from smart_edge_custom.manufacturing.production_lot import backfill_completed_job
		job, first = self.make_historical_qc()
		qc = backfill_completed_job(job.name)["production_lot"]
		_, final = self.complete(2, 470)
		self.assertEqual(final.input_production_lot, first.name)
		self.assertEqual(self.chain(get_production_lot_genealogy(final.name, "Backward")["roots"][0]), [final.name, qc, first.name])

	def test_backfill_rejects_missing_actual_completion_information(self):
		from smart_edge_custom.manufacturing.production_lot import backfill_completed_job
		job, _ = self.make_historical_qc()
		frappe.db.set_value("SmartEdge Production Job", job.name, "actual_end", None)
		with self.assertRaisesRegex(frappe.ValidationError, "Actual Completion"):
			backfill_completed_job(job.name, dry_run=True)

	def test_no_bom_operations_are_invented(self):
		operations, warnings = get_route_operations(frappe._dict(finished_item=self.items["Additives"]), None)
		self.assertEqual(operations, [])
		self.assertEqual(warnings, [])

	def test_released_printing_snapshot_accepts_document_and_api_rows(self):
		self.doc.reload()
		expected = [row.as_dict() for row in self.doc.operation_preview]
		for source in (self.doc, frappe._dict(self.doc.as_dict())):
			with self.subTest(source=type(source).__name__):
				operations, warnings = get_route_operations(source, None)
				self.assertEqual(operations, expected)
				self.assertEqual(warnings, [])
				operations[1]["operation"] = "Changed response only"
				self.assertEqual(self.doc.operation_preview[1].operation, "Printing")
		self.assertEqual(create_planning_entries(self.doc), self.entries)

	def test_printing_selection_preview_and_save_preserve_released_operations(self):
		expected = [(r.name, r.operation, r.sequence) for r in self.doc.operation_preview]
		counts = {dt: frappe.db.count(dt) for dt in (
			"SmartEdge Production Plan Entry", "SmartEdge Production Lot", "Batch", "Stock Entry",
		)}
		for route in ("Online", "Offline"):
			self.doc.printing_route = route
			preview = preview_generator(self.doc.as_dict())
			self.assertEqual([(r["name"], r["operation"], r["sequence"]) for r in preview["operations"]], expected)
			self.doc.save()
			self.doc.reload()
			self.assertEqual(self.doc.printing_route, route)
			self.assertEqual([(r.name, r.operation, r.sequence) for r in self.doc.operation_preview], expected)
		self.assertEqual(counts, {dt: frappe.db.count(dt) for dt in counts})

	def test_printing_start_pause_resume_complete_creates_one_nonstock_batch(self):
		_, previous = self.complete(0, 490)
		entry = frappe.get_doc("SmartEdge Production Plan Entry", self.entries[1])
		entry.production_quantity = 500
		entry.save()
		job = frappe.get_doc({"doctype": "SmartEdge Production Job", "plan_entry": entry.name,
			"final_eb_bom_generator": self.doc.name, "company": self.company,
			"operation": "Printing", "workstation": self.workstation, "operator_employee": self.employee,
			"status": "Draft", "planned_qty_meter": 500,
		}).insert()
		reason = frappe.get_doc({"doctype": "SmartEdge Downtime Reason",
			"reason_name": "Printing test " + frappe.generate_hash(length=10)}).insert()
		counts = {dt: frappe.db.count(dt) for dt in ("Batch", "Stock Entry", "Work Order")}
		self.assertEqual(job.start_job()["status"], "In Progress")
		self.assertEqual(job.pause_job(reason.name)["status"], "Paused")
		self.assertEqual(job.resume_job()["status"], "In Progress")
		job.actual_qty_meter = job.good_qty = 480
		result = job.complete_job()
		self.assertEqual(result["status"], "Completed")
		lot = frappe.get_doc("SmartEdge Production Lot", result["production_lot"])
		self.assertEqual((lot.finished_item, lot.operation, lot.input_production_lot),
			(self.doc.finished_item, "Printing", previous.name))
		self.assertEqual((lot.planned_quantity, lot.actual_output_quantity), (500, 480))
		self.assertEqual(job.complete_job()["production_lot"], lot.name)
		self.assertEqual(frappe.db.count("SmartEdge Production Lot", {"production_job": job.name}), 1)
		self.assertEqual({dt: qty + (1 if dt == "Batch" else 0) for dt, qty in counts.items()}, {dt: frappe.db.count(dt) for dt in counts})

	@staticmethod
	def chain(node):
		result = [node["production_lot"]]
		while node.get("children"):
			node = node["children"][0]
			result.append(node["production_lot"])
		return result


class TestOperationBatchStockLinks(unittest.TestCase):
	@patch("smart_edge_custom.manufacturing.production_lot.record_movement")
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.get_doc")
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all")
	def test_final_batch_link_uses_only_actual_output_evidence(self, get_all, get_doc, movement):
		from smart_edge_custom.manufacturing.production_lot import link_final_stock_batches

		get_all.return_value = [frappe._dict(batch_no="FG-ACTUAL", quantity=470, uom="Meter")]
		job = frappe._dict(name="JOB-PACK", plan_entry="PLAN-PACK")
		get_doc.side_effect = lambda doctype, name: job if doctype == "SmartEdge Production Job" else frappe._dict(name=name)
		link_final_stock_batches([frappe._dict(name="PCK-LOT", production_job=job.name, operation="Packing")], "STE-ACTUAL", frappe._dict(finished_item="FG-ITEM"))
		filters = get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["stock_entry"], "STE-ACTUAL")
		self.assertEqual(filters["direction"], "Output")
		self.assertEqual(filters["source_status"], "Active")
		self.assertEqual(movement.call_args.kwargs["source_lot"], "PCK-LOT")
		self.assertEqual(movement.call_args.kwargs["destination_batch"], "FG-ACTUAL")
		self.assertEqual(movement.call_args.kwargs["quantity"], 470)
		self.assertEqual(movement.call_args.kwargs["reference_name"], "STE-ACTUAL")

	@patch("smart_edge_custom.manufacturing.production_lot.record_movement")
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.get_doc")
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.db.exists", return_value=True)
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.get_all")
	def test_existing_compound_and_colour_batches_link_to_first_operation(self, get_all, _exists, get_doc, movement):
		from smart_edge_custom.manufacturing.production_lot import link_actual_batches_to_first_lot

		get_all.return_value = [frappe._dict(batch_no="CMP-EXISTING", quantity=173, uom="Kg"), frappe._dict(batch_no="BC-EXISTING", quantity=300, uom="Gram")]
		get_doc.side_effect = lambda doctype, name: frappe._dict(name=name, plan_entry="PLAN-EXT")
		lots = [frappe._dict(name="EXT-LOT", operation_sequence=1, operation="Extrusion", production_job="JOB-EXT"), frappe._dict(name="PCK-LOT", operation_sequence=3)]
		self.assertEqual(link_actual_batches_to_first_lot("GEN", lots, "STE-ACTUAL", frappe._dict()), 2)
		self.assertEqual({call.kwargs["source_batch"] for call in movement.call_args_list}, {"CMP-EXISTING", "BC-EXISTING"})
		self.assertTrue(all(call.kwargs["destination_lot"] == "EXT-LOT" for call in movement.call_args_list))

	def test_physical_batch_trace_attaches_correct_operation_endpoint_in_both_directions(self):
		from contextlib import ExitStack
		from smart_edge_custom.manufacturing.batch_genealogy import get_genealogy

		for direction, stock_field, lot_field, lot in (("Backward", "destination_stock_batch", "source_production_lot", "PCK-LOT"), ("Forward", "source_stock_batch", "destination_production_lot", "EXT-LOT")):
			with self.subTest(direction=direction), ExitStack() as stack:
				batch = MagicMock(item="ITEM")
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.frappe.get_doc", return_value=batch))
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.build_genealogy_graph", return_value=({}, {("BATCH", "ITEM")}, False)))
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.get_batch_details", return_value={}))
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.build_genealogy_tree", return_value={}))
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.frappe.db.exists", return_value=True))
				stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.safe_link", return_value=True))
				get_all = stack.enter_context(patch("smart_edge_custom.manufacturing.batch_genealogy.frappe.get_all", return_value=[frappe._dict({stock_field: "BATCH", lot_field: lot})]))
				build = stack.enter_context(patch("smart_edge_custom.manufacturing.production_lot.build_lot_genealogy_node", return_value={"production_lot": lot}))
				result = get_genealogy("BATCH", direction)
				self.assertEqual(result["production_lot_roots"][0]["production_lot"], lot)
				self.assertIn(stock_field, get_all.call_args.kwargs["filters"])
				self.assertEqual(build.call_args.args[1], direction)

	@patch("smart_edge_custom.manufacturing.planning.validate_stock_item", return_value=frappe._dict(has_batch_no=0))
	def test_non_batch_finished_item_reports_configuration(self, _item):
		from smart_edge_custom.manufacturing.planning import get_finished_stock_row

		with self.assertRaisesRegex(frappe.ValidationError, "must be batch-controlled"):
			get_finished_stock_row(frappe._dict(finished_item="FG-ITEM"), 500, "FG-WAREHOUSE")

	@patch("smart_edge_custom.manufacturing.planning.frappe.get_doc", return_value=frappe._dict(plan_type="Final EB"))
	@patch("smart_edge_custom.manufacturing.planning.lock_production_job")
	@patch("smart_edge_custom.manufacturing.planning.get_open_job")
	def test_repeated_completion_api_returns_existing_lot_without_resetting_actuals(self, get_job, _lock, _get_doc):
		from smart_edge_custom.manufacturing.planning import complete_production_job

		job = get_job.return_value
		job.name = "JOB-DONE"
		job.status = "Completed"
		job.complete_job.return_value = {"status": "Completed", "production_lot": "PCK-EXISTING"}
		result = complete_production_job("PLAN-DONE", actual_qty_meter=999)
		self.assertEqual(result["production_lot"], "PCK-EXISTING")
		job.set.assert_not_called()

	@patch("smart_edge_custom.manufacturing.production_lot.record_movement")
	@patch("smart_edge_custom.manufacturing.production_lot.link_job_lots")
	@patch("smart_edge_custom.manufacturing.production_lot.get_input_production_lot", return_value="EXT-LOT")
	@patch("smart_edge_custom.manufacturing.production_lot.frappe.db.get_value", return_value=frappe._dict(finished_item="FG", stock_uom="Meter"))
	def test_bom_qc_holds_input_without_claiming_it_as_its_own_output(self, _value, _input, link, movement):
		from smart_edge_custom.manufacturing.production_lot import record_qc_hold

		job = frappe._dict(name="JOB-QC", final_eb_bom_generator="GEN", operation="QC")
		entry = frappe._dict(plan_type="Final EB", bom_no="BOM-FG", planning_category="QC", released_qty_meter=490)
		record_qc_hold(job, entry)
		link.assert_called_once_with(job, "EXT-LOT", None)
		self.assertEqual(movement.call_args.args[0], "QC Hold")
		self.assertEqual(movement.call_args.kwargs["quantity"], 490)

	def test_board_does_not_overwrite_plan_quantity_with_generator_quantity(self):
		from contextlib import ExitStack
		from frappe.utils import getdate
		from smart_edge_custom.manufacturing.planning import get_plan_entries

		row = frappe._dict(name="PLAN", creation="2026-10-30 09:00:00", plan_type="Final EB", sequence=1,
			final_eb_bom_generator="GEN", status="Pending", production_quantity=500)
		generator = frappe._dict(name="GEN", finished_item="FG", finished_item_name="Finished Item", production_quantity=999)
		with ExitStack() as stack:
			stack.enter_context(patch("smart_edge_custom.manufacturing.planning.frappe.get_all", return_value=[row]))
			meta = stack.enter_context(patch("smart_edge_custom.manufacturing.planning.frappe.get_meta"))
			meta.return_value.has_field.return_value = False
			stack.enter_context(patch("smart_edge_custom.manufacturing.planning.get_generator_map", return_value={"GEN": generator}))
			stack.enter_context(patch("smart_edge_custom.manufacturing.planning.attach_job_details"))
			stack.enter_context(patch("smart_edge_custom.manufacturing.production_lot.attach_lot_details"))
			stack.enter_context(patch("smart_edge_custom.manufacturing.planning.attach_work_order_progress"))
			stack.enter_context(patch("smart_edge_custom.manufacturing.planning.get_employee_name_map", return_value={}))
			result = get_plan_entries(getdate("2026-10-26"), getdate("2026-11-01"))
		self.assertEqual(result[0].production_quantity, 500)
