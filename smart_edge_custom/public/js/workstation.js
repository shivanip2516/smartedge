const SMARTEDGE_CAPACITY_RATE_DOCTYPE = "SmartEdge Workstation Capacity Rate";

frappe.ui.form.on("Workstation", {
	refresh(frm) {
		setup_workstation_capacity_actions(frm);
	},
});

function setup_workstation_capacity_actions(frm) {
	if (frm.is_new() || !frappe.model.can_read(SMARTEDGE_CAPACITY_RATE_DOCTYPE)) {
		return;
	}

	frm.dashboard.add_transactions([
		{
			label: __("Machine Capacity Configuration"),
			items: [SMARTEDGE_CAPACITY_RATE_DOCTYPE],
		},
	]);

	frm.add_custom_button(
		__("View Capacity Rates"),
		() => {
			frappe.route_options = { workstation: frm.doc.name };
			frappe.set_route("List", SMARTEDGE_CAPACITY_RATE_DOCTYPE);
		},
		__("Machine Capacity Configuration")
	);

	if (frappe.model.can_create(SMARTEDGE_CAPACITY_RATE_DOCTYPE)) {
		frm.add_custom_button(
			__("Add Capacity Rate"),
			async () => {
				if (!frappe.db?.get_list) {
					frappe.new_doc(SMARTEDGE_CAPACITY_RATE_DOCTYPE, { workstation: frm.doc.name });
					return;
				}
			const rows = await frappe.db.get_list(SMARTEDGE_CAPACITY_RATE_DOCTYPE, {
				filters: { workstation: frm.doc.name }, fields: ["name"], limit: 2,
			});
			if (rows.length === 1) {
				frappe.set_route("Form", SMARTEDGE_CAPACITY_RATE_DOCTYPE, rows[0].name);
				return;
			}
			if (!rows.length && frappe.model.can_create(SMARTEDGE_CAPACITY_RATE_DOCTYPE)) {
				frappe.new_doc(SMARTEDGE_CAPACITY_RATE_DOCTYPE, { workstation: frm.doc.name });
				return;
			}
			frappe.route_options = { workstation: frm.doc.name };
			frappe.set_route("List", SMARTEDGE_CAPACITY_RATE_DOCTYPE);
		},
		__("Machine Capacity Configuration")
	);
	}
}

if (typeof module !== "undefined" && module.exports) {
	module.exports = { setup_workstation_capacity_actions };
}
