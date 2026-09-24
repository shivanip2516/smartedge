frappe.provide("smart_edge_custom");

frappe.listview_settings["Additive"] = {
	onload(listview) {
		new smart_edge_custom.AdditiveMaster(listview);
	},
};

frappe.listview_settings["Compound"] = {
	onload(listview) {
		new smart_edge_custom.CompoundMaster(listview);
	},
};

frappe.listview_settings["Size Weight"] = {
	onload(listview) {
		new smart_edge_custom.SizeWeightMaster(listview);
	},
};

smart_edge_custom.format_kg = (value) => `${flt(value || 0, 3).toFixed(3)} kg`;
smart_edge_custom.format_weight = (value) => flt(value || 0, 4).toFixed(4);
smart_edge_custom.escape = (value) => frappe.utils.escape_html(value == null || value === "" ? "" : String(value));

smart_edge_custom.MasterPage = class MasterPage {
	constructor(listview, options) {
		this.listview = listview;
		this.options = options;
		this.search = "";
		this.can_create = frappe.model.can_create ? frappe.model.can_create(options.doctype) : true;
		this.can_write = frappe.model.can_write ? frappe.model.can_write(options.doctype) : true;
		this.can_delete = frappe.model.can_delete ? frappe.model.can_delete(options.doctype) : true;
		this.make();
		this.refresh();
	}

	make() {
		this.listview.page.set_title(__(this.options.title));
		this.listview.page.clear_primary_action();

		this.listview.$result.hide();
		this.listview.page.sidebar && this.listview.page.sidebar.hide();

		const add_button = this.can_create
			? `<button class="btn smart-master-add">${frappe.utils.icon("add", "sm")} ${__(this.options.add_label)}</button>`
			: "";

		this.$wrapper = $(`
			<div class="smart-master ${this.options.class_name}">
				<div class="smart-master-topbar">
					<input class="form-control smart-master-search" type="search" placeholder="${__(
						this.options.search_placeholder
					)}">
					${add_button}
				</div>
				<p class="smart-master-intro">${this.options.intro}</p>
				<div class="smart-master-table">
					<div class="smart-master-row smart-master-heading">
						${this.options.columns.map((column) => `<div>${__(column)}</div>`).join("")}
					</div>
					<div class="smart-master-rows"></div>
				</div>
			</div>
		`);

		this.listview.page.main.find(".layout-main-section").append(this.$wrapper);
		this.$rows = this.$wrapper.find(".smart-master-rows");

		this.$wrapper.find(".smart-master-search").on(
			"input",
			frappe.utils.debounce((event) => {
				this.search = event.target.value || "";
				this.refresh();
			}, 200)
		);

		this.$wrapper.find(".smart-master-add").on("click", () => this.show_dialog());
	}

	refresh() {
		return frappe
			.call({
				method: this.options.get_method,
				args: { search: this.search },
			})
			.then((response) => this.render(response.message || []));
	}

	render(rows) {
		this.$rows.empty();

		if (!rows.length) {
			this.$rows.append(`<div class="smart-master-empty">${__(this.options.empty_message)}</div>`);
			return;
		}

		rows.forEach((row) => this.$rows.append(this.render_row(row)));
	}

	actions(row) {
		const $actions = $(`<div class="smart-master-actions"></div>`);
		if (this.can_write) {
			$(`<button class="btn btn-xs btn-link" title="${__("Edit")}">${frappe.utils.icon("edit", "sm")}</button>`)
				.on("click", () => this.show_dialog(row))
				.appendTo($actions);
		}
		if (this.can_delete) {
			$(`<button class="btn btn-xs btn-link" title="${__("Delete")}">${frappe.utils.icon("delete", "sm")}</button>`)
				.on("click", () => this.confirm_delete(row))
				.appendTo($actions);
		}
		return $actions;
	}

	confirm_delete(row) {
		frappe.confirm(__(this.options.delete_message), () => {
			frappe
				.call({
					method: this.options.delete_method,
					args: { name: row.name },
				})
				.then(() => {
					frappe.show_alert({ message: __(this.options.deleted_message), indicator: "green" });
					return this.refresh();
				});
		});
	}
};

