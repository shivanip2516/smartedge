import frappe


PRODUCT_TYPE_VALUES = ("Solid", "Wood Grain", "Texture", "Other")


def execute():
	seed_product_types()
	migrate_shade_to_shade_name()
	seed_shades_from_items()
	seed_type_finishes_from_items()
	delete_old_shade_field()


def seed_product_types():
	for material_type_name in PRODUCT_TYPE_VALUES:
		if frappe.db.exists("Material Type", material_type_name):
			continue

		doc = frappe.new_doc("Material Type")
		doc.material_type_name = material_type_name
		doc.insert(ignore_permissions=True)


def migrate_shade_to_shade_name():
	if not frappe.db.has_column("Item", "shade") or not frappe.db.has_column(
		"Item", "custom_shade_name"
	):
		return

	frappe.db.sql(
		"""
		update `tabItem`
		set custom_shade_name = shade
		where ifnull(custom_shade_name, '') = ''
			and ifnull(shade, '') != ''
		"""
	)


def seed_shades_from_items():
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
		if frappe.db.exists("Shade", row.custom_shade_name):
			continue

		doc = frappe.new_doc("Shade")
		doc.shade_name = row.custom_shade_name
		doc.insert(ignore_permissions=True)


def seed_type_finishes_from_items():
	if not frappe.db.has_column("Item", "custom_type_finish"):
		return

	for row in frappe.db.sql(
		"""
		select custom_type_finish, max(custom_type_short_code) as custom_type_short_code
		from `tabItem`
		where ifnull(custom_type_finish, '') != ''
		group by custom_type_finish
		""",
		as_dict=True,
	):
		type_finish = row.custom_type_finish
		type_short_code = row.custom_type_short_code or type_finish

		if frappe.db.exists("PVC Type Finish", type_finish):
			if not frappe.db.get_value("PVC Type Finish", type_finish, "abbreviation"):
				frappe.db.set_value("PVC Type Finish", type_finish, "abbreviation", type_short_code)
			continue

		doc = frappe.new_doc("PVC Type Finish")
		doc.type_finish = type_finish
		doc.abbreviation = type_short_code
		doc.insert(ignore_permissions=True)


def delete_old_shade_field():
	if not frappe.db.exists("Custom Field", "Item-shade"):
		return

	frappe.delete_doc(
		"Custom Field",
		"Item-shade",
		ignore_permissions=True,
		force=True,
	)
