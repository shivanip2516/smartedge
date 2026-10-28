frappe.ui.form.on("Final EB BOM Generator", {
	setup(frm) {
		frm.set_query("finished_item", () => ({
			filters: {
				include_item_in_manufacturing: 1,
				is_fixed_asset: 0,
				disabled: 0,
			},
		}));

		frm.preview_debounced = frappe.utils.debounce(() => refresh_preview(frm), 450);
	},

	async refresh(frm) {
		show_status(frm);
		render_configurator_summary(frm);
		frm.add_custom_button(__("Refresh Route"), () => refresh_preview(frm, true));
		frm.add_custom_button(__("Refresh Batch Allocation"), () => refresh_preview(frm, true, true));
		frm.add_custom_button(__("Send to Planning"), () => send_to_planning(frm), __("Actions"));
		await refresh_material_planning(frm);
	},

	async finished_item(frm) {
		await fetch_finished_item_details(frm);
		frm.preview_debounced();
	},

	production_quantity(frm) {
		if (frm.__applying_preview) {
			return;
		}
		frm.preview_debounced();
	},

	thickness_mm(frm) {
		frm.preview_debounced();
	},

	width_mm(frm) {
		frm.preview_debounced();
	},

	weight_factor(frm) {
		frm.preview_debounced();
	},

	compound(frm) {
		frm.preview_debounced();
	},

	compound_consumption_batches(frm) {
		frm.preview_debounced();
	},

	base_colour(frm) {
		frm.preview_debounced();
	},

	shade_consumption_batches(frm) {
		frm.preview_debounced();
	},

	source_warehouse(frm) {
		frm.preview_debounced();
	},

	printing_type(frm) {
		frm.preview_debounced();
	},

	printing_route(frm) {
		frm.preview_debounced();
	},

	extrusion_type(frm) {
		if (frm.doc.extrusion_type === "Wide Sheet Extrusion" && !frm.doc.slitting_requirement) {
			frm.set_value("slitting_requirement", "400 mm Wide Sheet Slitting");
		}
		frm.preview_debounced();
	},

	slitting_requirement(frm) {
		frm.preview_debounced();
	},
});

async function fetch_finished_item_details(frm) {
	if (!frm.doc.finished_item) {
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.final_bom_generator.get_finished_item_details",
		args: {
			finished_item: frm.doc.finished_item,
		},
	});

	const details = response.message || {};
	const fields = [
		"finished_item_name",
		"item_group",
		"stock_uom",
		"custom_size",
		"custom_base_colour",
		"custom_product_category",
		"custom_product_type_design_category",
		"custom_type_finish",
		"custom_type_short_code",
		"custom_customer_order_code",
		"thickness_mm",
		"width_mm",
		"printing_type",
		"printing_route",
		"extrusion_type",
		"slitting_requirement",
		"source_warehouse",
	];

	for (const fieldname of fields) {
		if (details[fieldname] !== undefined && details[fieldname] !== null && details[fieldname] !== "") {
			if (
				["source_warehouse", "printing_route", "extrusion_type", "slitting_requirement"].includes(fieldname) &&
				frm.doc[fieldname]
			) {
				continue;
			}
			await frm.set_value(fieldname, details[fieldname]);
		}
	}

	if (!frm.doc.weight_factor && details.weight_factor) {
		await frm.set_value("weight_factor", details.weight_factor);
	}
}

async function refresh_preview(frm, show_alert = false, refresh_batch_allocations = false) {
	if (!frm.doc.finished_item) {
		return;
	}

	const request_id = (frm.__preview_request_id || 0) + 1;
	frm.__preview_request_id = request_id;
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.final_bom_generator.preview_generator",
		args: {
			doc: refresh_batch_allocations
				? { ...frm.doc, _refresh_batch_allocations: 1 }
				: frm.doc,
		},
		freeze: show_alert,
		freeze_message: __("Calculating BOM preview..."),
	});
	if (request_id !== frm.__preview_request_id) {
		return;
	}

	apply_preview(frm, response.message || {});
	show_status(frm);

	if (show_alert) {
		frappe.show_alert({
			message: __("BOM preview refreshed."),
			indicator: get_indicator(frm.doc.status),
		});
	}
	if (!frm.is_new() && !frm.is_dirty()) {
		await refresh_material_planning(frm);
	}
}

