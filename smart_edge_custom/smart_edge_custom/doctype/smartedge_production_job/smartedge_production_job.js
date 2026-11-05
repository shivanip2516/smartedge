frappe.ui.form.on("SmartEdge Production Job", {
	setup(frm) {
		const employee_query = () => {
			const filters = { status: "Active" };
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			return { filters };
		};
		frm.set_query("operator_employee", employee_query);
		frm.set_query("supervisor_employee", employee_query);
	},

	async refresh(frm) {
		set_indicator(frm);
		add_job_actions(frm);
		set_operator_hint(frm);
		await show_recovered_material_allocations(frm);
		await show_scrap_movement_history(frm);
		await show_qc_visibility(frm);
	},

	operator_employee(frm) {
		set_operator_hint(frm);
	},
});

async function show_scrap_movement_history(frm) {
	if (frm.is_new()) return;
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.unusable_scrap.get_job_scrap_movement_history",
		args: { production_job: frm.doc.name },
	});
	const rows = response.message || [];
	if (!rows.length) return;
	const content = rows.map((row) => {
		const stock_entry = row.stock_entry
			? `<a href="/app/stock-entry/${encodeURIComponent(row.stock_entry)}">${frappe.utils.escape_html(row.stock_entry)}</a>` : "—";
		return `<div class="mb-2">
			<strong>${frappe.utils.escape_html(row.movement_type || "")}</strong> ·
			${format_number(row.quantity || 0, null, 6)} ${frappe.utils.escape_html(row.uom || "")} ·
			${frappe.utils.escape_html(row.status || "")} · ${frappe.utils.escape_html(String(row.movement_datetime || ""))}<br>
			${frappe.utils.escape_html(row.source_operation || "")} · ${frappe.utils.escape_html(row.source_workstation || "")} ·
			${frappe.utils.escape_html(row.source_employee || "")} · ${stock_entry}
		</div>`;
	}).join("");
	frm.dashboard.add_section(`<div class="small">${content}</div>`, __("Scrap Movement History"), "custom smartedge-scrap-history");
}

function set_indicator(frm) {
	if (!frm.doc.status) {
		return;
	}
	const indicator = {
		Draft: "gray",
		"In Progress": "green",
		Paused: "orange",
		Completed: "blue",
		"On Hold": "orange",
		Cancelled: "red",
	}[frm.doc.status] || "gray";
	frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.status), indicator);
}

async function show_recovered_material_allocations(frm) {
	if (frm.is_new()) return;
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.scrap_recovery.get_job_recovered_allocations",
		args: { production_job: frm.doc.name },
	});
	const rows = response.message || [];
	if (!rows.length) return;
	const content = rows.map((row) => `
		<div>
			<a href="/app/smartedge-recovered-material-allocation/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a>:
			${frappe.utils.escape_html(row.recovered_material_item || "")} ·
			<a href="/app/batch/${encodeURIComponent(row.recovered_material_batch)}">${frappe.utils.escape_html(row.recovered_material_batch || "")}</a> ·
			${format_number(row.allocated_quantity || 0, null, 6)} ${frappe.utils.escape_html(row.uom || "")} ·
			${frappe.utils.escape_html(row.status || "")}
		</div>`).join("");
	frm.dashboard.add_section(`<div class="small">${content}</div>`, __("Recovered Material Reuse"), "custom smartedge-recovered-material");
}

