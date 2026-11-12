frappe.ui.form.on("Item", {
	onload(frm) {
		initialize_item_name_sync(frm);
		refresh_item_group_rules(frm);
	},

	refresh(frm) {
		initialize_item_name_sync(frm);
		configure_customer_order_code_field(frm);
		refresh_item_group_rules(frm);
		refresh_item_name(frm);
		setup_item_scrap_recovery_actions(frm);
	},

	item_group(frm) {
		refresh_item_group_rules(frm);
	},

	custom_size(frm) {
		update_item_name(frm);
	},

	custom_color(frm) {
		update_customer_order_code(frm);
	},

	async custom_type_finish(frm) {
		if (!(await update_type_short_code(frm))) {
			return;
		}

		await update_customer_order_code(frm);
	},

	custom_type_short_code(frm) {
		update_item_name(frm);
	},

	custom_product_category(frm) {
		update_item_name(frm);
	},

	custom_product_type_design_category(frm) {
		update_item_name(frm);
	},

	product_code(frm) {
		update_item_name(frm);
	},
});

const PVC_EDGE_BAND_NAME_PATTERN = /^.+ EB .+ .+(?: \(.+\))?$/;
const PVC_EDGE_BAND_NAME_FIELDS = [
	"custom_size",
	"custom_color",
	"custom_type_short_code",
	"product_code",
];
const PVC_EDGE_BAND_REQUIRED_NAME_FIELDS = PVC_EDGE_BAND_NAME_FIELDS.slice(0, -1);
const PVC_EDGE_BAND_NAME_SOURCE_FIELDS = [...PVC_EDGE_BAND_NAME_FIELDS, "item_group"];

function initialize_item_name_sync(frm) {
	if (frm.__smartedge_item_name_sync_state) {
		return;
	}

	frm.__smartedge_item_name_sync_state = {
		item_name: frm.doc.item_name || "",
		values: Object.fromEntries(
			PVC_EDGE_BAND_NAME_SOURCE_FIELDS.map((fieldname) => [fieldname, frm.doc[fieldname] || ""])
		),
	};
}

async function refresh_item_name(frm) {
	if (!(await update_type_short_code(frm))) {
		return;
	}

	await update_customer_order_code(frm, { silent: true });
}

async function update_type_short_code(frm) {
	if (!frm.doc.custom_type_finish) {
		if (frm.doc.custom_type_short_code) {
			await frm.set_value("custom_type_short_code", "");
		}
		return true;
	}

	const type_finish = frm.doc.custom_type_finish;
	const response = await frappe.db.get_value("PVC Type Finish", type_finish, "abbreviation");
	if (frm.doc.custom_type_finish !== type_finish) {
		return false;
	}

	const short_code = (response.message && response.message.abbreviation) || "";
	if (frm.doc.custom_type_short_code !== short_code) {
		await frm.set_value("custom_type_short_code", short_code);
	}
	return frm.doc.custom_type_finish === type_finish;
}

const SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE = "SmartEdge Recovery Material";

function setup_item_scrap_recovery_actions(frm) {
	if (frm.is_new() || !frappe.model.can_read(SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE)) {
		return;
	}

	frm.dashboard.add_transactions([
		{
			label: __("Scrap & Recovery Configuration"),
			items: [SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE],
		},
	]);

	frm.add_custom_button(
		__("View Scrap Mapping"),
		() => {
			frappe.route_options = { source_item: frm.doc.name };
			frappe.set_route("List", SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE);
		},
		__("Scrap & Recovery Configuration")
	);

	if (frappe.model.can_create(SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE)) {
		frm.add_custom_button(
			__("Add Scrap Mapping"),
			() => frappe.new_doc(SMARTEDGE_RECOVERY_MATERIAL_DOCTYPE, { source_item: frm.doc.name }),
			__("Scrap & Recovery Configuration")
		);
	}
}

function configure_customer_order_code_field(frm) {
	frm.set_df_property("product_code", "label", __("Customer Order Code"));
	frm.set_df_property("product_code", "read_only", 1);

	if (frm.fields_dict.custom_customer_order_code) {
		frm.set_df_property("custom_customer_order_code", "hidden", 1);
		frm.set_df_property("custom_customer_order_code", "reqd", 0);
	}

}

const SMARTEDGE_EXCLUDED_ITEM_GROUPS = new Set([
	"Additives",
	"Compounds",
	"Compound",
	"Pigments",
	"Base Colour",
	"Base Color",
]);
const DYNAMIC_MANDATORY_FIELDS = [
	"custom_size",
	"custom_color",
	"custom_product_category",
	"custom_product_type_design_category",
	"custom_type_finish",
	"gst_hsn_code",
];

function apply_dynamic_mandatory_fields(frm, mandatory) {
	for (const fieldname of DYNAMIC_MANDATORY_FIELDS) {
		if (!frm.fields_dict[fieldname]) {
			continue;
		}

		frm.set_df_property(fieldname, "reqd", mandatory ? 1 : 0);
	}
}