function apply_preview(frm, preview) {
	const scalar_fields = [
		"production_quantity",
		"weight_per_meter_gm",
		"weight_per_meter_kg",
		"total_eb_weight_kg",
		"compound_item",
		"base_colour_item",
		"compound_consumption_per_meter",
		"compound_total_required",
		"compound_available_qty",
		"compound_shortage_qty",
		"shade_consumption_per_meter",
		"shade_total_required",
		"shade_available_qty",
		"shade_shortage_qty",
		"material_weight_status",
		"material_weight_difference_kg_per_meter",
		"compound_cost",
		"shade_cost",
		"material_cost",
		"additional_consumable_cost",
		"operation_cost",
		"labour_cost",
		"total_production_cost",
		"cost_per_meter",
		"warnings",
		"status",
		"selected_route",
	];

	frm.__applying_preview = true;
	for (const fieldname of scalar_fields) {
		if (preview[fieldname] !== undefined && frm.doc[fieldname] !== preview[fieldname]) {
			frm.set_value(fieldname, preview[fieldname]);
		}
	}
	setTimeout(() => {
		frm.__applying_preview = false;
	}, 0);

	set_child_rows(frm, "material_preview", preview.materials || []);
	set_child_rows(frm, "compound_batch_allocations", preview.compound_batch_allocations || []);
	set_child_rows(frm, "shade_batch_allocations", preview.shade_batch_allocations || []);
	set_child_rows(frm, "additional_costs", preview.additional_costs || []);
	set_child_rows(frm, "operation_preview", preview.operations || []);
	render_configurator_summary(frm, preview.route_summary || {});
}

function set_child_rows(frm, table_field, rows) {
	frm.clear_table(table_field);
	for (const data of rows) {
		const row = frm.add_child(table_field);
		Object.assign(row, data);
	}
	frm.refresh_field(table_field);
}

function show_status(frm) {
	const workflow_status = frm.__material_workflow_status || get_material_workflow_status(frm);
	const display_status = workflow_status
		? __("Material Workflow: {0}", [workflow_status])
		: frm.doc.status;
	if (!display_status) {
		return;
	}

	frm.dashboard.clear_headline();
	frm.dashboard.set_headline_alert(
		frappe.utils.escape_html(display_status),
		["Ready to Send to Planning", "Sent to Planning", "Materials Produced"].includes(workflow_status)
			? "green"
			: get_indicator(frm.doc.status)
	);
}

function get_material_workflow_status(frm) {
	if (!frm.doc.compound || !frm.doc.base_colour || !cint(frm.doc.compound_consumption_batches) || !cint(frm.doc.shade_consumption_batches)) {
		return __("Material Configuration Pending");
	}
	if ([frm.doc.compound_production_status, frm.doc.base_colour_production_status].some((status) =>
		["In Progress", "Paused", "Partially Completed"].includes(status)
	)) {
		return __("Material Production In Progress");
	}
	if (frm.__material_planning_context?.released_to_planning) {
		return __("Sent to Planning");
	}
	if (cint(frm.doc.compound_planned_batches) || cint(frm.doc.base_colour_planned_batches)) {
		return __("Material Production Planned");
	}
	return __("Ready to Send to Planning");
}