smart_edge_custom.AdditiveMaster = class AdditiveMaster extends smart_edge_custom.MasterPage {
	constructor(listview) {
		super(listview, {
			doctype: "Additive",
			title: "Additives",
			add_label: "Add Additive",
			search_placeholder: "Search additives...",
			class_name: "additive-master",
			intro: __("Master list of chemical additives used in manufacturing compounds."),
			columns: ["ADDITIVE NAME", "REMARK", "ACTIONS"],
			get_method: "smart_edge_custom.masters.get_additives",
			delete_method: "smart_edge_custom.masters.delete_additive",
			empty_message: "No additives found.",
			delete_message: "Are you sure you want to delete this Additive?",
			deleted_message: "Additive deleted",
		});
	}

	render_row(row) {
		const $row = $(`
			<div class="smart-master-row" data-name="${smart_edge_custom.escape(row.name)}">
				<div class="smart-master-name">
					<span class="smart-master-icon additive-icon">◇</span>
					<span>${smart_edge_custom.escape(row.additive_name || row.name)}</span>
				</div>
				<div>${row.remark ? smart_edge_custom.escape(row.remark) : '<span class="smart-muted">-</span>'}</div>
			</div>
		`);
		$row.append(this.actions(row));
		return $row;
	}

	show_dialog(row) {
		const is_edit = Boolean(row);
		const dialog = new frappe.ui.Dialog({
			title: is_edit ? __("Edit Additive") : __("Add Additive"),
			fields: [
				{
					fieldname: "subtitle",
					fieldtype: "HTML",
					options: `<p class="smart-dialog-subtitle">${__(
						"Define a chemical additive used in compound recipes."
					)}</p>`,
				},
				{
					fieldname: "additive_name",
					fieldtype: "Data",
					label: __("Name"),
					reqd: 1,
					placeholder: __("e.g. Calcium Carbonate"),
					default: row ? row.additive_name : "",
				},
				{
					fieldname: "remark",
					fieldtype: "Data",
					label: __("Remark"),
					placeholder: __("Optional notes..."),
					default: row ? row.remark : "",
				},
			],
			primary_action_label: is_edit ? __("Save") : __("Add"),
			primary_action: (values) => {
				if (!(values.additive_name || "").trim()) {
					frappe.msgprint(__("Name is required."));
					return;
				}

				dialog.disable_primary_action();
				frappe
					.call({
						method: is_edit ? "smart_edge_custom.masters.update_additive" : "smart_edge_custom.masters.create_additive",
						args: is_edit ? { name: row.name, ...values } : values,
					})
					.then(() => {
						dialog.hide();
						frappe.show_alert({ message: is_edit ? __("Additive saved") : __("Additive added"), indicator: "green" });
						return this.refresh();
					})
					.always(() => dialog.enable_primary_action());
			},
		});

		dialog.show();
		dialog.$wrapper.addClass("smart-master-dialog smart-simple-dialog");
	}
};

