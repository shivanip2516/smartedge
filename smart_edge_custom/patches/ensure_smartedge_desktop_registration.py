from frappe.utils.install import auto_generate_icons_and_sidebar


def execute():
	"""Create the app Desktop Icon for sites installed before app registration was added."""
	auto_generate_icons_and_sidebar("smart_edge_custom")