function render_configurator_summary(frm, route_summary = {}) {
	const wrapper = frm.fields_dict.route_summary_html?.$wrapper;
	if (!wrapper) {
		return;
	}

	const compound_plus_shade =
		flt(frm.doc.compound_total_required) + flt(frm.doc.shade_total_required);
	const rows = [
		{
			title: __("Finished Product"),
			items: [
				[__("Finished Item"), frm.doc.finished_item],
				[__("Item Name"), frm.doc.finished_item_name],
				[__("Size"), frm.doc.custom_size],
				[__("Thickness"), format_number(frm.doc.thickness_mm, 3)],
				[__("Width"), format_number(frm.doc.width_mm, 3)],
				[__("Product Type"), frm.doc.custom_product_type_design_category],
				[__("Finish"), frm.doc.custom_type_finish],
				[__("Base Colour"), frm.doc.custom_base_colour],
				[__("Production Qty"), format_number(frm.doc.production_quantity, 3)],
				[__("Weight / Meter"), `${format_number(frm.doc.weight_per_meter_kg, 6)} Kg/m`],
				[__("Total Weight"), `${format_number(frm.doc.total_eb_weight_kg, 3)} Kg`],
			],
		},
		{
			title: __("Material Inputs"),
			items: [
				[__("Compound"), frm.doc.compound],
				[__("Compound Item"), frm.doc.compound_item],
				[__("Compound Batches"), format_number(frm.doc.compound_consumption_batches, 0)],
				[__("Compound Required"), format_number(frm.doc.compound_total_required, 3)],
				[__("Compound Available"), format_number(frm.doc.compound_available_qty, 3)],
				[__("Shade"), frm.doc.base_colour],
				[__("Shade Item"), frm.doc.base_colour_item],
				[__("Shade Batches"), format_number(frm.doc.shade_consumption_batches, 0)],
				[__("Shade Required"), format_number(frm.doc.shade_total_required, 3)],
				[__("Shade Available"), format_number(frm.doc.shade_available_qty, 3)],
			],
		},
		{
			title: __("Material Workflow"),
			items: [
				[__("Workflow Status"), frm.__material_workflow_status || get_material_workflow_status(frm)],
				[__("Compound Status"), frm.doc.compound_production_status],
				[__("Compound Planned / Completed"), `${cint(frm.doc.compound_planned_batches)} / ${cint(frm.doc.compound_completed_batches)}`],
				[__("Base Colour Status"), frm.doc.base_colour_production_status],
				[__("Base Colour Planned / Completed"), `${cint(frm.doc.base_colour_planned_batches)} / ${cint(frm.doc.base_colour_completed_batches)}`],
			],
		},
		{
			title: __("Process Route"),
			items: [
				[__("Route"), route_summary.route_name || frm.doc.selected_route],
				[__("Extrusion"), frm.doc.extrusion_type],
				[__("Printing"), `${frm.doc.printing_type || ""} / ${frm.doc.printing_route || ""}`],
				[__("Slitting"), frm.doc.slitting_requirement],
			],
		},
		{
			title: __("Final Cost Summary"),
			items: [
				[__("Material Cost"), format_currency(frm.doc.material_cost)],
				[__("Consumables"), format_currency(frm.doc.additional_consumable_cost)],
				[__("Machine Cost"), format_currency(frm.doc.operation_cost)],
				[__("Labour Cost"), format_currency(frm.doc.labour_cost)],
				[__("Total Cost"), format_currency(frm.doc.total_production_cost)],
				[__("Cost / Meter"), format_currency(frm.doc.cost_per_meter)],
			],
		},
	];

	wrapper.html(`
		<div class="smartedge-bom-shell">
			${rows.map(render_summary_section).join("")}
			${render_weight_validation(frm, compound_plus_shade)}
			${render_warning_box(frm)}
		</div>
	`);
}

function render_summary_section(section) {
	return `
		<div class="smartedge-summary-section">
			<div class="smartedge-summary-title">${frappe.utils.escape_html(section.title)}</div>
			<div class="smartedge-summary-grid">
				${section.items
					.map(([label, value]) => {
						const display_value = value === undefined || value === null || value === "" ? "-" : value;
						return `
							<div class="smartedge-summary-card">
								<div class="smartedge-summary-label">${frappe.utils.escape_html(label)}</div>
								<div class="smartedge-summary-value">${frappe.utils.escape_html(display_value)}</div>
							</div>
						`;
					})
					.join("")}
			</div>
		</div>
	`;
}

