const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const context = vm.createContext({
	__: (value) => value,
	frappe: { listview_settings: {}, get_meta: () => ({ states: [] }),
		workflow: { workflows: {}, avoid_status_override: {}, get_state_fieldname: () => null },
		model: { is_submittable: () => false }, meta: { has_field: (_dt, field) => field === "disabled" } },
});
vm.runInContext(fs.readFileSync(require.resolve("../../frappe/frappe/public/js/frappe/model/indicator.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(require.resolve("../../erpnext/erpnext/stock/doctype/batch/batch_list.js"), "utf8"), context);
const original = context.frappe.listview_settings.Batch;
const active = JSON.stringify(original.get_indicator({ batch_qty: 10 }));
vm.runInContext(fs.readFileSync(require.resolve("./public/js/batch_list.js"), "utf8"), context);
const settings = context.frappe.listview_settings.Batch;
assert.equal(JSON.stringify(settings.get_indicator({ batch_qty: 10 })), active);
assert.equal(settings.get_indicator({ disabled: 1 })[0], "Disabled");
assert.equal(settings.get_indicator({ disabled: 1, batch_qty: 0, custom_batch_purpose: "Operation Traceability" })[0], "Completed — Traceability");
assert.ok(settings.add_fields.includes("custom_operation_name"));
assert.ok(settings.add_fields.includes("reference_name"));
assert.ok(settings.add_fields.includes("item_name"));
assert.equal(settings.filters, undefined);
assert.ok(settings.add_fields.includes("batch_qty"));
assert.equal(context.frappe.get_indicator({ disabled: 1, custom_batch_purpose: "Operation Traceability" }, "Batch")[0], "Completed — Traceability");
assert.equal(context.frappe.get_indicator({ disabled: 1 }, "Batch")[0], "Disabled");
assert.equal(context.frappe.get_indicator({ batch_qty: 10 }, "Batch")[0], "Active");
assert.equal(settings.get_indicator({ disabled: 0, batch_qty: 0, custom_batch_purpose: "Operation Traceability" })[0], "Completed — Traceability");
console.log("Standard Batch List: trace indicator, physical indicators, Item/job fields and default filters verified");
