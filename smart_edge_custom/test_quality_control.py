import unittest
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import quality_control as qc


class TestQualityControl(unittest.TestCase):
	def setUp(self):
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)

		self.enterContext(patch.object(qc, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(qc.frappe, "throw", side_effect=throw))
		self.enterContext(patch.object(frappe, "logger", return_value=logging.getLogger("quality-control-test")))
		frappe.local.session = frappe._dict(user="operator@example.com")
		frappe.local.flags = frappe._dict()
		frappe.local.db = Mock()
		self.addCleanup(delattr, frappe.local, "session")
		self.addCleanup(delattr, frappe.local, "flags")
		self.addCleanup(delattr, frappe.local, "db")

	def test_fully_classified_quantities_are_accepted(self):
		values = qc.validate_quantities(4524, 4524, 4400, 24, 100)
		self.assertEqual(values["Inspected Quantity"], 4524)

	def test_partial_inspection_is_supported(self):
		values = qc.validate_quantities(4524, 4000, 4000, 0, 0)
		self.assertEqual(values["Accepted Quantity"], 4000)

	def test_negative_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			qc.validate_quantities(100, 100, 101, -1, 0)

	def test_unreconciled_quantity_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			qc.validate_quantities(100, 100, 99, 0, 0)

	def test_inspection_cannot_exceed_eligible_quantity(self):
		with self.assertRaises(frappe.ValidationError):
			qc.validate_quantities(100, 101, 101, 0, 0)

	def test_decision_status_preserves_partial_acceptance(self):
		self.assertEqual(qc._decision_status(80, 20, 0), "Accepted")
		self.assertEqual(qc._decision_status(0, 20, 0), "Rejected")
		self.assertEqual(qc._decision_status(80, 0, 20), "Rework Required")

	def test_qc_visibility_uses_actual_decision_not_document_creation(self):
		self.assertEqual(qc.get_qc_display_status(), "QC Pending")
		self.assertEqual(
			qc.get_qc_display_status(frappe._dict(docstatus=0, status="Accepted")),
			"QC Pending",
		)
		self.assertEqual(
			qc.get_qc_display_status(frappe._dict(docstatus=1, status="Accepted")),
			"QC Approved",
		)
		self.assertEqual(
			qc.get_qc_display_status(frappe._dict(docstatus=1, status="Rejected")),
			"QC Rejected",
		)
		self.assertEqual(
			qc.get_qc_display_status(frappe._dict(custom_smartedge_qc_status="Under Review")),
			"QC Pending",
		)
		self.assertEqual(
			qc.get_qc_display_status(frappe._dict(custom_smartedge_qc_status="Rework Required")),
			"QC Rework Required",
		)

	def test_new_inspection_uses_valid_erpnext_status_for_pending_custom_qc(self):
		doc = Mock()
		qc_entry = frappe._dict(
			name="PLAN-QC",
			final_eb_bom_generator="GEN-1",
			operation="QC After Extrusion",
		)
		with patch.object(qc, "_source_details", return_value=(
			frappe._dict(name="PLAN-EXT"),
			frappe._dict(name="JOB-1"),
			"LOT-1",
			"BATCH-1",
		)), patch.object(qc, "_next_key", return_value="smartedge-qc:PLAN-QC:1"), patch.object(
			qc.frappe, "new_doc", return_value=doc
		), patch.object(
			qc.frappe.db, "get_value", return_value=frappe._dict(finished_item="FG-1", company="ACME")
		), patch.object(qc, "_notify_quality_managers"):
			qc._new_inspection(qc_entry, 100)

		self.assertEqual(doc.status, "Accepted")
		self.assertEqual(doc.custom_smartedge_qc_status, "Pending")
		doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_resubmit_rework_does_not_mutate_submitted_source(self):
		source = frappe._dict(
			name="MAT-QA-2026-00008", docstatus=1,
			custom_smartedge_qc_status="Rework Required", custom_rework_resolved=0,
			custom_rework_resubmitted_to=None, custom_qc_plan_entry="PLAN-QC",
			custom_rework_qty=36, custom_source_production_job="SE-JOB-2026-00154",
		)
		new_doc = frappe._dict(name="MAT-QA-2026-00009", custom_smartedge_qc_status="Pending")
		with patch.object(qc, "require_qc_authority"), patch.object(qc, "_lock_inspection", return_value=source), patch.object(
			qc, "_validate_smartedge_inspection"
		), patch.object(qc.frappe, "get_all", return_value=[]), patch.object(
			qc.frappe, "get_doc", return_value=frappe._dict(name="PLAN-QC")
		), patch.object(qc, "_new_inspection", return_value=new_doc) as create, patch.object(
			qc, "_save_api"
		) as save:
			result = qc.resubmit_rework(source.name)

		self.assertEqual(result["quality_inspection"], "MAT-QA-2026-00009")
		create.assert_called_once_with(
			frappe._dict(name="PLAN-QC"), 36,
			source_job="SE-JOB-2026-00154", source_qc="MAT-QA-2026-00008",
		)
		save.assert_not_called()

	def test_resubmit_rework_reuses_existing_reinspection(self):
		source = frappe._dict(
			name="MAT-QA-2026-00008", docstatus=1,
			custom_smartedge_qc_status="Rework Required", custom_rework_resolved=0,
			custom_rework_resubmitted_to=None, custom_qc_plan_entry="PLAN-QC",
			custom_rework_qty=36, custom_source_production_job="SE-JOB-2026-00154",
		)
		existing = frappe._dict(name="MAT-QA-2026-00009", custom_smartedge_qc_status="Pending")
		with patch.object(qc, "require_qc_authority"), patch.object(qc, "_lock_inspection", return_value=source), patch.object(
			qc, "_validate_smartedge_inspection"
		), patch.object(qc.frappe, "get_all", return_value=[frappe._dict(name=existing.name)]), patch.object(
			qc.frappe, "get_doc", side_effect=[existing]
		), patch.object(qc, "_new_inspection") as create:
			result = qc.resubmit_rework(source.name)

		self.assertEqual(result["quality_inspection"], existing.name)
		create.assert_not_called()

	def test_board_qc_enrichment_preserves_source_operation_identity(self):
		source = frappe._dict(
			name="PLAN-EXT",
			operation="Single Mould Extrusion + Integrated Online Printing",
			workstation="Single Mould Extrusion",
			production_job="JOB-EXT",
			status="Completed",
		)
		checkpoint = frappe._dict(
			name="PLAN-QC",
			operation="QC After Extrusion",
			workstation="QC After Extrusion",
		)
		context = {
			"operation": checkpoint.operation,
			"workstation": checkpoint.workstation,
			"production_job": "JOB-QC-SOURCE",
			"qc_display_status": "QC Pending",
			"accepted_qty": 0,
		}

		def previous(row):
			return source if row.name == checkpoint.name else None

		with patch.object(qc, "is_qc_entry", side_effect=lambda row: row.name == checkpoint.name), patch.object(
			qc, "get_qc_context", return_value=context
		), patch(
			"smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry",
			side_effect=previous,
		):
			qc.attach_qc_details([source, checkpoint])

		self.assertEqual(source.operation, "Single Mould Extrusion + Integrated Online Printing")
		self.assertEqual(source.workstation, "Single Mould Extrusion")
		self.assertEqual(source.production_job, "JOB-EXT")
		self.assertEqual(source.qc_checkpoint_operation, "QC After Extrusion")
		self.assertEqual(source.qc_display_status, "QC Pending")
		self.assertTrue(source.qc_required)

	def test_board_qc_enrichment_does_not_mark_running_source_as_pending(self):
		source = frappe._dict(
			name="PLAN-EXT",
			operation="Single Mould Extrusion",
			workstation="Single Mould Extrusion",
			production_job="JOB-EXT",
			status="In Progress",
		)
		checkpoint = frappe._dict(
			name="PLAN-QC",
			operation="QC After Extrusion",
			workstation="QC After Extrusion",
		)
		context = {
			"operation": checkpoint.operation,
			"qc_display_status": "Awaiting Manufacturing Output",
			"qc_status": "Blocked",
		}

		def previous(row):
			return source if row.name == checkpoint.name else None

		with patch.object(qc, "is_qc_entry", side_effect=lambda row: row.name == checkpoint.name), patch.object(
			qc, "get_qc_context", return_value=context
		), patch(
			"smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry",
			side_effect=previous,
		):
			qc.attach_qc_details([source, checkpoint])

		self.assertFalse(getattr(source, "qc_required", False))
		self.assertFalse(getattr(source, "qc_display_status", None))

	def test_aggregate_counts_reinspection_acceptance_without_double_releasing_rework(self):
		rows = [
			frappe._dict(name="QI-1", custom_smartedge_qc_status="Rework Required", custom_eligible_qty=100,
				custom_inspected_qty=100, custom_accepted_qty=80, custom_rejected_qty=0, custom_rework_qty=20,
				custom_source_qc=None, custom_rework_resolved=1, modified="1"),
			frappe._dict(name="QI-2", custom_smartedge_qc_status="Accepted", custom_eligible_qty=20,
				custom_inspected_qty=20, custom_accepted_qty=20, custom_rejected_qty=0, custom_rework_qty=0,
				custom_source_qc="QI-1", custom_rework_resolved=0, modified="2"),
		]
		with patch.object(qc, "_inspection_rows", return_value=rows):
			result = qc.aggregate("PLAN-QC")
		self.assertEqual(result.accepted_qty, 100)
		self.assertEqual(result.root_inspected_qty, 100)
		self.assertEqual(result.unresolved_rework_qty, 0)

	def test_reinspection_resolves_parent_without_submitted_source_update(self):
		rows = [
			frappe._dict(name="QI-1", custom_smartedge_qc_status="Rework Required", custom_eligible_qty=100,
				custom_inspected_qty=100, custom_accepted_qty=80, custom_rejected_qty=0, custom_rework_qty=20,
				custom_source_qc=None, custom_rework_resolved=0, modified="1"),
			frappe._dict(name="QI-2", custom_smartedge_qc_status="Accepted", custom_eligible_qty=20,
				custom_inspected_qty=20, custom_accepted_qty=20, custom_rejected_qty=0, custom_rework_qty=0,
				custom_source_qc="QI-1", custom_rework_resolved=0, modified="2"),
		]
		with patch.object(qc, "_inspection_rows", return_value=rows):
			result = qc.aggregate("PLAN-QC")
		self.assertEqual(result.accepted_qty, 100)
		self.assertEqual(result.unresolved_rework_qty, 0)

	def test_pending_notification_is_role_scoped_and_idempotent(self):
		inspection = frappe._dict(name="QI-1", custom_eligible_qty=100)
		entry = frappe._dict(operation="QC After Extrusion")
		job = frappe._dict(
			name="JOB-1", operation="Extrusion", workstation="EXT-1", actual_end="2026-11-03 10:00:00"
		)
		production = frappe._dict(finished_item="FG-1")
		notification = Mock()
		with patch.object(qc, "get_qc_recipient_users", return_value=["qc@example.com"]), patch.object(
			qc.frappe.db, "exists", return_value=False
		), patch.object(qc.frappe, "new_doc", return_value=notification), patch.object(
			qc, "format_datetime", return_value="03-11-2026 10:00"
		):
			qc._notify_quality_managers(inspection, entry, job, production)
		self.assertEqual(notification.for_user, "qc@example.com")
		self.assertEqual(notification.document_type, "Quality Inspection")
		self.assertEqual(notification.document_name, "QI-1")
		self.assertEqual(notification.subject, "QC Inspection Pending — JOB-1 — FG-1")
		notification.insert.assert_called_once_with(ignore_permissions=True)

		with patch.object(qc, "get_qc_recipient_users", return_value=["qc@example.com"]), patch.object(
			qc.frappe.db, "exists", return_value=True
		), patch.object(qc.frappe, "new_doc") as new_doc, patch.object(
			qc, "format_datetime", return_value="03-11-2026 10:00"
		):
			qc._notify_quality_managers(inspection, entry, job, production)
		new_doc.assert_not_called()

	def test_unauthorized_user_cannot_decide(self):
		with patch.object(frappe, "get_roles", return_value=["Manufacturing User"]):
			with self.assertRaises(frappe.PermissionError):
				qc.require_qc_authority()

	def test_quality_manager_is_authorized(self):
		frappe.local.session.user = "qc@example.com"
		with patch.object(frappe, "get_roles", return_value=[qc.QC_ROLE]):
			qc.require_qc_authority()

	def test_smartedge_reference_skips_standard_child_item_table(self):
		doc = SimpleNamespace(reference_type="SmartEdge Production Job", reference_name="SE-JOB-TEST")
		with patch.object(qc.QualityInspection, "set_child_row_reference") as standard_child_sync, patch.object(
			qc.QualityInspection, "update_qc_reference"
		) as standard_reference_sync:
			qc.SmartEdgeQualityInspection.set_child_row_reference(doc)
			qc.SmartEdgeQualityInspection.update_qc_reference(doc)
		standard_child_sync.assert_not_called()
		standard_reference_sync.assert_not_called()

	def test_smartedge_submission_requires_api_authority(self):
		blocked = object.__new__(qc.SmartEdgeQualityInspection)
		blocked.custom_smartedge_qc_key = "smartedge-qc:1"
		blocked.flags = frappe._dict()
		with self.assertRaises(frappe.ValidationError):
			qc.SmartEdgeQualityInspection.before_submit(blocked)

		allowed = object.__new__(qc.SmartEdgeQualityInspection)
		allowed.custom_smartedge_qc_key = "smartedge-qc:1"
		allowed.flags = frappe._dict(smartedge_qc_api=True)
		with patch.object(qc.QualityInspection, "before_submit") as standard_before_submit:
			qc.SmartEdgeQualityInspection.before_submit(allowed)
		standard_before_submit.assert_called_once_with()


if __name__ == "__main__":
	unittest.main()
