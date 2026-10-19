frappe.ui.form.on("BOM", {
	setup(frm) {
		set_item_code_query(frm);
	},
	refresh(frm) {
		refresh_batch_field(frm);
	},

	item(frm) {
		load_compound_recipe(frm);
	},

	custom_process_2(frm) {
		frm.refresh_field("items");
	},
});

const PROCESS_ITEM_GROUPS = {
	Extrusion: ["Compounds", "Pigments"],
	"Extrusion Online Printing": ["Compounds", "Pigments"],
	"Offline Printing": ["Base Colour"],
};

function set_item_code_query(frm) {
	frm.set_query("item_code", "items", () => {
		const item_groups = PROCESS_ITEM_GROUPS[frm.doc.custom_process_2];

		if (item_groups?.length) {
			return {
				query: "smart_edge_custom.bom.get_process_items",
				filters: {
					process: frm.doc.custom_process_2,
				},
			};
		}

		return {
			query: "erpnext.manufacturing.doctype.bom.bom.item_query",
			filters: {
				include_item_in_manufacturing: 1,
				is_fixed_asset: 0,
			},
		};
	});
}

function has_bom_components(frm) {
	return (frm.doc.items || []).some((row) => row.item_code);
}

async function load_compound_recipe(frm) {
	if (!frm.doc.item) {
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.bom.get_compound_recipe",
		args: {
			item_code: frm.doc.item,
		},
	});
	const recipe = response.message || {};
	await apply_batch_recipe_details(frm, recipe);

	if (!recipe.is_compound) {
		await load_base_colour_recipe(frm);
		return;
	}

	if (has_bom_components(frm)) {
		frappe.show_alert({
			message: __("BOM Items already contain components. Compound recipe was not auto-loaded."),
			indicator: "orange",
		});
		return;
	}

	if (!recipe.recipe_found) {
		frappe.show_alert({
			message: __("No Compound recipe found for the selected Compound Item."),
			indicator: "orange",
		});
		return;
	}

	if (!(recipe.rows || []).length) {
		frappe.show_alert({
			message: __("The selected Compound does not have any Additives in its recipe."),
			indicator: "orange",
		});
		return;
	}

	frm.clear_table("items");
	await add_recipe_rows(frm, recipe.rows);

	frappe.show_alert({
		message: __("Compound recipe loaded into BOM Items."),
		indicator: "green",
	});
}

async function load_base_colour_recipe(frm) {
	const response = await frappe.call({
		method: "smart_edge_custom.bom.get_base_colour_recipe",
		args: {
			item_code: frm.doc.item,
		},
	});
	const recipe = response.message || {};
	await apply_batch_recipe_details(frm, recipe);

	if (!recipe.is_base_colour) {
		return;
	}

	if (has_bom_components(frm)) {
		frappe.show_alert({
			message: __("BOM Items already contain components. Base Colour recipe was not auto-loaded."),
			indicator: "orange",
		});
		return;
	}

	if (!recipe.recipe_found) {
		frappe.show_alert({
			message: __("No Base Colour recipe found for the selected Base Colour Item."),
			indicator: "orange",
		});
		return;
	}

	if (!(recipe.rows || []).length) {
		frappe.show_alert({
			message: __("The selected Base Colour does not have any Pigments in its recipe."),
			indicator: "orange",
		});
		return;
	}

	frm.clear_table("items");
	await add_recipe_rows(frm, recipe.rows);

	frappe.show_alert({
		message: __("Base Colour recipe loaded into BOM Items."),
		indicator: "green",
	});
}

async function refresh_batch_field(frm) {
	if (!frm.doc.item) {
		set_batch_field_properties(frm, false);
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.bom.get_bom_batch_details",
		args: { item_code: frm.doc.item },
	});
	set_batch_field_properties(frm, Boolean(response.message?.is_batch_manufacturing_item));
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

async function add_recipe_rows(frm, items) {
	for (const item of items) {
		const row = frm.add_child("items");
		await frappe.model.set_value(row.doctype, row.name, "item_code", item.item_code);
		await frappe.model.set_value(row.doctype, row.name, "qty", item.qty);
		if (item.uom) {
			await frappe.model.set_value(row.doctype, row.name, "uom", item.uom);
		}
	}

	frm.refresh_field("items");
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
