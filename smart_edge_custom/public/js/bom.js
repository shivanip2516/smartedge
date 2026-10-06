frappe.ui.form.on("BOM", {
	setup(frm) {
		set_item_code_query(frm);
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