function render_weight_validation(frm, compound_plus_shade) {
	const matched = frm.doc.material_weight_status === "Material Weight Matched";
	const indicator = matched ? "green" : "orange";
	return `
		<div class="smartedge-validation-card ${indicator}">
			<div>
				<div class="smartedge-summary-title">${__("Material Weight Validation")}</div>
				<div>${__("Calculated EB Weight")}: <strong>${format_number(frm.doc.weight_per_meter_kg, 6)} Kg/m</strong></div>
				<div>${__("Compound + Shade")}: <strong>${format_number(compound_plus_shade, 6)} Kg</strong></div>
			</div>
			<span class="indicator ${indicator}">${frappe.utils.escape_html(frm.doc.material_weight_status || "-")}</span>
		</div>
	`;
}

function render_warning_box(frm) {
	if (!frm.doc.warnings) {
		return "";
	}

	return `<div class="smartedge-summary-warning">${frappe.utils.escape_html(frm.doc.warnings)}</div>`;
}

function format_number(value, precision) {
	if (value === undefined || value === null || value === "") {
		return "-";
	}
	return flt(value, precision).toFixed(precision);
}

function get_indicator(status) {
	if (["Ready for Planning", "Planned", "Completed"].includes(status)) {
		return "green";
	}
	if (["Cancelled", "On Hold"].includes(status)) {
		return "red";
	}
	return "orange";
}

async function send_to_planning(frm) {
	await refresh_preview(frm, true);
	await frm.save();
	const response = await frm.call({
		method: "send_to_planning",
		doc: frm.doc,
		error_handlers: {
			InsufficientMaterialQuantityError: (error) => show_insufficient_material_dialog(error, frm),
		},
	});

	if (response.message?.entries) {
		await frm.reload_doc();
		frappe.show_alert({
			message: __("{0} operation(s) sent to planning.", [response.message.entries.length]),
			indicator: "green",
		});
		frappe.set_route("smartedge-production-planning");
	}
}

function show_insufficient_material_dialog(error, frm) {
	const payload = get_insufficient_material_payload(error);
	if (!payload?.shortages?.length) {
		return;
	}

	const escape = frappe.utils.escape_html;
	const shortages = payload.shortages;
	const rows = shortages
		.map((row) => {
			const uom = escape(row.uom || "");
			const planned = flt(row.planned || 0);
			const additional = flt(row.uncovered ?? row.shortage);
			return `<tr>
				<td><strong>${escape(row.item_name || row.item_code || "")}</strong></td>
				<td>${escape(row.warehouse || "")}</td>
				<td class="text-right">${format_number(row.required, 3)} ${uom}</td>
				<td class="text-right">${format_number(row.available, 3)} ${uom}</td>
				<td class="text-right">${format_number(planned, 3)} ${uom}</td>
				<td class="text-right text-danger"><strong>${format_number(additional, 3)} ${uom}</strong></td>
			</tr>`;
		})
		.join("");

	frappe.dom.set_style(`
		.smartedge-shortage-dialog .table { margin-bottom: 0; }
		.smartedge-shortage-dialog th, .smartedge-shortage-dialog td { padding: 10px 12px; vertical-align: middle; }
		.smartedge-shortage-dialog th { background: var(--subtle-fg); white-space: nowrap; }
		.smartedge-shortage-dialog .table-responsive { border: 1px solid var(--border-color); border-radius: var(--border-radius); }
		@media (max-width: 576px) { .smartedge-shortage-dialog th, .smartedge-shortage-dialog td { padding: 8px; font-size: 12px; } }
	`);

	const dialog = new frappe.ui.Dialog({
		title: __("Insufficient Material Quantity"),
		size: "large",
		fields: [{
			fieldtype: "HTML",
			fieldname: "shortage_details",
			options: `<div class="smartedge-shortage-dialog">
				<div class="flex align-items-start mb-3">
					<span class="fa fa-exclamation-triangle text-warning mr-2" aria-hidden="true"></span>
					<div>
						<div class="text-muted">${__("Cannot Send to Planning because part of the prepared-material shortage is not covered by linked manufacturing plans.")}</div>
						<div class="font-weight-bold mt-2">${__("{0} material(s) require additional manufacturing.", [shortages.length])}</div>
					</div>
				</div>
				<div class="table-responsive">
					<table class="table table-bordered">
						<thead><tr><th>${__("Material")}</th><th>${__("Warehouse")}</th><th class="text-right">${__("Required")}</th><th class="text-right">${__("Available")}</th><th class="text-right">${__("Linked Plan")}</th><th class="text-right">${__("Additional Required")}</th></tr></thead>
						<tbody>${rows}</tbody>
					</table>
				</div>
				<p class="text-muted mt-3 mb-0">${__("Plan the additional quantity before sending this document to planning. Physical stock will only change after manufacturing is completed.")}</p>
			</div>`,
		}],
		primary_action_label: __("Plan Required Materials"),
		primary_action() {
			dialog.hide();
			open_material_planning_dialog(frm);
		},
	});
	dialog.show();
	if (dialog.set_secondary_action_label && dialog.set_secondary_action) {
		dialog.set_secondary_action_label(__("Close"));
		dialog.set_secondary_action(() => dialog.hide());
	}
}

