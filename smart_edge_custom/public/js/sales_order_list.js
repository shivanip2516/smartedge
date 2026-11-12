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
				const messages = results.map((row) => {
					if (row.error) return frappe.utils.escape_html(row.error);
					const item = frappe.utils.escape_html(row.finished_item || __("Finished Item"));
					const quantity = frappe.utils.escape_html(`${row.quantity || 0} ${row.stock_uom || ""}`);
					const sales_orders = frappe.utils.escape_html((row.sales_orders || []).join(", "));
					const link = row.name
						? `<a href="/app/final-eb-bom-generator/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a>`
						: "";
					return `${item}: ${quantity} (${sales_orders})${link ? ` — ${link}` : ""}`;
				});
				frappe.msgprint({
					title: __("Generate FG & BOM Results"),
					indicator: errors.length ? "orange" : "green",
					message: messages.join("<br>") || __("No generators were created."),
				});
				listview.refresh();
			} finally { button.prop("disabled", false); }
		},
	});
	dialog.show();
	let preview_request_id = 0;
	const update_primary_state = () => dialog.get_primary_btn().prop("disabled", dialog.$wrapper.find("input.se-fg-bom-select:checked").length === 0);
	const refresh_consolidated_preview = async () => {
		const request_id = ++preview_request_id;
		const requirements = Array.from(dialog.$wrapper.find("input.se-fg-bom-select:checked")).map((input) => JSON.parse(input.value));
		const preview = dialog.fields_dict.requirements.$wrapper.find(".se-consolidated-preview");
		if (!requirements.length) {
			preview.html(`<div class="text-muted small">${__("Select one or more rows to preview consolidated generators.")}</div>`);
			return;
		}
		preview.html(`<div class="text-muted small">${__("Calculating consolidated generators...")}</div>`);
		try {
			const response = await frappe.call({
				method: "smart_edge_custom.manufacturing.sales_order_list_generation.preview_fg_and_bom",
				args: { requirements },
			});
			if (request_id !== preview_request_id) return;
			const groups = response.message || [];
			if (!groups.length) {
				preview.html(`<div class="text-muted small">${__("No consolidatable Finished Goods requirements remain.")}</div>`);
				return;
			}
			const rows = groups.map((group) => {
				const item = frappe.utils.escape_html(group.finished_item_name || group.finished_item || "");
				const code = group.finished_item_name && group.finished_item !== group.finished_item_name
					? ` <span class="text-muted">(${frappe.utils.escape_html(group.finished_item)})</span>`
					: "";
				const orders = frappe.utils.escape_html((group.sales_orders || []).join(", "));
				const quantity = frappe.utils.escape_html(`${group.total_production_quantity || 0} ${group.production_uom || ""}`);
				return `<tr><td>${item}${code}</td><td>${frappe.utils.escape_html(group.sales_order_count || 0)}</td><td>${orders}</td><td class="text-right">${quantity}</td></tr>`;
			}).join("");
			preview.html(`<div class="small font-weight-bold mb-1">${__("Consolidated generator preview")}</div><div class="table-responsive"><table class="table table-bordered table-sm mb-0"><thead><tr><th>${__("Finished Item")}</th><th>${__("Sales Orders")}</th><th>${__("Sources")}</th><th class="text-right">${__("Production Quantity")}</th></tr></thead><tbody>${rows}</tbody></table></div>`);
		} catch (error) {
			if (request_id === preview_request_id) preview.html(`<div class="text-danger small">${frappe.utils.escape_html(error.message || __("Unable to calculate the consolidation preview."))}</div>`);
		}
	};
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
		dialog.fields_dict.requirements.$wrapper.html(`<div class="mb-2"><button class="btn btn-xs btn-default se-select-all">${__("Select All")}</button> <button class="btn btn-xs btn-default se-deselect-all">${__("Deselect All")}</button> <span class="text-muted se-selected-count">0 selected</span></div><table class="table table-bordered"><thead><tr><th>${__("Select")}</th><th>${__("Sales Order")}</th><th>${__("Customer Name")}</th><th>${__("FG Item")}</th><th>${__("Required Production Qty")}</th></tr></thead><tbody>${body}</tbody></table><div class="se-consolidated-preview mt-3"></div>`);
		const refresh_count = () => {
			dialog.fields_dict.requirements.$wrapper.find(".se-selected-count").text(`${dialog.$wrapper.find("input.se-fg-bom-select:checked").length} selected`);
			update_primary_state();
			refresh_consolidated_preview();
		};
		dialog.fields_dict.requirements.$wrapper.on("change", "input.se-fg-bom-select", refresh_count).on("click", ".se-select-all", () => { dialog.$wrapper.find("input.se-fg-bom-select").prop("checked", true); refresh_count(); }).on("click", ".se-deselect-all", () => { dialog.$wrapper.find("input.se-fg-bom-select").prop("checked", false); refresh_count(); });
		refresh_count();
	} catch (error) {
		dialog.fields_dict.requirements.$wrapper.html(`<div class="text-danger">${frappe.utils.escape_html(error.message || __("Unable to load pending requirements."))}</div>`);
	}
}
