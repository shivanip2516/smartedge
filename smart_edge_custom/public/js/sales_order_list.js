frappe.listview_settings["Sales Order"] = {
	onload(listview) {
		if (!frappe.user.has_role("Manufacturing User") && !frappe.user.has_role("Manufacturing Manager") && !frappe.user.has_role("System Manager")) return;
		listview.page.add_inner_button(__("Generate FG & BOM"), () => open_generate_fg_bom_dialog(listview));
	},
};

async function open_generate_fg_bom_dialog(listview) {
	const selected = (listview.get_checked_items() || []).map((row) => row.name);
	const dialog = new frappe.ui.Dialog({
		title: __("Generate FG & BOM"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "requirements" }],
		primary_action_label: __("Generate FG & BOM"),
		primary_action: async () => {
			const requirements = Array.from(dialog.$wrapper.find("input.se-fg-bom-select:checked")).map((input) => JSON.parse(input.value));
			if (!requirements.length) return frappe.msgprint(__("Select at least one pending Sales Order item."));
			const button = dialog.get_primary_btn();
			button.prop("disabled", true);
			try {
				const response = await frappe.call({ method: "smart_edge_custom.manufacturing.sales_order_list_generation.generate_fg_and_bom", args: { requirements } });
				const results = response.message || [];
				const errors = results.filter((row) => row.error);
				dialog.hide();
				frappe.msgprint({ title: __("Generate FG & BOM Results"), indicator: errors.length ? "orange" : "green", message: results.map((row) => frappe.utils.escape_html(row.error || `${row.sales_order}: ${row.message}`)).join("<br>") });
				listview.refresh();
			} finally { button.prop("disabled", false); }
		},
	});
	dialog.show();
	const update_primary_state = () => dialog.get_primary_btn().prop("disabled", dialog.$wrapper.find("input.se-fg-bom-select:checked").length === 0);
	update_primary_state();
	dialog.fields_dict.requirements.$wrapper.html(`<div class="text-muted">${__("Loading pending requirements...")}</div>`);
	try {
		const response = await frappe.call({ method: "smart_edge_custom.manufacturing.sales_order_list_generation.get_pending_fg_requirements", args: { sales_orders: selected } });
		const rows = response.message || [];
		if (!rows.length) {
			dialog.fields_dict.requirements.$wrapper.html(`<div class="text-muted">${__("No pending Finished Goods requirements.")}</div>`);
			return;
		}
		const body = rows.map((row) => `<tr><td><input class="se-fg-bom-select" type="checkbox" value='${frappe.utils.escape_html(JSON.stringify({ sales_order: row.sales_order, sales_order_item: row.sales_order_item }))}'></td><td>${frappe.utils.escape_html(row.sales_order)}</td><td>${frappe.utils.escape_html(row.customer_name || row.customer)}</td><td>${frappe.utils.escape_html(row.item_name || row.item_code)}</td><td>${frappe.utils.escape_html(`${row.pending_qty} ${row.uom || ""}`)}</td></tr>`).join("");
		dialog.fields_dict.requirements.$wrapper.html(`<div class="mb-2"><button class="btn btn-xs btn-default se-select-all">${__("Select All")}</button> <button class="btn btn-xs btn-default se-deselect-all">${__("Deselect All")}</button> <span class="text-muted se-selected-count">0 selected</span></div><table class="table table-bordered"><thead><tr><th>${__("Select")}</th><th>${__("Sales Order")}</th><th>${__("Customer Name")}</th><th>${__("FG Item")}</th><th>${__("Required Production Qty")}</th></tr></thead><tbody>${body}</tbody></table>`);
		const refresh_count = () => { dialog.fields_dict.requirements.$wrapper.find(".se-selected-count").text(`${dialog.$wrapper.find("input.se-fg-bom-select:checked").length} selected`); update_primary_state(); };
		dialog.fields_dict.requirements.$wrapper.on("change", "input.se-fg-bom-select", refresh_count).on("click", ".se-select-all", () => { dialog.$wrapper.find("input.se-fg-bom-select").prop("checked", true); refresh_count(); }).on("click", ".se-deselect-all", () => { dialog.$wrapper.find("input.se-fg-bom-select").prop("checked", false); refresh_count(); });
	} catch (error) {
		dialog.fields_dict.requirements.$wrapper.html(`<div class="text-danger">${frappe.utils.escape_html(error.message || __("Unable to load pending requirements."))}</div>`);
	}
}