async function refresh_material_planning(frm) {
	frm.remove_custom_button(__("Plan Required Materials"));
	if (frm.is_new() || !frm.doc.name) return;
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.material_production.get_material_planning_context",
		args: { production: frm.doc.name },
	});
	const context = response.message || {};
	frm.__material_planning_context = context;
	frm.__material_workflow_status = context.workflow_status;
	apply_material_status(frm, context);
	add_material_navigation(frm, context);
	if (context.has_shortage) {
		frm.add_custom_button(__("Plan Required Materials"), () => open_material_planning_dialog(frm));
	}
}

function apply_material_status(frm, context) {
	frm.__material_workflow_status = context.workflow_status || frm.__material_workflow_status;
	frm.doc.material_readiness_status = context.overall_status;
	for (const row of context.requirements || []) {
		const prefix = row.material_type === "Compound" ? "compound" : "base_colour";
		frm.doc[`${prefix}_production_status`] = row.status;
		frm.doc[`${prefix}_production_plan`] = row.production_plan;
		frm.doc[`${prefix}_work_order`] = row.work_order;
		frm.doc[`${prefix}_planned_batches`] = row.already_planned_batches;
		frm.doc[`${prefix}_completed_batches`] = row.completed_batches;
	}
	frm.refresh_fields([
		"material_readiness_status",
		"compound_production_status",
		"compound_production_plan",
		"compound_work_order",
		"compound_planned_batches",
		"compound_completed_batches",
		"base_colour_production_status",
		"base_colour_production_plan",
		"base_colour_work_order",
		"base_colour_planned_batches",
		"base_colour_completed_batches",
	]);
	show_status(frm);
	render_configurator_summary(frm);
}

