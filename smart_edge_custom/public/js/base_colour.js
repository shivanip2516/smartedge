frappe.ui.form.on("Base Colour", {
	setup(frm) {
		set_base_colour_queries(frm);
	},

	refresh(frm) {
		frm.set_intro(__("Name + weightage (1 = lightest, 10 = darkest)."));
		set_base_colour_queries(frm);

		if (frm.is_new()) {
			frm.page.set_title(__("Add Base Colour"));
		} else {
			frm.page.set_title(__("Edit Base Colour"));
		}
		refresh_recipe_total(frm);
	},

	colour_name(frm) {
		return refresh_recipe_total(frm);
	},

	validate(frm) {
		validate_base_colour_weightage(frm.doc.weightage);
	},
});

frappe.ui.form.on("Base Colour Pigment", {
	pigments_add(frm) {
		return refresh_recipe_total(frm);
	},

	pigments_delete(frm) {
		return refresh_recipe_total(frm);
	},

	pigment_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.pigment_item) {
			frappe.model.set_value(cdt, cdn, "uom", "");
			return;
		}

		frappe.db.get_value("Item", row.pigment_item, "stock_uom").then(async (response) => {
			await frappe.model.set_value(cdt, cdn, "uom", (response.message && response.message.stock_uom) || "");
			return refresh_recipe_total(frm);
		});
	},

	quantity_kg(frm) {
		return refresh_recipe_total(frm);
	},

	uom(frm) {
		return refresh_recipe_total(frm);
	},
});

async function refresh_recipe_total(frm) {
	if (!frm.doc.colour_name) {
		frm.set_df_property("total_kg", "label", __("Total"));
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.base_colour.get_recipe_total",
		args: {
			item_code: frm.doc.colour_name,
			pigments: frm.doc.pigments || [],
		},
	});
	const details = response.message || {};
	frm.set_df_property("total_kg", "label", __("Total ({0})", [details.stock_uom || ""]));
	await frm.set_value("total_kg", flt(details.total));
}

function set_base_colour_queries(frm) {
	frm.set_query("colour_name", () => ({
		filters: {
			item_group: "Base Colour",
		},
	}));

	frm.set_query("pigment_item", "pigments", () => ({
		filters: {
			item_group: "Pigments",
		},
	}));
}

function validate_base_colour_weightage(weightage) {
	if (!Number.isInteger(Number(weightage)) || Number(weightage) < 1 || Number(weightage) > 10) {
		frappe.throw(__("Weightage must be an integer between 1 and 10."));
	}
}
