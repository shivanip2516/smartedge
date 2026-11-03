frappe.ui.form.on("BOM", {
	setup(frm) {
		frm.set_query("item_code", "items", () => ({
			query: "smart_edge_custom.bom.get_bom_items",
			filters: { parent_item: frm.doc.item },
		}));
	},
	async refresh(frm) {
		// Retain historical data while the metadata patch hides the retired field.
		frm.toggle_display("custom_process_2", false);
		await refresh_batch_field(frm);
		await update_recipe_quantity(frm);
	},
	async item(frm) {
		frm.refresh_field("items");
		await refresh_batch_field(frm, true);
		await warn_invalid_components(frm);
		await update_recipe_quantity(frm);
	},
	validate: update_recipe_quantity,
});

frappe.ui.form.on("BOM Item", {
	items_add: update_recipe_quantity,
	items_remove: update_recipe_quantity,
	item_code: update_recipe_quantity,
	qty: update_recipe_quantity,
	uom: update_recipe_quantity,
	conversion_factor: update_recipe_quantity,
});

async function update_recipe_quantity(frm) {
	if (frm.doc.docstatus || !frm.doc.item) return;
	const snapshot = () => JSON.stringify({
		item_code: frm.doc.item,
		items: (frm.doc.items || []).map(({ item_code, qty, uom, conversion_factor }) => ({
			item_code, qty, uom, conversion_factor,
		})),
	});
	const request = snapshot();
	const response = await frappe.call({
		method: "smart_edge_custom.bom.get_bom_quantity",
		args: JSON.parse(request),
	});
	// Ignore responses for rows or a parent that changed while the call ran.
	if (frm.doc.docstatus || snapshot() !== request || response.message == null) return;
	const total = flt(response.message);
	if (flt(frm.doc.quantity) !== total) {
		await frm.set_value("quantity", total);
	}
}

async function warn_invalid_components(frm) {
	const parent_item = frm.doc.item;
	if (!parent_item || !(frm.doc.items || []).some((row) => row.item_code)) return;
	const { message: item } = await frappe.db.get_value("Item", parent_item, "item_group");
	if (frm.doc.item !== parent_item) return;
	const allowed_group = { Compounds: "Additives", "Base Colour": "Pigments" }[item?.item_group];
	if (!allowed_group) return;
	const invalid = await Promise.all(
		frm.doc.items.filter((row) => row.item_code).map(async (row) => {
			const item_code = row.item_code;
			const { message: component } = await frappe.db.get_value("Item", item_code, "item_group");
			if (row.item_code === item_code && component?.item_group !== allowed_group) {
				return __("Row {0}: {1}", [row.idx, item_code]);
			}
		})
	);
	if (frm.doc.item === parent_item && invalid.some(Boolean)) {
		frappe.msgprint({
			title: __("Invalid BOM components"),
			indicator: "orange",
			message: __("Only Items in Item Group {0} are allowed. Please correct: {1}", [
				frappe.utils.escape_html(allowed_group),
				invalid.filter(Boolean).map((row) => frappe.utils.escape_html(row)).join("; "),
			]),
		});
	}
}

async function refresh_batch_field(frm, apply_defaults = false) {
	const parent_item = frm.doc.item;
	if (!parent_item) {
		set_batch_field_properties(frm, false);
		return;
	}
	const response = await frappe.call({
		method: "smart_edge_custom.bom.get_bom_batch_details",
		args: { item_code: parent_item },
	});
	if (frm.doc.item !== parent_item) return;
	const details = response.message || {};
	if (apply_defaults) {
		await apply_batch_recipe_details(frm, details);
	} else {
		set_batch_field_properties(frm, Boolean(details.is_batch_manufacturing_item));
	}
}

async function apply_batch_recipe_details(frm, details) {
	const batch_controlled = Boolean(details.is_batch_manufacturing_item);
	set_batch_field_properties(frm, batch_controlled);

	if (batch_controlled) {
		await frm.set_value("custom_batch", 1);
	}

	if (details.is_batch_manufacturing_item && flt(details.output_qty) > 0) {
		await frm.set_value("quantity", flt(details.output_qty));
	}
}

function set_batch_field_properties(frm, enabled) {
	frm.toggle_display("custom_batch", enabled);
	frm.toggle_reqd("custom_batch", enabled);
}

frappe.provide("smart_edge_custom.batch_manufacturing");

const bom_make_work_order_handlers = frappe.ui.form.get_event_handler_list("BOM", "make_work_order");
if (
	bom_make_work_order_handlers.length &&
	!smart_edge_custom.batch_manufacturing.standard_bom_make_work_order
) {
	smart_edge_custom.batch_manufacturing.standard_bom_make_work_order =
		bom_make_work_order_handlers[0];
	bom_make_work_order_handlers[0] = make_batch_aware_work_order;
}

async function make_batch_aware_work_order(frm) {
	const details = await frappe.xcall(
		"smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details",
		{
			item_code: frm.doc.item,
			bom_no: frm.doc.name,
		}
	);

	if (!details?.applicable) {
		return smart_edge_custom.batch_manufacturing.standard_bom_make_work_order(frm);
	}

	const weight_per_batch = flt(details.weight_per_batch);
	const stock_uom = details.stock_uom || __("Kg");
	let dialog;
	const update_total = () => {
		if (!dialog) return;
		const batches = cint(dialog.get_value("number_of_batches"));
		dialog.set_value("total_manufacturing_quantity", flt(batches * weight_per_batch));
	};

	dialog = new frappe.ui.Dialog({
		title: __("Create Work Order"),
		fields: [
			{
				fieldname: "number_of_batches",
				fieldtype: "Int",
				label: __("No. of Batches"),
				default: 1,
				reqd: 1,
				non_negative: 1,
				onchange: update_total,
			},
			{
				fieldname: "weight_per_batch",
				fieldtype: "Float",
				label: __("Weight per Batch ({0})", [stock_uom]),
				default: weight_per_batch,
				read_only: 1,
			},
			{
				fieldname: "total_manufacturing_quantity",
				fieldtype: "Float",
				label: __("Total Manufacturing Quantity ({0})", [stock_uom]),
				default: weight_per_batch,
				read_only: 1,
			},
			{
				fieldname: "use_multi_level_bom",
				fieldtype: "Check",
				label: __("Use Multi-Level BOM"),
				default: frm.doc?.__onload?.use_multi_level_bom,
				hidden: frm.doc.track_semi_finished_goods ? 1 : 0,
			},
		],
		primary_action_label: __("Create"),
		primary_action: async (values) => {
			if (cint(values.number_of_batches) < 1) {
				frappe.msgprint(__("No. of Batches must be at least 1."));
				return;
			}
			dialog.disable_primary_action();
			try {
				const work_order = await frappe.xcall(
					"smart_edge_custom.manufacturing.batch_manufacturing.make_batch_work_order",
					{
						bom_no: frm.doc.name,
						item: frm.doc.item,
						number_of_batches: values.number_of_batches,
						company: frm.doc.company,
						project: frm.doc.project,
						use_multi_level_bom: values.use_multi_level_bom,
					}
				);
				dialog.hide();
				const doc = frappe.model.sync(work_order)[0];
				frappe.set_route("Form", doc.doctype, doc.name);
			} finally {
				dialog.enable_primary_action();
			}
		},
	});
	dialog.show();
}