function add_material_navigation(frm, context) {
	for (const label of [
		__("Open Compound Work Order"),
		__("Open Base Colour Work Order"),
		__("Planning Entries"),
		__("Planning Board"),
		__("Reconcile Planning"),
	]) {
		frm.remove_custom_button(label, __("Material Production"));
	}
	const requirements = context.requirements || [];
	for (const row of requirements) {
		if (!row.work_order) continue;
		frm.add_custom_button(
			__("Open {0} Work Order", [row.material_type]),
			() => frappe.set_route("Form", "Work Order", row.work_order),
			__("Material Production")
		);
	}
	frm.add_custom_button(
		__("Planning Entries"),
		() => frappe.set_route("List", "SmartEdge Production Plan Entry", {
			final_eb_bom_generator: frm.doc.name,
			plan_type: "Required Material",
		}),
		__("Material Production")
	);
	frm.add_custom_button(
		__("Planning Board"),
		() => {
			window.location.href = `/app/smartedge-production-planning?search=${encodeURIComponent(frm.doc.name)}`;
		},
		__("Material Production")
	);
	frm.add_custom_button(
		__("Reconcile Planning"),
		async () => {
			await frappe.call({
				method: "smart_edge_custom.manufacturing.material_production.reconcile_required_material_plans",
				args: { production: frm.doc.name },
				freeze: true,
				freeze_message: __("Checking linked material Work Orders..."),
			});
			await frm.reload_doc();
		},
		__("Material Production")
	);
}