function refresh_item_group_rules(frm) {
	const item_group = frm.doc.item_group;
	const request_id = (frm.__smartedge_item_group_request_id || 0) + 1;
	frm.__smartedge_item_group_request_id = request_id;

	if (!item_group) {
		frm.__smartedge_item_group_rules = { item_group: null, is_excluded: false, is_pvc_edge_band: false };
		apply_dynamic_mandatory_fields(frm, false);
		return;
	}

	if (frm.__smartedge_item_group_rules?.item_group === item_group) {
		apply_dynamic_mandatory_fields(frm, !frm.__smartedge_item_group_rules.is_excluded);
		update_item_name(frm);
		return;
	}

	frappe.call({
		method: "smart_edge_custom.item.get_item_group_rules",
		args: { item_group },
	}).then((response) => {
		if (request_id !== frm.__smartedge_item_group_request_id || frm.doc.item_group !== item_group) {
			return;
		}

		const rules = response.message || {};
		frm.__smartedge_item_group_rules = { ...rules, item_group };
		apply_dynamic_mandatory_fields(frm, !rules.is_excluded);
		update_item_name(frm);
	}).catch(() => {
		if (request_id !== frm.__smartedge_item_group_request_id || frm.doc.item_group !== item_group) {
			return;
		}

		const is_excluded = SMARTEDGE_EXCLUDED_ITEM_GROUPS.has(item_group);
		frm.__smartedge_item_group_rules = { item_group, is_excluded, is_pvc_edge_band: false };
		apply_dynamic_mandatory_fields(frm, !is_excluded);
	});
}

async function update_customer_order_code(frm, opts = {}) {
	const color = frm.doc.custom_color;
	const type_finish = frm.doc.custom_type_finish;

	if (!color || !type_finish) {
		await set_customer_order_code(frm, "");
		if (frm.doc.custom_color === color && frm.doc.custom_type_finish === type_finish) {
			update_item_name(frm);
		}
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.item.get_customer_order_code",
		args: {
			color,
			type_finish,
		},
	});
	if (frm.doc.custom_color !== color || frm.doc.custom_type_finish !== type_finish) {
		return;
	}

	const order_code = response.message || "";
	await set_customer_order_code(frm, order_code);
	if (frm.doc.custom_color === color && frm.doc.custom_type_finish === type_finish) {
		update_item_name(frm);
	}

	if (!order_code && !opts.silent) {
		frappe.show_alert({
			message: __("No Customer Order Code found for the selected Color and Type / Finish."),
			indicator: "orange",
		});
	}
}

function update_item_name(frm) {
	const generated_item_name = get_generated_item_name(frm);
	if (!generated_item_name || !should_update_item_name(frm)) {
		return;
	}

	if (frm.doc.item_name !== generated_item_name) {
		frm.set_value("item_name", generated_item_name);
	}
}

function get_generated_item_name(frm) {
	if (!frm.__smartedge_item_group_rules?.is_pvc_edge_band) {
		return "";
	}

	if (!frm.doc.custom_type_finish || !frm.doc.custom_size || !frm.doc.custom_color) {
		return "";
	}

	if (!PVC_EDGE_BAND_REQUIRED_NAME_FIELDS.every((fieldname) => frm.doc[fieldname])) {
		return "";
	}

	const generated_item_name = `${frm.doc.custom_size} EB ${frm.doc.custom_color} ${frm.doc.custom_type_short_code}`;
	return frm.doc.product_code ? `${generated_item_name} (${frm.doc.product_code})` : generated_item_name;
}

function should_update_item_name(frm) {
	if (!frm.doc.item_name) {
		return true;
	}

	if (frm.is_new()) {
		return frm.doc.item_name === frm.doc.item_code || PVC_EDGE_BAND_NAME_PATTERN.test(frm.doc.item_name);
	}

	const state = frm.__smartedge_item_name_sync_state;
	if (!state) {
		return false;
	}

	const source_changed = PVC_EDGE_BAND_NAME_SOURCE_FIELDS.some(
		(fieldname) => (frm.doc[fieldname] || "") !== state.values[fieldname]
	);
	if (!source_changed) {
		return false;
	}

	if (frm.doc.item_name === state.item_name) {
		return state.item_name === frm.doc.item_code || PVC_EDGE_BAND_NAME_PATTERN.test(state.item_name);
	}

	return PVC_EDGE_BAND_NAME_PATTERN.test(frm.doc.item_name);
}

async function set_customer_order_code(frm, order_code) {
	if ((frm.doc.product_code || "") === order_code) {
		return;
	}

	await frm.set_value("product_code", order_code);
}

if (typeof module !== "undefined" && module.exports) {
	module.exports = { setup_item_scrap_recovery_actions };
}