smart_edge_custom.CompoundMaster = class CompoundMaster extends smart_edge_custom.MasterPage {
	constructor(listview) {
		super(listview, {
			doctype: "Compound",
			title: "Compounds (Recipes)",
			add_label: "Add Compound",
			search_placeholder: "Search compounds...",
			class_name: "compound-master",
			intro: __("Define recipes by combining additives with quantities. Total kg is auto-calculated as the sum of additive quantities."),
			columns: ["NAME", "ADDITIVES", "TOTAL (KG)", "NOTE", "ACTIONS"],
			get_method: "smart_edge_custom.masters.get_compounds",
			delete_method: "smart_edge_custom.masters.delete_compound",
			empty_message: "No compounds found.",
			delete_message: "Are you sure you want to delete this Compound?",
			deleted_message: "Compound deleted",
		});
	}

	render_row(row) {
		const badges = (row.additives || [])
			.map(
				(item) =>
					`<span class="compound-badge">${smart_edge_custom.escape(item.additive)} <b>${smart_edge_custom.escape(
						flt(item.quantity_kg, 3)
					)}kg</b></span>`
			)
			.join("");
		const $row = $(`
			<div class="smart-master-row" data-name="${smart_edge_custom.escape(row.name)}">
				<div class="smart-master-name">
					<span class="smart-master-icon compound-icon">${frappe.utils.icon("filter", "sm")}</span>
					<span>${smart_edge_custom.escape(row.compound_name || row.name)}</span>
				</div>
				<div class="compound-badges">${badges || '<span class="smart-muted">-</span>'}</div>
				<div class="smart-strong">${flt(row.total_kg, 3).toFixed(3)}</div>
				<div>${row.note ? smart_edge_custom.escape(row.note) : '<span class="smart-muted">-</span>'}</div>
			</div>
		`);
		$row.append(this.actions(row));
		return $row;
	}

	show_dialog(row) {
		frappe
			.call({
				method: "smart_edge_custom.masters.get_additives",
			})
			.then((response) => {
				this.show_compound_dialog(row, response.message || []);
			});
	}

	show_compound_dialog(row, additive_options) {
		const is_edit = Boolean(row);
		const dialog = new frappe.ui.Dialog({
			title: is_edit ? __("Edit Compound") : __("Add Compound"),
			fields: [
				{
					fieldname: "body",
					fieldtype: "HTML",
					options: this.get_dialog_html(row),
				},
			],
			primary_action_label: is_edit ? __("Save") : __("Add"),
			primary_action: () => this.save_compound(dialog, row),
		});

		dialog.show();
		dialog.$wrapper.addClass("smart-master-dialog compound-dialog");
		this.bind_compound_dialog(dialog, row, additive_options);
	}

	get_dialog_html(row) {
		return `
			<p class="smart-dialog-subtitle">${__("Build a recipe from additives. Total kg updates live.")}</p>
			<div class="smart-field">
				<label>${__("Compound Name")} <span>*</span></label>
				<input class="form-control compound-name-input" placeholder="${__("e.g. Black PVC Mix")}" value="${smart_edge_custom.escape(
					row ? row.compound_name : ""
				)}">
			</div>
			<div class="smart-field">
				<label>${__("Note")}</label>
				<input class="form-control compound-note-input" placeholder="${__("Optional notes...")}" value="${smart_edge_custom.escape(
					row ? row.note : ""
				)}">
			</div>
			<div class="smart-section recipe-section">
				<div class="smart-section-head">
					<h4>${__("Additives")} (<span class="recipe-count">0</span>)</h4>
					<button class="btn btn-sm btn-default add-recipe-row" type="button">${frappe.utils.icon("add", "sm")} ${__(
			"Add Row"
		)}</button>
				</div>
				<div class="recipe-rows"></div>
				<div class="recipe-empty">${__("No additives added.")}</div>
				<div class="smart-total">
					<span>${__("Total")}</span>
					<strong class="recipe-total">${smart_edge_custom.format_kg(0)}</strong>
				</div>
			</div>
		`;
	}

	bind_compound_dialog(dialog, row, additive_options) {
		dialog._additive_options = additive_options;
		dialog.$body = dialog.$wrapper.find(".modal-body");
		dialog.$body.find(".add-recipe-row").on("click", () => this.add_recipe_row(dialog));

		(row && row.additives ? row.additives : []).forEach((item) => this.add_recipe_row(dialog, item));
		this.update_recipe_total(dialog);
	}

	add_recipe_row(dialog, item = {}) {
		const options = [`<option value=""></option>`]
			.concat(
				(dialog._additive_options || []).map((additive) => {
					const value = additive.name;
					const selected = value === item.additive ? " selected" : "";
					return `<option value="${smart_edge_custom.escape(value)}"${selected}>${smart_edge_custom.escape(
						additive.additive_name || value
					)}</option>`;
				})
			)
			.join("");

		const $row = $(`
			<div class="recipe-row">
				<select class="form-control recipe-additive">${options}</select>
				<input class="form-control recipe-qty" type="number" min="0" step="0.001" value="${smart_edge_custom.escape(
					item.quantity_kg || ""
				)}">
				<button class="btn btn-xs btn-link recipe-remove" type="button" title="${__("Remove")}">&times;</button>
			</div>
		`);

		$row.find("select, input").on("input change", () => this.update_recipe_total(dialog));
		$row.find(".recipe-remove").on("click", () => {
			$row.remove();
			this.update_recipe_total(dialog);
		});
		dialog.$body.find(".recipe-rows").append($row);
		this.update_recipe_total(dialog);
	}

	get_recipe_rows(dialog) {
		const rows = [];
		dialog.$body.find(".recipe-row").each(function () {
			rows.push({
				additive: ($(this).find(".recipe-additive").val() || "").trim(),
				quantity_kg: $(this).find(".recipe-qty").val(),
			});
		});
		return rows;
	}

	update_recipe_total(dialog) {
		const rows = this.get_recipe_rows(dialog);
		const total = rows.reduce((sum, row) => sum + flt(row.quantity_kg || 0, 3), 0);
		dialog.$body.find(".recipe-count").text(rows.length);
		dialog.$body.find(".recipe-empty").toggle(!rows.length);
		dialog.$body.find(".recipe-total").text(smart_edge_custom.format_kg(total));
	}

	save_compound(dialog, row) {
		const compound_name = (dialog.$body.find(".compound-name-input").val() || "").trim();
		const note = dialog.$body.find(".compound-note-input").val() || "";
		const additives = this.get_recipe_rows(dialog);

		if (!compound_name) {
			frappe.msgprint(__("Compound Name is required."));
			return;
		}
		if (!additives.length) {
			frappe.msgprint(__("At least one additive row is required."));
			return;
		}
		if (additives.some((item) => !item.additive || !flt(item.quantity_kg))) {
			frappe.msgprint(__("Every additive row needs an Additive and a valid quantity."));
			return;
		}

		dialog.disable_primary_action();
		frappe
			.call({
				method: row ? "smart_edge_custom.masters.update_compound" : "smart_edge_custom.masters.create_compound",
				args: {
					name: row ? row.name : undefined,
					compound_name,
					note,
					additives: JSON.stringify(additives),
				},
			})
			.then(() => {
				dialog.hide();
				frappe.show_alert({ message: row ? __("Compound saved") : __("Compound added"), indicator: "green" });
				return this.refresh();
			})
			.always(() => dialog.enable_primary_action());
	}
};

