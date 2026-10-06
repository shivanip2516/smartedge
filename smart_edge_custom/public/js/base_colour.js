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
	},

	validate(frm) {
		validate_base_colour_weightage(frm.doc.weightage);
	},
});

frappe.ui.form.on("Base Colour Pigment", {
	pigment_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.pigment_item) {
			frappe.model.set_value(cdt, cdn, "uom", "");
			return;
		}

		frappe.db.get_value("Item", row.pigment_item, "stock_uom").then((response) => {
			frappe.model.set_value(cdt, cdn, "uom", (response.message && response.message.stock_uom) || "");
		});
	},
});

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
