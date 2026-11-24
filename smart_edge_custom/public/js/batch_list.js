(() => {
	const settings = frappe.listview_settings.Batch || {};
	const originalIndicator = settings.get_indicator;
	settings.add_fields = [...new Set([...(settings.add_fields || []),
		"custom_operation_name", "custom_operation_output_quantity",
		"reference_doctype", "reference_name", "item_name"] )];
	const operation_quantity = frappe.meta?.get_docfield?.("Batch", "custom_operation_output_quantity");
	if (operation_quantity) operation_quantity.label = __("Operation Quantity (Meter)");
	settings.get_indicator = (doc) => originalIndicator?.(doc);
	frappe.listview_settings.Batch = settings;
})();
