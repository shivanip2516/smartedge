frappe.provide("smart_edge_custom");

frappe.listview_settings["Base Colour"] = {
	onload(listview) {
		new smart_edge_custom.BaseColourList(listview);
	},
};

smart_edge_custom.BaseColourList = class BaseColourList {
	constructor(listview) {
		this.listview = listview;
		this.search = "";
		this.can_create = frappe.model.can_create ? frappe.model.can_create("Base Colour") : true;
		this.can_write = frappe.model.can_write ? frappe.model.can_write("Base Colour") : true;
		this.can_delete = frappe.model.can_delete ? frappe.model.can_delete("Base Colour") : true;
		this.make();
		this.refresh();
	}

	make() {
		this.listview.page.set_title(__("Base Colours"));
		this.listview.page.clear_primary_action();

		if (this.can_create) {
			this.listview.page.set_primary_action(__("+ Add Base Colour"), () => this.show_dialog());
		}

		this.listview.$result.hide();
		this.listview.page.sidebar && this.listview.page.sidebar.hide();

		this.$wrapper = $(`
			<div class="base-colour-master">
				<div class="base-colour-toolbar">
					<input class="form-control base-colour-search" type="search" placeholder="${__("Search colours...")}">
				</div>
				<p class="base-colour-intro">
					${__(
						"Master list of base colours with a weightage. Use 1 for the lightest colour (processed first in the mixer) up to 10 for the darkest. Mixer sequence is built using these weightages."
					)}
				</p>
				<div class="base-colour-table">
					<div class="base-colour-row base-colour-heading">
						<div>${__("COLOUR NAME")}</div>
						<div>${__("WEIGHTAGE")}</div>
						<div>${__("ACTIONS")}</div>
					</div>
					<div class="base-colour-rows"></div>
				</div>
			</div>
		`);

		this.listview.page.main.find(".layout-main-section").append(this.$wrapper);
		this.$rows = this.$wrapper.find(".base-colour-rows");

		this.$wrapper.find(".base-colour-search").on(
			"input",
			frappe.utils.debounce((event) => {
				this.search = event.target.value || "";
				this.refresh();
			}, 300)
		);
	}

	refresh() {
		return frappe
			.call({
				method: "smart_edge_custom.base_colour.get_base_colours",
				args: {
					search: this.search,
				},
			})
			.then((response) => {
				this.render(response.message || []);
			});
	}

	render(rows) {
		this.$rows.empty();

		if (!rows.length) {
			this.$rows.append(`<div class="base-colour-empty">${__("No Base Colours found")}</div>`);
			return;
		}

		rows.forEach((row) => {
			const colour_name = frappe.utils.escape_html(row.colour_name || row.name);
			const name = frappe.utils.escape_html(row.name);
			const weightage = cint(row.weightage);
			const badge_class = this.get_badge_class(weightage);
			const $row = $(`
				<div class="base-colour-row" data-name="${name}">
					<div class="base-colour-name">
						<span class="base-colour-palette" aria-hidden="true">${frappe.utils.icon("color", "sm")}</span>
						<span>${colour_name}</span>
					</div>
					<div><span class="base-colour-weight ${badge_class}">${weightage || ""}</span></div>
					<div class="base-colour-actions"></div>
				</div>
			`);

			const $actions = $row.find(".base-colour-actions");
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

			this.$rows.append($row);
		});
	}

	get_badge_class(weightage) {
		if (weightage <= 3) {
			return "base-colour-weight-light";
		}

		if (weightage <= 6) {
			return "base-colour-weight-medium";
		}

		return "base-colour-weight-dark";
	}

	show_dialog(row) {
		const is_edit = Boolean(row);
		const dialog = new frappe.ui.Dialog({
			title: is_edit ? __("Edit Base Colour") : __("Add Base Colour"),
			fields: [
				{
					fieldname: "subtitle",
					fieldtype: "HTML",
					options: `<p class="base-colour-dialog-subtitle">${__(
						"Name + weightage (1 = lightest, 10 = darkest)."
					)}</p>`,
				},
				{
					fieldname: "colour_name",
					fieldtype: "Data",
					label: __("Colour Name"),
					reqd: 1,
					placeholder: __("e.g. Walnut"),
					default: row ? row.colour_name : "",
				},
				{
					fieldname: "weightage",
					fieldtype: "Int",
					label: __("Weightage (1-10)"),
					reqd: 1,
					default: row ? row.weightage : 5,
					description: __("Lower = lighter (processed first). Higher = darker."),
				},
			],
			primary_action_label: is_edit ? __("Save") : __("Add"),
			primary_action: (values) => {
				if (!this.validate(values)) {
					return;
				}

				const method = is_edit
					? "smart_edge_custom.base_colour.update_base_colour"
					: "smart_edge_custom.base_colour.create_base_colour";
				const args = is_edit ? { name: row.name, ...values } : values;

				dialog.disable_primary_action();
				frappe
					.call({ method, args })
					.then(() => {
						dialog.hide();
						frappe.show_alert({
							message: is_edit ? __("Base Colour saved") : __("Base Colour added"),
							indicator: "green",
						});
						return this.refresh();
					})
					.always(() => dialog.enable_primary_action());
			},
		});

		dialog.show();
		dialog.$wrapper.addClass("base-colour-dialog");
	}

	validate(values) {
		const weightage = Number(values.weightage);

		if (!(values.colour_name || "").trim()) {
			frappe.msgprint(__("Colour Name is required."));
			return false;
		}

		if (!Number.isInteger(weightage) || weightage < 1 || weightage > 10) {
			frappe.msgprint(__("Weightage must be an integer between 1 and 10."));
			return false;
		}

		return true;
	}

	confirm_delete(row) {
		frappe.confirm(__("Are you sure you want to delete this Base Colour?"), () => {
			frappe
				.call({
					method: "smart_edge_custom.base_colour.delete_base_colour",
					args: { name: row.name },
				})
				.then(() => {
					frappe.show_alert({ message: __("Base Colour deleted"), indicator: "green" });
					return this.refresh();
				});
		});
	}
};
