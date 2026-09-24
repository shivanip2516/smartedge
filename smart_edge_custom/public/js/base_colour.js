frappe.ui.form.on("Base Colour", {
	refresh(frm) {
		frm.set_intro(__("Name + weightage (1 = lightest, 10 = darkest)."));

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

function validate_base_colour_weightage(weightage) {
	if (!Number.isInteger(Number(weightage)) || Number(weightage) < 1 || Number(weightage) > 10) {
		frappe.throw(__("Weightage must be an integer between 1 and 10."));
	}
}