function add_job_actions(frm) {
	if (frm.is_new() || frm.doc.status === "Completed" || frm.doc.status === "Cancelled") {
		return;
	}
	if (["Draft", "On Hold"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Start Job"), () => call_job_action(frm, "start_job"), __("Actions"));
	}
	if (frm.doc.status === "In Progress") {
		frm.add_custom_button(__("Pause"), () => open_pause_dialog(frm), __("Actions"));
		frm.add_custom_button(__("Complete Job"), () => call_job_action(frm, "complete_job"), __("Actions"));
	}
	if (frm.doc.status === "Paused") {
		frm.add_custom_button(__("Resume"), () => call_job_action(frm, "resume_job"), __("Actions"));
		frm.add_custom_button(__("Complete Job"), () => call_job_action(frm, "complete_job"), __("Actions"));
	}
	if (["Draft", "On Hold", "In Progress", "Paused"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Allocate Recovered Material"), () => open_recovered_material_dialog(frm), __("Actions"));
	}
}

async function open_recovered_material_dialog(frm) {
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.scrap_recovery.get_recovered_material_options",
		args: { production_job: frm.doc.name },
	});
	const options = response.message || [];
	if (!options.length) {
		frappe.msgprint(__("No QC-released recovered material is permitted and available for this operation."));
		return;
	}
	const by_label = {};
	for (const row of options) {
		const label = `${row.item_code} · ${row.batch_no} · ${format_number(row.maximum_selectable_quantity, null, 6)} ${row.uom}`;
		by_label[label] = row;
	}
	const dialog = new frappe.ui.Dialog({
		title: __("Allocate Recovered Material"),
		fields: [
			{ fieldtype: "Select", fieldname: "selection", label: __("Eligible Recovered Batch"), options: Object.keys(by_label), reqd: 1 },
			{ fieldtype: "Float", fieldname: "quantity", label: __("Quantity to Reuse"), precision: 6, reqd: 1 },
		],
		primary_action_label: __("Allocate"),
		primary_action: async (values) => {
			const selected = by_label[values.selection];
			if (!selected || flt(values.quantity) <= 0 || flt(values.quantity) > flt(selected.maximum_selectable_quantity)) {
				frappe.msgprint(__("Enter a positive quantity within the permitted available limit."));
				return;
			}
			await frappe.call({
				method: "smart_edge_custom.manufacturing.scrap_recovery.create_recovered_allocation",
				args: { production_job: frm.doc.name, recovery: selected.recovery, quantity: values.quantity },
			});
			dialog.hide();
			await frm.reload_doc();
		},
	});
	dialog.show();
}

async function call_job_action(frm, method) {
	await frm.call(method);
	await frm.reload_doc();
}

function open_pause_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Pause Production"),
		fields: [
			{ fieldtype: "Data", fieldname: "production_job", label: __("Production Job"), read_only: 1, default: frm.doc.name },
			{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: frm.doc.operation },
			{ fieldtype: "Data", fieldname: "operator_name", label: __("Employee"), read_only: 1, default: frm.doc.operator_name || "" },
			{
				fieldtype: "Link",
				fieldname: "downtime_reason",
				label: __("Downtime Reason"),
				options: "SmartEdge Downtime Reason",
				reqd: 1,
				get_query: () => ({ filters: { disabled: 0 } }),
			},
			{ fieldtype: "Small Text", fieldname: "downtime_remarks", label: __("Remarks") },
		],
		primary_action_label: __("Confirm Pause"),
		primary_action: async (values) => {
			await frm.call("pause_job", {
				downtime_reason: values.downtime_reason,
				downtime_remarks: values.downtime_remarks,
			});
			dialog.hide();
			await frm.reload_doc();
		},
	});
	dialog.show();
}

function set_operator_hint(frm) {
	if (frm.doc.status === "Draft" && !frm.doc.operator_employee) {
		frm.set_intro(__("Select an active Employee as Operator before starting this production job."), "orange");
		return;
	}
	frm.set_intro("");
}

async function show_qc_visibility(frm) {
	if (frm.is_new()) return;
	const job_name = frm.doc.name;
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.quality_control.get_job_qc_visibility",
		args: { production_job: job_name },
	});
	if (frm.doc.name !== job_name) return;
	const context = response.message || {};
	if (!context.qc_required) return;
	const colors = {
		"QC Pending": "orange",
		"QC Approved": "green",
		"QC Rejected": "red",
	};
	frm.dashboard.add_indicator(__(context.qc_display_status), colors[context.qc_display_status] || "orange");
	const inspection = context.quality_inspection
		? `<a href="/app/quality-inspection/${encodeURIComponent(context.quality_inspection)}">${frappe.utils.escape_html(context.quality_inspection)}</a>`
		: "—";
	const warning = context.notification_warning
		? `<div class="alert alert-warning mt-2">${frappe.utils.escape_html(context.notification_warning)}</div>`
		: "";
	frm.dashboard.add_section(`
		<div class="small">
			<div><strong>${__("Production Status")}:</strong> ${frappe.utils.escape_html(frm.doc.status || "")}</div>
			<div><strong>${__("QC Status")}:</strong> ${frappe.utils.escape_html(context.qc_display_status || "")}</div>
			<div><strong>${__("Production Job")}:</strong> ${frappe.utils.escape_html(context.production_job || "")}</div>
			<div><strong>${__("Operation")}:</strong> ${frappe.utils.escape_html(context.operation || "")}</div>
			<div><strong>${__("Workstation")}:</strong> ${frappe.utils.escape_html(context.workstation || "")}</div>
			<div><strong>${__("Completed Quantity")}:</strong> ${format_number(context.completed_quantity || 0, null, 6)}</div>
			<div><strong>${__("Quality Inspection")}:</strong> ${inspection}</div>
			${warning}
		</div>
	`, __("Quality Control"), "custom smartedge-qc-visibility");
}
