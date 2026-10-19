frappe.ui.form.on("Work Order", {
	refresh(frm) {
		refresh_batch_manufacturing(frm, false);
	},

	production_item(frm) {
		return refresh_batch_manufacturing(frm, true);
	},

	bom_no(frm) {
		return refresh_batch_manufacturing(frm, true);
	},

	custom_no_of_batches(frm) {
		set_total_manufacturing_quantity(frm);
	},
});

async function refresh_batch_manufacturing(frm, initialize) {
	if (!frm.doc.production_item || !frm.doc.bom_no) {
		set_batch_manufacturing_fields(frm, false);
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details",
		args: {
			item_code: frm.doc.production_item,
			bom_no: frm.doc.bom_no,
		},
	});
	const details = response.message || {};
	set_batch_manufacturing_fields(frm, Boolean(details.applicable), details.stock_uom);

	if (!details.applicable || frm.doc.docstatus !== 0) {
		return;
	}

	// A refresh of an older draft must not silently replace its quantity.
	if (!initialize && !flt(frm.doc.custom_weight_per_batch)) {
		return;
	}

	await frm.set_value("custom_weight_per_batch", flt(details.weight_per_batch));
	if (cint(frm.doc.custom_no_of_batches) < 1) {
		await frm.set_value("custom_no_of_batches", 1);
	}
	await set_total_manufacturing_quantity(frm);
}

function set_batch_manufacturing_fields(frm, enabled, stock_uom) {
	frm.toggle_display("custom_no_of_batches", enabled);
	frm.toggle_display("custom_weight_per_batch", enabled);
	frm.toggle_reqd("custom_no_of_batches", enabled);
	frm.set_df_property("qty", "read_only", enabled ? 1 : 0);
	frm.set_df_property(
		"qty",
		"label",
		enabled && stock_uom
			? __("Total Manufacturing Quantity ({0})", [stock_uom])
			: __("Qty To Manufacture")
	);

	const label = stock_uom
		? __("Weight per Batch ({0})", [stock_uom])
		: __("Weight per Batch");
	frm.set_df_property("custom_weight_per_batch", "label", label);
}

async function set_total_manufacturing_quantity(frm) {
	const batches = cint(frm.doc.custom_no_of_batches);
	const weight = flt(frm.doc.custom_weight_per_batch);
	if (frm.doc.docstatus !== 0 || batches < 1 || weight <= 0) {
		return;
	}

	await frm.set_value("qty", flt(batches * weight, precision("qty", frm.doc)));
}
