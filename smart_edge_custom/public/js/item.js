frappe.ui.form.on("Item", {
	custom_type_finish(frm) {
		if (!frm.doc.custom_type_finish) {
			frm.set_value("custom_type_short_code", "");
			return;
		}

		frappe.db
			.get_value("PVC Type Finish", frm.doc.custom_type_finish, "abbreviation")
			.then((response) => {
				frm.set_value(
					"custom_type_short_code",
					(response.message && response.message.abbreviation) || ""
				);
			});
	},
});
