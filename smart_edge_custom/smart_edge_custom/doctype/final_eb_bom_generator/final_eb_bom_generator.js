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

	refresh(frm) {
		show_status(frm);
		render_configurator_summary(frm);
		frm.add_custom_button(__("Refresh Route"), () => refresh_preview(frm, true));
		frm.add_custom_button(__("Send to Planning"), () => send_to_planning(frm), __("Actions"));
	},

	async finished_item(frm) {
		await fetch_finished_item_details(frm);
		frm.preview_debounced();
	},

	production_quantity(frm) {
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

	compound_consumption_per_meter(frm) {
		frm.preview_debounced();
	},

	base_colour(frm) {
		frm.preview_debounced();
	},

	shade_consumption_per_meter(frm) {
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

async function refresh_preview(frm, show_alert = false) {
	if (!frm.doc.finished_item || !frm.doc.production_quantity) {
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.final_bom_generator.preview_generator",
		args: {
			doc: frm.doc,
		},
		freeze: show_alert,
		freeze_message: __("Calculating BOM preview..."),
	});

	apply_preview(frm, response.message || {});
	show_status(frm);

	if (show_alert) {
		frappe.show_alert({
			message: __("BOM preview refreshed."),
			indicator: get_indicator(frm.doc.status),
		});
	}
}

function apply_preview(frm, preview) {
	const scalar_fields = [
		"weight_per_meter_gm",
		"weight_per_meter_kg",
		"total_eb_weight_kg",
		"compound_item",
		"base_colour_item",
		"compound_total_required",
		"compound_available_qty",
		"compound_shortage_qty",
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

	for (const fieldname of scalar_fields) {
		if (preview[fieldname] !== undefined) {
			frm.set_value(fieldname, preview[fieldname]);
		}
	}

	set_child_rows(frm, "material_preview", preview.materials || []);
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
	if (!frm.doc.status) {
		return;
	}

	frm.dashboard.clear_headline();
	frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.status), get_indicator(frm.doc.status));
}

function render_configurator_summary(frm, route_summary = {}) {
	const wrapper = frm.fields_dict.route_summary_html?.$wrapper;
	if (!wrapper) {
		return;
	}

	const compound_plus_shade =
		flt(frm.doc.compound_consumption_per_meter) + flt(frm.doc.shade_consumption_per_meter);
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
				[__("Compound / Meter"), `${format_number(frm.doc.compound_consumption_per_meter, 6)} Kg`],
				[__("Compound Required"), format_number(frm.doc.compound_total_required, 3)],
				[__("Compound Available"), format_number(frm.doc.compound_available_qty, 3)],
				[__("Shade"), frm.doc.base_colour],
				[__("Shade Item"), frm.doc.base_colour_item],
				[__("Shade / Meter"), `${format_number(frm.doc.shade_consumption_per_meter, 6)} Kg`],
				[__("Shade Required"), format_number(frm.doc.shade_total_required, 3)],
				[__("Shade Available"), format_number(frm.doc.shade_available_qty, 3)],
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
				<div>${__("Compound + Shade")}: <strong>${format_number(compound_plus_shade, 6)} Kg/m</strong></div>
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
	const response = await frm.call("send_to_planning");

	if (response.message?.entries) {
		await frm.reload_doc();
		frappe.show_alert({
			message: __("{0} operation(s) sent to planning.", [response.message.entries.length]),
			indicator: "green",
		});
		frappe.set_route("smartedge-production-planning");
	}
}
