const assert = require("node:assert/strict");

let workstation_handlers;
const routes = [];
const new_documents = [];

global.__ = (value) => value;
global.frappe = {
	ui: {
		form: {
			on(doctype, handlers) {
				assert.equal(doctype, "Workstation");
				workstation_handlers = handlers;
			},
		},
	},
	model: {
		can_read: () => true,
		can_create: () => true,
	},
	set_route: (...args) => routes.push(args),
	new_doc: (...args) => new_documents.push(args),
};

require("./public/js/workstation.js");

const buttons = new Map();
const transactions = [];
const frm = {
	doc: { name: "Extruder-1" },
	is_new: () => false,
	dashboard: {
		add_transactions: (value) => transactions.push(...value),
	},
	add_custom_button(label, action, group) {
		buttons.set(label, { action, group });
	},
};

workstation_handlers.refresh(frm);

assert.deepEqual(transactions, [{
	label: "Machine Capacity Configuration",
	items: ["SmartEdge Workstation Capacity Rate"],
}]);
assert.equal(buttons.get("View Capacity Rates").group, "Machine Capacity Configuration");
assert.equal(buttons.get("Add Capacity Rate").group, "Machine Capacity Configuration");

buttons.get("View Capacity Rates").action();
assert.deepEqual(frappe.route_options, { workstation: "Extruder-1" });
assert.deepEqual(routes, [["List", "SmartEdge Workstation Capacity Rate"]]);

buttons.get("Add Capacity Rate").action();
assert.deepEqual(new_documents, [[
	"SmartEdge Workstation Capacity Rate",
	{ workstation: "Extruder-1" },
]]);

const inaccessible_buttons = [];
frappe.model.can_read = () => false;
workstation_handlers.refresh({
	doc: { name: "Extruder-2" },
	is_new: () => false,
	dashboard: { add_transactions: () => assert.fail("must not add inaccessible links") },
	add_custom_button: (...args) => inaccessible_buttons.push(args),
});
assert.equal(inaccessible_buttons.length, 0);

console.log("Workstation capacity navigation tests passed");
