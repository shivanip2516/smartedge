frappe.ui.form.on("Item", {
	refresh(frm) {
		configure_customer_order_code_field(frm);
		update_customer_order_code(frm, { silent: true });
	},

	custom_color(frm) {
		update_customer_order_code(frm);
	},

	custom_type_finish(frm) {
		if (!frm.doc.custom_type_finish) {
			frm.set_value("custom_type_short_code", "");
			update_customer_order_code(frm);
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

		update_customer_order_code(frm);
	},
});

function configure_customer_order_code_field(frm) {
	frm.set_df_property("product_code", "label", __("Customer Order Code"));
	frm.set_df_property("product_code", "read_only", 1);

	if (frm.fields_dict.custom_customer_order_code) {
		frm.set_df_property("custom_customer_order_code", "hidden", 1);
		frm.set_df_property("custom_customer_order_code", "reqd", 0);
	}

	for (const fieldname of ["custom_base_color", "custom_base_colour"]) {
		if (frm.fields_dict[fieldname]) {
			frm.set_df_property(fieldname, "hidden", 1);
			frm.set_df_property(fieldname, "reqd", 0);
		}
	}
}

async function update_customer_order_code(frm, opts = {}) {
	const color = frm.doc.custom_color;
	const type_finish = frm.doc.custom_type_finish;

	if (!color || !type_finish) {
		await set_customer_order_code(frm, "");
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.item.get_customer_order_code",
		args: {
			color,
			type_finish,
		},
	});

	const order_code = response.message || "";
	await set_customer_order_code(frm, order_code);

	if (!order_code && !opts.silent) {
		frappe.show_alert({
			message: __("No Customer Order Code found for the selected Color and Type / Finish."),
			indicator: "orange",
		});
	}
}

async function set_customer_order_code(frm, order_code) {
	if ((frm.doc.product_code || "") === order_code) {
		return;
	}

	await frm.set_value("product_code", order_code);
}