async function open_material_planning_dialog(frm) {
	if (frm.is_dirty()) await frm.save();
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.material_production.get_material_planning_context",
		args: { production: frm.doc.name },
		freeze: true,
		freeze_message: __("Checking eligible material batches..."),
	});
	const context = response.message || {};
	const shortages = (context.requirements || []).filter((row) => row.batches_to_plan > 0);
	if (!shortages.length) {
		frappe.show_alert({ message: __("All required material batches are available or already planned."), indicator: "green" });
		await refresh_material_planning(frm);
		return;
	}

	const fields = [];
	const material_states = {};
	for (const row of shortages) {
		const prefix = row.material_type === "Compound" ? "compound" : "base_colour";
		material_states[prefix] = {
			row,
			operation_valid: Boolean(row.operation && (row.operation_options || []).includes(row.operation)),
			workstations: row.compatible_workstations || [],
			restricted: Boolean(row.workstation_filter_applied),
			stock_checked: false,
			stock_available: false,
		};
		fields.push(
			{ fieldtype: "Section Break", label: __("{0} Production", [row.material_type]) },
			{ fieldtype: "Data", fieldname: `${prefix}_material_type`, label: __("Material Type"), read_only: 1, default: row.material_type },
			{ fieldtype: "Link", fieldname: `${prefix}_item`, label: __("Item"), options: "Item", read_only: 1, default: row.item_code },
			{ fieldtype: "Int", fieldname: `${prefix}_required`, label: __("Required Batches"), read_only: 1, default: row.required_batches },
			{ fieldtype: "Int", fieldname: `${prefix}_available`, label: __("Available Eligible Batches"), read_only: 1, default: row.available_batches },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Int", fieldname: `${prefix}_already_planned`, label: __("Already Planned Batches"), read_only: 1, default: row.already_planned_batches },
			{ fieldtype: "Int", fieldname: `${prefix}_batches`, label: __("Batches to Plan"), read_only: 1, default: row.batches_to_plan },
			{ fieldtype: "Float", fieldname: `${prefix}_weight`, label: __("Weight per Batch ({0})", [row.stock_uom]), precision: 6, read_only: 1, default: row.weight_per_batch },
			{ fieldtype: "Float", fieldname: `${prefix}_total`, label: __("Total Manufacturing Quantity ({0})", [row.stock_uom]), precision: 6, read_only: 1, default: row.total_manufacturing_quantity },
			{ fieldtype: "Section Break" },
			{ fieldtype: "Link", fieldname: `${prefix}_bom`, label: __("BOM"), options: "BOM", read_only: 1, default: row.bom_no },
			{
				fieldtype: "Link", fieldname: `${prefix}_operation`, label: __("Operation"), options: "Operation", reqd: 1,
				default: row.operation,
				get_query: () => ({ filters: { name: ["in", row.operation_options || []] } }),
			},
			{
				fieldtype: "Link", fieldname: `${prefix}_workstation`, label: __("Workstation"), options: "Workstation", reqd: 1,
				default: row.default_workstation,
				get_query: () => {
					const state = material_states[prefix];
					const filters = { disabled: 0 };
					if (state.restricted) filters.name = ["in", state.workstations];
					return { filters };
				},
			},
			{ fieldtype: "Column Break" },
			{ fieldtype: "Link", fieldname: `${prefix}_employee`, label: __("Employee"), options: "Employee", reqd: 1, get_query: () => ({ filters: { status: "Active", company: frm.doc.company } }) },
			{ fieldtype: "Link", fieldname: `${prefix}_shift`, label: __("Shift"), options: "Shift Type" },
			{ fieldtype: "Datetime", fieldname: `${prefix}_planned_start`, label: __("Planned Start"), reqd: 1, default: frappe.datetime.now_datetime() },
			{ fieldtype: "Section Break", label: __("Warehouses") },
			{ fieldtype: "Link", fieldname: `${prefix}_source`, label: __("Source Warehouse"), options: "Warehouse", reqd: 1, default: row.source_warehouse, get_query: warehouse_query(frm) },
			{ fieldtype: "Link", fieldname: `${prefix}_wip`, label: __("Work-in-Progress Warehouse"), options: "Warehouse", reqd: 1, default: row.wip_warehouse, get_query: warehouse_query(frm) },
			{ fieldtype: "Link", fieldname: `${prefix}_target`, label: __("Target Warehouse"), options: "Warehouse", reqd: 1, default: row.target_warehouse, get_query: warehouse_query(frm) },
			{ fieldtype: "Section Break", label: __("BOM Raw Material Availability") },
			{ fieldtype: "HTML", fieldname: `${prefix}_raw_stock_status`, options: `<div class="text-muted">${__("Checking raw-material stock...")}</div>` }
		);
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Plan Required Material Production"),
		size: "extra-large",
		fields,
		primary_action_label: __("Create Production Plans"),
		primary_action: async (values) => {
			const plans = shortages.map((row) => {
				const prefix = row.material_type === "Compound" ? "compound" : "base_colour";
				return {
					material_type: row.material_type,
					operation: values[`${prefix}_operation`],
					workstation: values[`${prefix}_workstation`], employee: values[`${prefix}_employee`], shift: values[`${prefix}_shift`],
					planned_start: values[`${prefix}_planned_start`], source_warehouse: values[`${prefix}_source`],
					wip_warehouse: values[`${prefix}_wip`], target_warehouse: values[`${prefix}_target`],
				};
			});
			dialog.disable_primary_action();
			try {
				await frappe.call({
					method: "smart_edge_custom.manufacturing.material_production.create_required_material_plans",
					args: { production: frm.doc.name, plans },
					freeze: true,
					freeze_message: __("Creating Work Orders and production plans..."),
				});
				dialog.hide();
				await frm.reload_doc();
				frappe.show_alert({ message: __("Required material production was planned."), indicator: "green" });
			} finally {
				update_primary_state();
			}
		},
	});
	dialog.show();
	dialog.disable_primary_action();

	const render_stock_status = (prefix, stock) => {
		const state = material_states[prefix];
		const wrapper = dialog.fields_dict[`${prefix}_raw_stock_status`].$wrapper;
		if (stock.message) {
			wrapper.html(`<div class="text-danger">${frappe.utils.escape_html(stock.message)}</div>`);
			return;
		}
		const rows = (stock.materials || []).map((item) => `
			<tr class="${flt(item.shortage_qty) > 0 ? "text-danger" : ""}">
				<td>${frappe.utils.escape_html(item.item_name || item.item_code)}</td>
				<td class="text-right">${flt(item.required_qty, 6)} ${frappe.utils.escape_html(item.uom || "")}</td>
				<td class="text-right">${flt(item.available_qty, 6)} ${frappe.utils.escape_html(item.uom || "")}</td>
			</tr>`).join("");
		wrapper.html(rows
			? `<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr><th>${__("Raw Material")}</th><th class="text-right">${__("Required")}</th><th class="text-right">${__("Available after reservations")}</th></tr></thead><tbody>${rows}</tbody></table></div>
				<div class="${stock.available ? "text-success" : "text-danger"}">${stock.available ? __("Required BOM raw materials are available.") : __("Insufficient BOM raw materials. Production planning is disabled.")}</div>`
			: `<div class="${stock.available ? "text-success" : "text-danger"}">${stock.available ? __("No transferable raw materials are required by this BOM.") : __("Raw-material availability could not be confirmed.")}</div>`);
		state.stock_available = Boolean(stock.available);
	};

	const update_primary_state = () => {
		const values = dialog.get_values(true) || {};
		const ready = shortages.every((row) => {
			const prefix = row.material_type === "Compound" ? "compound" : "base_colour";
			const state = material_states[prefix];
			const workstation = values[`${prefix}_workstation`];
			return state.operation_valid && state.stock_checked && state.stock_available &&
				Boolean(values[`${prefix}_operation`] && workstation && values[`${prefix}_employee`] &&
					values[`${prefix}_planned_start`] && values[`${prefix}_source`] &&
					values[`${prefix}_wip`] && values[`${prefix}_target`]) &&
				state.workstations.includes(workstation);
		});
		if (ready) dialog.enable_primary_action();
		else dialog.disable_primary_action();
	};

	const refresh_raw_stock = async (prefix) => {
		const state = material_states[prefix];
		state.stock_checked = false;
		state.stock_available = false;
		update_primary_state();
		dialog.fields_dict[`${prefix}_raw_stock_status`].$wrapper.html(`<div class="text-muted">${__("Checking raw-material stock...")}</div>`);
		const response = await frappe.call({
			method: "smart_edge_custom.manufacturing.material_production.get_material_raw_stock",
			args: {
				production: frm.doc.name,
				material_type: state.row.material_type,
				source_warehouse: dialog.get_value(`${prefix}_source`),
			},
		});
		state.stock_checked = true;
		render_stock_status(prefix, response.message || {});
		update_primary_state();
	};

	const refresh_workstations = async (prefix) => {
		const state = material_states[prefix];
		const operation = dialog.get_value(`${prefix}_operation`);
		state.operation_valid = false;
		state.workstations = [];
		dialog.set_value(`${prefix}_workstation`, "");
		update_primary_state();
		if (!operation) return;
		const response = await frappe.call({
			method: "smart_edge_custom.manufacturing.material_production.get_material_operation_context",
			args: { bom_no: state.row.bom_no, operation },
		});
		const operation_context = response.message || {};
		state.operation_valid = operation_context.operation === operation;
		state.workstations = operation_context.workstations || [];
		state.restricted = Boolean(operation_context.restricted);
		dialog.set_value(`${prefix}_workstation`, operation_context.default_workstation || "");
		dialog.fields_dict[`${prefix}_workstation`].refresh();
		update_primary_state();
	};

	for (const row of shortages) {
		const prefix = row.material_type === "Compound" ? "compound" : "base_colour";
		dialog.fields_dict[`${prefix}_operation`].df.onchange = () => refresh_workstations(prefix);
		dialog.fields_dict[`${prefix}_source`].df.onchange = () => refresh_raw_stock(prefix);
		for (const fieldname of ["workstation", "employee", "planned_start", "wip", "target"]) {
			dialog.fields_dict[`${prefix}_${fieldname}`].df.onchange = update_primary_state;
		}
		refresh_raw_stock(prefix);
	}
}

function warehouse_query(frm) {
	return () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0 } });
}

function get_insufficient_material_payload(error) {
	try {
		const messages = JSON.parse(error?._server_messages || "[]");
		for (const value of messages) {
			const envelope = typeof value === "string" ? JSON.parse(value) : value;
			const payload = typeof envelope?.message === "string" ? JSON.parse(envelope.message) : envelope;
			if (payload?.type === "insufficient_material_quantity") {
				return payload;
			}
		}
		return null;
	} catch (e) {
		return null;
	}
}
