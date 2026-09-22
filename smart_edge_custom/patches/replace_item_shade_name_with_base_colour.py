import frappe


def execute():
	if not frappe.db.exists("DocType", "Base Colour"):
		return

	ensure_base_colour_custom_field()
	create_base_colours_from_existing_shades()
	copy_item_shade_name_to_base_colour()
	delete_shade_name_custom_field()
	update_item_field_order()
	frappe.clear_cache(doctype="Item")


def ensure_base_colour_custom_field():
	if frappe.db.exists("Custom Field", "Item-custom_base_colour"):
		frappe.db.set_value(
			"Custom Field",
			"Item-custom_base_colour",
			{
				"fieldtype": "Link",
				"label": "Base Colour",
				"insert_after": "custom_size",
				"options": "Base Colour",
				"reqd": 1,
				"allow_in_quick_entry": 1,
				"in_global_search": 1,
				"in_list_view": 1,
				"in_standard_filter": 1,
				"module": "smart_edge_custom",
			},
		)
		return

	doc = frappe.get_doc(
		{
			"doctype": "Custom Field",
			"name": "Item-custom_base_colour",
			"dt": "Item",
			"fieldname": "custom_base_colour",
			"fieldtype": "Link",
			"label": "Base Colour",
			"insert_after": "custom_size",
			"options": "Base Colour",
			"reqd": 1,
			"allow_in_quick_entry": 1,
			"in_global_search": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"module": "smart_edge_custom",
		}
	)
	doc.insert(ignore_permissions=True)


def create_base_colours_from_existing_shades():
	if not frappe.db.has_column("Item", "custom_shade_name"):
		return

	for row in frappe.db.sql(
		"""
		select distinct custom_shade_name
		from `tabItem`
		where ifnull(custom_shade_name, '') != ''
		""",
		as_dict=True,
	):
		if frappe.db.exists("Base Colour", row.custom_shade_name):
			continue

		doc = frappe.new_doc("Base Colour")
		doc.colour_name = row.custom_shade_name
		doc.weightage = 5
		doc.insert(ignore_permissions=True)


def copy_item_shade_name_to_base_colour():
	if not (
		frappe.db.has_column("Item", "custom_shade_name")
		and frappe.db.has_column("Item", "custom_base_colour")
	):
		return

	frappe.db.sql(
		"""
		update `tabItem`
		set custom_base_colour = custom_shade_name
		where ifnull(custom_base_colour, '') = ''
			and ifnull(custom_shade_name, '') != ''
		"""
	)


def delete_shade_name_custom_field():
	if not frappe.db.exists("Custom Field", "Item-custom_shade_name"):
		return

	frappe.delete_doc(
		"Custom Field",
		"Item-custom_shade_name",
		ignore_permissions=True,
		force=True,
	)


def update_item_field_order():
	if frappe.db.exists("Custom Field", "Item-custom_product_category"):
		frappe.db.set_value(
			"Custom Field",
			"Item-custom_product_category",
			"insert_after",
			"custom_base_colour",
		)
