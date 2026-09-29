frappe.ui.form.on("Compound Additive", {
	additive(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.additive) {
			frappe.model.set_value(cdt, cdn, "uom", "");
			return;
		}

		frappe.db.get_value("Additive", row.additive, "uom").then((response) => {
			frappe.model.set_value(cdt, cdn, "uom", (response.message && response.message.uom) || "");
		});
	},
});