smart_edge_custom.SizeWeightMaster = class SizeWeightMaster extends smart_edge_custom.MasterPage {
	constructor(listview) {
		super(listview, {
			doctype: "Size Weight",
			title: "Size / Weight",
			add_label: "Add Size",
			search_placeholder: "Search sizes...",
			class_name: "size-weight-master",
			intro: `${__("Define sizes with width, thickness, and a multiplying factor. Weight per meter is auto-calculated in")} <b>${__(
				"grams"
			)}</b>: <code>weight/meter (g) = width × thickness × factor</code>`,
			columns: ["SIZE NAME", "WIDTH", "THICKNESS", "FACTOR", "WEIGHT / METER (G)", "NOTE", "ACTIONS"],
			get_method: "smart_edge_custom.masters.get_size_weights",
			delete_method: "smart_edge_custom.masters.delete_size_weight",
			empty_message: "No sizes found.",
			delete_message: "Are you sure you want to delete this Size / Weight?",
			deleted_message: "Size / Weight deleted",
		});
	}

	render_row(row) {
		const $row = $(`
			<div class="smart-master-row" data-name="${smart_edge_custom.escape(row.name)}">
				<div class="smart-master-name">
					<span class="smart-master-icon size-icon">⌁</span>
					<span>${smart_edge_custom.escape(row.size_name || row.name)}</span>
				</div>
				<div>${smart_edge_custom.escape(row.width)}</div>
				<div>${smart_edge_custom.escape(row.thickness)}</div>
				<div>${smart_edge_custom.escape(row.factor)}</div>
				<div class="smart-strong">${smart_edge_custom.format_weight(row.weight_per_meter_g)}</div>
				<div>${row.note ? smart_edge_custom.escape(row.note) : '<span class="smart-muted">-</span>'}</div>
			</div>
		`);
		$row.append(this.actions(row));
		return $row;
	}

	show_dialog(row) {
		const is_edit = Boolean(row);
		const dialog = new frappe.ui.Dialog({
			title: is_edit ? __("Edit Size / Weight") : __("Add Size / Weight"),
			fields: [
				{
					fieldname: "body",
					fieldtype: "HTML",
					options: this.get_dialog_html(row),
				},
			],
			primary_action_label: is_edit ? __("Save") : __("Add"),
			primary_action: () => this.save_size(dialog, row),
		});

		dialog.show();
		dialog.$wrapper.addClass("smart-master-dialog size-weight-dialog");
		this.bind_size_dialog(dialog);
	}

	get_dialog_html(row) {
		return `
			<p class="smart-dialog-subtitle">${__("Weight per meter (in grams) = width × thickness × factor")}</p>
			<div class="smart-field">
				<label>${__("Size Name")} <span>*</span></label>
				<input class="form-control size-name-input" placeholder="${__("e.g. 10mm x 2mm")}" value="${smart_edge_custom.escape(
					row ? row.size_name : ""
				)}">
			</div>
			<div class="smart-field-grid">
				<div class="smart-field">
					<label>${__("Width")}</label>
					<input class="form-control size-width-input" type="number" step="0.001" min="0" value="${smart_edge_custom.escape(
						row ? row.width : ""
					)}">
				</div>
				<div class="smart-field">
					<label>${__("Thickness")}</label>
					<input class="form-control size-thickness-input" type="number" step="0.001" min="0" value="${smart_edge_custom.escape(
						row ? row.thickness : ""
					)}">
				</div>
				<div class="smart-field">
					<label>${__("Factor")}</label>
					<input class="form-control size-factor-input" type="number" step="0.0001" min="0" value="${smart_edge_custom.escape(
						row ? row.factor : 1
					)}">
				</div>
			</div>
			<div class="smart-field">
				<label>${__("Note")}</label>
				<input class="form-control size-note-input" placeholder="${__("Optional...")}" value="${smart_edge_custom.escape(
					row ? row.note : ""
				)}">
			</div>
			<div class="smart-total size-total">
				<span>${__("Weight per meter (g)")}</span>
				<strong class="size-weight-total">0.0000</strong>
			</div>
		`;
	}

	bind_size_dialog(dialog) {
		dialog.$body = dialog.$wrapper.find(".modal-body");
		dialog.$body.find(".size-width-input, .size-thickness-input, .size-factor-input").on("input change", () => {
			this.update_size_total(dialog);
		});
		this.update_size_total(dialog);
	}

	get_size_values(dialog) {
		return {
			size_name: (dialog.$body.find(".size-name-input").val() || "").trim(),
			width: dialog.$body.find(".size-width-input").val(),
			thickness: dialog.$body.find(".size-thickness-input").val(),
			factor: dialog.$body.find(".size-factor-input").val(),
			note: dialog.$body.find(".size-note-input").val() || "",
		};
	}

	update_size_total(dialog) {
		const values = this.get_size_values(dialog);
		const total = flt(values.width) * flt(values.thickness) * flt(values.factor);
		dialog.$body.find(".size-weight-total").text(smart_edge_custom.format_weight(total));
	}

	save_size(dialog, row) {
		const values = this.get_size_values(dialog);
		if (!values.size_name) {
			frappe.msgprint(__("Size Name is required."));
			return;
		}
		if (!flt(values.width) || !flt(values.thickness) || !flt(values.factor)) {
			frappe.msgprint(__("Width, Thickness, and Factor must be valid positive numbers."));
			return;
		}

		dialog.disable_primary_action();
		frappe
			.call({
				method: row ? "smart_edge_custom.masters.update_size_weight" : "smart_edge_custom.masters.create_size_weight",
				args: row ? { name: row.name, ...values } : values,
			})
			.then(() => {
				dialog.hide();
				frappe.show_alert({ message: row ? __("Size / Weight saved") : __("Size / Weight added"), indicator: "green" });
				return this.refresh();
			})
			.always(() => dialog.enable_primary_action());
	}
};
