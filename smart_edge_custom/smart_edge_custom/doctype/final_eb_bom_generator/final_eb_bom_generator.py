import frappe
from frappe.model.document import Document

from smart_edge_custom.manufacturing.final_bom_generator import (
	apply_item_configuration_defaults,
	apply_process_defaults,
	apply_preview_to_doc,
	calculate_bom_preview,
	send_generator_to_planning,
)


class FinalEBBOMGenerator(Document):
	def before_insert(self):
		if not self.company:
			self.company = (
				frappe.defaults.get_user_default("Company")
				or frappe.db.get_single_value("Global Defaults", "default_company")
			)

	def validate(self):
		apply_item_configuration_defaults(self)
		apply_process_defaults(self)
		if self.finished_item and not self.source_warehouse:
			self.source_warehouse = get_default_source_warehouse(self.finished_item, self.company)

		if self.finished_item and self.production_quantity:
			preview = calculate_bom_preview(self.as_dict())
			apply_preview_to_doc(self, preview)

	@frappe.whitelist()
	def preview_bom(self):
		preview = calculate_bom_preview(self.as_dict())
		apply_preview_to_doc(self, preview)
		return preview

	@frappe.whitelist()
	def send_to_planning(self):
		return send_generator_to_planning(self)


def get_default_source_warehouse(item_code, company=None):
	if not item_code:
		return None

	filters = {"parent": item_code}
	if company:
		filters["company"] = company

	return frappe.db.get_value("Item Default", filters, "default_warehouse")
