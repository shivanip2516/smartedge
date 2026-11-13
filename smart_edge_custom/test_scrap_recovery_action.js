const assert = require("node:assert/strict");

global.__ = (value) => value;

let handlers;
const calls = [];
const alerts = [];
const messages = [];

global.frappe = {
	ui: {
		form: {
			on(doctype, form_handlers) {
				assert.equal(doctype, "SmartEdge Scrap Recovery");
				handlers = form_handlers;
			},
		},
	},
	call(args) {
		calls.push(args);
		return Promise.resolve({ message: { stock_entry: "STE-1" } });
	},
	show_alert(value) {
		alerts.push(value);
	},
	msgprint(value) {
		messages.push(value);
	},
};

require("./smart_edge_custom/doctype/smartedge_scrap_recovery/smartedge_scrap_recovery.js");

function make_form(status, dirty = false) {
	const buttons = new Map();
	let save_count = 0;
	const form = {
		doc: { name: "REC-00001", status },
		is_new: () => false,
		is_dirty: () => dirty,
		save: async () => {
			save_count += 1;
			dirty = false;
		},
		reload_doc: async () => {},
		add_custom_button(label, action, group) {
			buttons.set(label, { action, group });
		},
	};
	return { form, buttons, get save_count() { return save_count; } };
}

async function run() {
	let fixture = make_form("Draft");
	handlers.refresh(fixture.form);
	const grinder = fixture.buttons.get("Complete Grinder Processing");
	assert.equal(grinder.group, "Recovery");
	await grinder.action();
	assert.equal(fixture.save_count, 0);
	assert.equal(calls[0].method, "smart_edge_custom.manufacturing.scrap_recovery.complete_grinder");
	assert.equal(alerts.at(-1).message, "Grinding completed successfully.");

	fixture = make_form("Draft", true);
	handlers.refresh(fixture.form);
	await fixture.buttons.get("Complete Grinder Processing").action();
	assert.equal(fixture.save_count, 1);
	assert.equal(calls[1].method, "smart_edge_custom.manufacturing.scrap_recovery.complete_grinder");

	fixture = make_form("Ground");
	handlers.refresh(fixture.form);
	await fixture.buttons.get("Complete Pulverizer Processing").action();
	assert.equal(calls[2].method, "smart_edge_custom.manufacturing.scrap_recovery.complete_pulverizer");

	let resolve_call;
	global.frappe.call = () => new Promise((resolve) => {
		resolve_call = resolve;
	});
	fixture = make_form("Draft");
	handlers.refresh(fixture.form);
	const first = fixture.buttons.get("Complete Grinder Processing").action();
	const second = fixture.buttons.get("Complete Grinder Processing").action();
	assert.equal(fixture.form.__smartedge_recovery_action_running, true);
	resolve_call({ message: {} });
	await Promise.all([first, second]);
	assert.equal(calls.length, 3);
	assert.equal(messages.length, 0);

	global.frappe.call = () => Promise.reject(new Error("Insufficient usable scrap stock"));
	fixture = make_form("Draft");
	handlers.refresh(fixture.form);
	await fixture.buttons.get("Complete Grinder Processing").action();
	assert.equal(messages.at(-1).message, "Insufficient usable scrap stock");
}

run().then(() => console.log("Scrap recovery action tests passed"));
