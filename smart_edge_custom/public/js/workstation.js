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
			() => frappe.new_doc(SMARTEDGE_CAPACITY_RATE_DOCTYPE, { workstation: frm.doc.name }),
			__("Machine Capacity Configuration")
		);
	}
}

if (typeof module !== "undefined" && module.exports) {
	module.exports = { setup_workstation_capacity_actions };
}
