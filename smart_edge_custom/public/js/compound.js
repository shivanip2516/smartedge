frappe.ui.form.on("Compound Additive", {
	additive(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.additive) {
			frappe.model.set_value(cdt, cdn, "uom", "");
			return;
		}

		frappe.db.get_value("Item", row.additive, "stock_uom").then((response) => {
			frappe.model.set_value(cdt, cdn, "uom", (response.message && response.message.stock_uom) || "");
		});
	},
});

frappe.ui.form.on("Compound", {
	setup(frm) {
		set_compound_item_queries(frm);
	},

	refresh(frm) {
		set_compound_item_queries(frm);
	},
});

function set_compound_item_queries(frm) {
	frm.set_query("compound_name", () => ({
		filters: {
			item_group: "Compounds",
		},
	}));

	frm.set_query("additive", "additives", () => ({
		filters: {
			item_group: "Additives",
		},
	}));
}
