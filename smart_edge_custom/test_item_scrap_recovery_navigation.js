const assert = require("node:assert/strict");

global.__ = (value) => value;
global.frappe = {
	ui: { form: { on: () => {} } },
	model: { can_read: () => true, can_create: () => true },
	db: { get_value: () => Promise.resolve({ message: {} }) },
	call: () => Promise.resolve({ message: null }),
	set_route: (...args) => routes.push(args),
	new_doc: (...args) => new_documents.push(args),
};

const routes = [];
const new_documents = [];
const { setup_item_scrap_recovery_actions } = require("./public/js/item.js");

const buttons = new Map();
const transactions = [];
const frm = {
	doc: { name: "IT-000002" },
	is_new: () => false,
	dashboard: { add_transactions: (value) => transactions.push(...value) },
	add_custom_button(label, action, group) {
		buttons.set(label, { action, group });
	},
};

setup_item_scrap_recovery_actions(frm);

assert.deepEqual(transactions, [{
	label: "Scrap & Recovery Configuration",
	items: ["SmartEdge Recovery Material"],
}]);
assert.equal(buttons.get("View Scrap Mapping").group, "Scrap & Recovery Configuration");
assert.equal(buttons.get("Add Scrap Mapping").group, "Scrap & Recovery Configuration");

buttons.get("View Scrap Mapping").action();
assert.deepEqual(frappe.route_options, { source_item: "IT-000002" });
assert.deepEqual(routes, [["List", "SmartEdge Recovery Material"]]);

buttons.get("Add Scrap Mapping").action();
assert.deepEqual(new_documents, [["SmartEdge Recovery Material", { source_item: "IT-000002" }]]);

frappe.model.can_read = () => false;
setup_item_scrap_recovery_actions({
	doc: { name: "IT-000003" },
	is_new: () => false,
	dashboard: { add_transactions: () => assert.fail("must not expose inaccessible configuration") },
	add_custom_button: () => assert.fail("must not expose inaccessible actions"),
});

console.log("Item scrap recovery navigation tests passed");
