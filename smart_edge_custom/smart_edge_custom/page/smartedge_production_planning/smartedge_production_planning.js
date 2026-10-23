frappe.pages["smartedge-production-planning"].on_page_load = function (wrapper) {
	wrapper.smartedge_planning = new smart_edge_custom.ProductionPlanningBoard(wrapper);
};

frappe.pages["smartedge-production-planning"].on_page_show = function (wrapper) {
	if (wrapper.smartedge_planning) {
		wrapper.smartedge_planning.refresh();
	}
};

frappe.provide("smart_edge_custom");

smart_edge_custom.ProductionPlanningBoard = class ProductionPlanningBoard {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: "",
			single_column: true,
		});
		this.method = "smart_edge_custom.manufacturing.planning";
		this.state = {
			week_start: this.get_week_start_from_route(),
			board: null,
			filters: { search: this.get_search_from_route() },
		};

		frappe.breadcrumbs.add(__("SmartEdge Manufacturing"));
		this.make_body();
		this.bind_global_events();
		this.bind_realtime_events();
	}

	bind_global_events() {
		$(document)
			.off("click.smartedge-production-planning")
			.on("click.smartedge-production-planning", () => {
				if (this.$body) {
					this.close_card_menus();
				}
			});
	}

	bind_realtime_events() {
		frappe.realtime.on("smartedge_planning_updated", () => {
			clearTimeout(this.realtime_refresh_timer);
			this.realtime_refresh_timer = setTimeout(() => this.refresh(), 500);
		});
	}

	make_body() {
		this.$body = $(`<div class="smartedge-planning"></div>`).appendTo(this.page.main);
		this.$header = $(`
			<div class="se-planning-header">
				<div class="se-title-block">
					<div class="se-page-title">${__("Production Planning Board")}</div>
					<div class="se-week-title"></div>
					<div class="se-summary-pills"></div>
				</div>
				<div class="se-header-actions">
					<button class="btn btn-sm btn-default se-prev-week">${__("Previous Week")}</button>
					<button class="btn btn-sm btn-default se-this-week">${__("This Week")}</button>
					<button class="btn btn-sm btn-default se-next-week">${__("Next Week")}</button>
					<button class="btn btn-sm btn-default se-refresh">${__("Refresh")}</button>
					<button class="btn btn-sm btn-primary se-new-production">${__("+ New Production")}</button>
				</div>
			</div>
		`).appendTo(this.$body);
		this.$week = this.$header.find(".se-week-title");
		this.$summary = this.$header.find(".se-summary-pills");
		this.$header.find(".se-prev-week").on("click", () => this.move_week(-7));
		this.$header.find(".se-this-week").on("click", () => this.set_this_week());
		this.$header.find(".se-next-week").on("click", () => this.move_week(7));
		this.$header.find(".se-refresh").on("click", () => this.refresh());
		this.$header.find(".se-new-production").on("click", () => frappe.new_doc("Final EB BOM Generator"));

		this.$filter_row = $(`<div class="se-filter-row"></div>`).appendTo(this.$body);
		this.$board_container = $(`<div class="planning-board-container"></div>`).appendTo(this.$body);
		this.$board_scroll = $(`<div class="smartedge-board-scroll"></div>`).appendTo(this.$board_container);
		this.$board_container.on("scroll", () => this.close_card_menus());
		this.$board_scroll.on("scroll", () => this.close_card_menus());
		this.$board = $(`<div class="smartedge-week-board se-board"></div>`).appendTo(this.$board_scroll);
		this.make_filters();
	}

	make_filters() {
		this.filters = {};
		for (const [fieldname, label] of [
			["category", __("Category")],
			["workstation", __("Workstation")],
			["operation", __("Operation")],
			["status", __("Status")],
			["priority", __("Priority")],
			["finished_product", __("Finished Product")],
			["search", __("Search")],
		]) {
			const fieldtype = fieldname === "finished_product" ? "Link" : fieldname === "search" ? "Data" : "Select";
			const control = frappe.ui.form.make_control({
				parent: this.$filter_row,
				df: {
					fieldname,
					label,
					fieldtype,
					options: fieldname === "finished_product" ? "Item" : [],
					onchange: () => {
						this.state.filters[fieldname] = control.get_value();
						this.refresh();
					},
				},
				render_input: true,
			});
			this.filters[fieldname] = control;
		}
		if (this.state.filters.search) {
			this.filters.search.set_input(this.state.filters.search);
		}
	}

	get_week_start_from_route() {
		const params = new URLSearchParams(window.location.search);
		const week = params.get("week");
		return week || get_monday(frappe.datetime.get_today());
	}

	get_search_from_route() {
		return new URLSearchParams(window.location.search).get("search") || "";
	}

	set_route_week() {
		const params = new URLSearchParams({ week: this.state.week_start });
		if (this.state.filters.search) params.set("search", this.state.filters.search);
		const route = `/app/smartedge-production-planning?${params.toString()}`;
		window.history.replaceState(null, "", route);
	}

	set_this_week() {
		this.state.week_start = get_monday(frappe.datetime.get_today());
		this.set_route_week();
		this.refresh();
	}

	move_week(days) {
		this.state.week_start = frappe.datetime.add_days(this.state.week_start, days);
		this.set_route_week();
		this.refresh();
	}

	async refresh() {
		const response = await frappe.call({
			method: `${this.method}.get_weekly_planning_board`,
			args: {
				week_start: this.state.week_start,
				...this.state.filters,
			},
		});
		this.state.board = response.message;
		this.state.week_start = response.message.week_start;
		this.populate_filter_options();
		this.render();
		this.schedule_date_refresh();
	}

	schedule_date_refresh() {
		clearTimeout(this.date_refresh_timer);
		const seconds = cint(this.state.board?.date_refresh_after_seconds || 0);
		if (seconds > 0) {
			this.date_refresh_timer = setTimeout(() => this.refresh(), seconds * 1000);
		}
	}

	populate_filter_options() {
		const filters = this.state.board.filters || {};
		this.set_select_options("category", filters.categories || []);
		this.set_select_options("workstation", filters.workstations || []);
		this.set_select_options("operation", filters.operations || []);
		this.set_select_options("status", filters.statuses || []);
		this.set_select_options("priority", filters.priorities || []);
	}

	set_select_options(fieldname, options) {
		const control = this.filters[fieldname];
		if (!control) return;
		const current = control.get_value();
		control.df.options = ["", ...options].join("\n");
		control.refresh();
		if (current) control.set_value(current);
	}

	render() {
		const board = this.state.board;
		this.stop_timers();
		this.$week.text(`${frappe.datetime.str_to_user(board.week_start)} - ${frappe.datetime.str_to_user(board.week_end)}`);
		this.render_summary(board);
		this.$board.empty();
		this.render_pending_column(board.pending || []);
		for (const day of Object.values(board.days || {})) {
			this.render_day_column(day);
		}
		this.start_timers();
	}

	render_summary(board) {
		const cards = [...(board.pending || [])];
		for (const day of Object.values(board.days || {})) {
			cards.push(...(day.operations || []));
		}
		const planned = cards.filter((card) => get_card_status(card) === "Planned").length;
		const running = cards.filter((card) => get_card_status(card) === "In Progress").length;
		const completed = cards.filter((card) => get_card_status(card) === "Completed").length;
		this.$summary.html(`
			<span class="se-summary-pill">${__("Pending")} <strong>${(board.pending || []).length}</strong></span>
			<span class="se-summary-pill">${__("Planned")} <strong>${planned}</strong></span>
			<span class="se-summary-pill">${__("Running")} <strong>${running}</strong></span>
			<span class="se-summary-pill">${__("Done")} <strong>${completed}</strong></span>
		`);
	}

	render_pending_column(cards) {
		const $column = this.make_column(__("Pending for Planning"), "", `${cards.length} ${__("Operations")}`, "pending");
		$column.addClass("pending-column");
		const $body = $column.find(".se-column-body");
		if (!cards.length) {
			$body.append(this.make_empty_state(__("No pending operations")));
		}
		for (const card of cards) {
			$body.append(this.make_card(card));
		}
		this.$board.append($column);
	}

	render_day_column(day) {
		const today = frappe.datetime.get_today();
		const $column = this.make_column(
			day.short_label,
			frappe.datetime.str_to_user(day.date),
			`${day.operation_count} ${__("Operations")}`,
			day.date
		);
		if (day.date === today) {
			$column.addClass("today");
		}
		const $body = $column.find(".se-column-body");
		for (const group of day.groups || []) {
			const group_color = CATEGORY_COLOR_CLASSES[group.color] || "";
			const $group = $(`
				<div class="se-group">
					<div class="se-group-title ${frappe.utils.escape_html(group_color)}">
						<span>${frappe.utils.escape_html(group.category)}</span>
						<strong>${frappe.utils.escape_html(group.count)}</strong>
					</div>
				</div>
			`);
			for (const workstation of group.workstations || []) {
				const capacity = workstation.capacity || {};
				const capacity_label = capacity.configured
					? `${se_format_number(capacity.planned_hours, 1)}h / ${se_format_number(capacity.capacity_hours, 1)}h ${__("planned")}`
					: "";
				const $workstation = $(`
					<div class="se-workstation-group" data-workstation="${frappe.utils.escape_html(workstation.workstation)}">
						<div class="se-workstation" title="${frappe.utils.escape_html(workstation.workstation)}">
							<span>${frappe.utils.escape_html(workstation.workstation)}</span>
							${capacity_label ? `<small>${frappe.utils.escape_html(capacity_label)}</small>` : ""}
						</div>
					</div>
				`);
				this.bind_workstation_drop($workstation, day.date, workstation.workstation);
				for (const card of workstation.cards || []) {
					$workstation.append(this.make_card(card));
				}
				$group.append($workstation);
			}
			$body.append($group);
		}
		if (!day.operation_count) {
			$body.append(this.make_empty_state(__("Drop operation here")));
		}
		this.$board.append($column);
	}

	make_column(title, date, count, date_key) {
		const $column = $(`
			<div class="se-column day-column" data-date="${frappe.utils.escape_html(date_key)}">
				<div class="se-column-header day-header">
					<div>
						<div class="se-column-title">${frappe.utils.escape_html(title)}</div>
						<div class="se-column-date">${frappe.utils.escape_html(date || "")}</div>
					</div>
					<div class="se-column-meta">
						<div class="se-column-count">${frappe.utils.escape_html(count || "")}</div>
					</div>
				</div>
				<div class="se-column-body day-drop-zone"></div>
			</div>
		`);
		if (date_key !== "pending") {
			const $body = $column.find(".se-column-body");
			$body.on("dragover", (event) => {
				event.preventDefault();
				$body.addClass("drag-over");
				$column.addClass("drag-over");
			});
			$body.on("dragleave", () => {
				$body.removeClass("drag-over");
				$column.removeClass("drag-over");
			});
			$body.on("drop", (event) => {
				event.preventDefault();
				$body.removeClass("drag-over");
				$column.removeClass("drag-over");
				const payload = JSON.parse(event.originalEvent.dataTransfer.getData("application/json"));
				this.open_schedule_dialog(payload, date_key);
			});
		}
		return $column;
	}

	bind_workstation_drop($workstation, date_key, workstation) {
		$workstation.on("dragover", (event) => {
			event.preventDefault();
			event.stopPropagation();
			$workstation.addClass("drag-over");
		});
		$workstation.on("dragleave", () => $workstation.removeClass("drag-over"));
		$workstation.on("drop", async (event) => {
			event.preventDefault();
			event.stopPropagation();
			$workstation.removeClass("drag-over");
			const card = JSON.parse(event.originalEvent.dataTransfer.getData("application/json"));
			await this.handle_workstation_drop(card, date_key, workstation);
		});
	}

	async handle_workstation_drop(card, date_key, workstation) {
		if (card.planned_date && card.planned_start) {
			await frappe.call({
				method: `${this.method}.schedule_operation`,
				args: {
					plan_entry: card.id,
					planned_date: date_key,
					workstation,
					planned_start: get_time_from_datetime(card.planned_start) || "09:00",
					duration_hours: card.duration_hours || 1,
					priority: card.priority,
				},
			});
			await this.refresh();
			return;
		}
		await this.open_schedule_dialog(card, date_key, workstation);
	}

	make_card(card) {
		const is_pending = !card.planned_date;
		const is_material = card.plan_type === "Required Material";
		const qty = is_material
			? `${cint(card.planned_batches)} ${__("batches")}`
			: `${se_format_number(card.qty, 2)} m`;
		const weight = card.planned_weight_kg
			? `${se_format_number(card.planned_weight_kg, 3)} ${is_material ? card.uom || "" : "Kg"}`
			: "";
		const released_qty = get_released_qty_label(card);
		const category_class = CATEGORY_COLOR_CLASSES[card.category_color] || "category-other";
		const effective_status = get_card_status(card);
		const status_class = `status-${String(effective_status || "Pending").toLowerCase().replace(/\s+/g, "-")}`;
		const status_label = get_status_label(effective_status);
		const product_line = [is_material ? card.material_type : card.size, card.item_name || card.finished_item].filter(Boolean).join(" • ");
		const qty_line = [qty, weight, released_qty].filter(Boolean).join(" • ");
		const planned_time = get_planned_time_label(card);
		const workstation_label = card.workstation || __("Not Assigned");
		const material_trace = is_material
			? `<div class="se-card-time">${__("WO")}: ${frappe.utils.escape_html(card.work_order || "-")} • ${__("BOM")}: ${frappe.utils.escape_html(card.bom_no || "-")}</div>
				<div class="se-card-time">${__("Employee")}: ${frappe.utils.escape_html(card.operator_name || "-")} • ${__("Shift")}: ${frappe.utils.escape_html(card.shift || "-")}</div>`
			: "";
		const tooltip = [
			card.production,
			card.item_name || card.finished_item,
			workstation_label,
			card.operation,
			card.operator_name && `${__("Operator")}: ${card.operator_name}`,
			card.planned_start && `${__("Start")}: ${card.planned_start}`,
			card.duration_hours && `${__("Hours")}: ${se_format_number(card.duration_hours, 2)}`,
		]
			.filter(Boolean)
			.join("\n");
		const $card = $(`
			<div class="se-card plan-card ${category_class} ${is_pending ? "pending-card" : ""}" draggable="true" title="${frappe.utils.escape_html(tooltip)}">
				<div class="se-card-head">
					<span class="se-card-operation-title">${frappe.utils.escape_html(card.operation || "")}</span>
					<span class="se-sequence-badge">${is_material ? __("MAT") : `#${frappe.utils.escape_html(card.operation_sequence || "")}`}</span>
				</div>
				<div class="se-card-status-row">
					<span class="se-status-pill ${status_class}">${frappe.utils.escape_html(status_label)}</span>
				</div>
				<div class="se-card-main">
					<div class="se-card-workstation" title="${frappe.utils.escape_html(workstation_label)}">
						<span>${__("Workstation")}:</span> ${frappe.utils.escape_html(workstation_label)}
					</div>
					<div class="se-product-name" title="${frappe.utils.escape_html(product_line)}">${frappe.utils.escape_html(product_line)}</div>
					<div class="se-card-qty">${frappe.utils.escape_html(qty_line)}</div>
					${material_trace}
					${this.get_status_line_html(card, planned_time, status_class)}
				</div>
				<div class="se-card-actions">
					${this.get_primary_action_html(card, is_pending, status_class)}
					<div class="dropdown se-card-menu">
						<button class="btn btn-xs btn-default se-icon-btn se-more" data-toggle="dropdown" aria-label="${__("More actions")}" title="${__("More actions")}">⋯</button>
						<ul class="dropdown-menu dropdown-menu-right">
							<li><a class="dropdown-item se-view" href="#">${__("View Details")}</a></li>
							${is_material ? `<li><a class="dropdown-item se-open-work-order" href="#">${__("Open Work Order")}</a></li>` : ""}
							<li><a class="dropdown-item se-edit" href="#">${__("Edit")}</a></li>
							${!is_material ? `<li><a class="dropdown-item se-plan-all" href="#">${__("Plan Full Route")}</a></li>` : ""}
							${!is_pending ? `<li><a class="dropdown-item se-unplan" href="#">${__("Unplan")}</a></li>` : ""}
							<li><a class="dropdown-item se-open-production" href="#">${__("Open Production")}</a></li>
						</ul>
					</div>
				</div>
			</div>
		`);
		$card.on("dragstart", (event) => {
			$card.addClass("dragging");
			event.originalEvent.dataTransfer.setData("application/json", JSON.stringify(card));
		});
		$card.on("dragend", () => $card.removeClass("dragging"));
		$card.find(".dropdown-item").on("click", (event) => {
			event.preventDefault();
			this.close_card_menus();
		});
		$card.find(".se-more").on("click", (event) => {
			event.preventDefault();
			event.stopPropagation();
			const $menu = $(event.currentTarget).closest(".se-card-menu");
			this.close_card_menus($menu);
			$menu.toggleClass("show");
			if ($menu.hasClass("show")) {
				this.position_card_menu($menu);
			}
		});
		$card.find(".se-card-menu").on("click", (event) => event.stopPropagation());
		$card.find(".se-view").on("click", () =>
			is_material
				? frappe.set_route("Form", "SmartEdge Production Plan Entry", card.id)
				: this.open_production_drawer(card.production, card)
		);
		$card.find(".se-open-work-order").on("click", () => frappe.set_route("Form", "Work Order", card.work_order));
		$card.find(".se-edit, .se-plan").on("click", () => this.open_schedule_dialog(card, card.planned_date || this.state.week_start));
		$card.find(".se-plan-all").on("click", () => this.plan_full_production(card));
		$card.find(".se-unplan").on("click", () => this.unplan(card));
		$card.find(".se-open-production").on("click", () => frappe.set_route("Form", "Final EB BOM Generator", card.production));
		$card.find(".se-start").on("click", () => this.open_start_dialog(card));
		$card.find(".se-pause").on("click", () => this.pause_production_job(card));
		$card.find(".se-resume").on("click", () => this.resume_production_job(card));
		$card.find(".se-complete").on("click", () => this.open_complete_dialog(card));
		return $card;
	}

	close_card_menus($except = null) {
		const $menus = $except ? this.$body.find(".se-card-menu.show").not($except) : this.$body.find(".se-card-menu.show");
		$menus.removeClass("show drop-up");
		$menus.find(".dropdown-menu").removeAttr("style");
	}

	position_card_menu($menu) {
		const $dropdown = $menu.find(".dropdown-menu");
		const trigger_rect = $menu.find(".se-more")[0].getBoundingClientRect();
		const menu_width = Math.max($dropdown.outerWidth() || 170, 170);
		const menu_height = Math.max($dropdown.outerHeight() || 150, 150);
		const gutter = 8;
		let top = trigger_rect.bottom + gutter;
		let left = trigger_rect.right - menu_width;
		if (top + menu_height > window.innerHeight - gutter) {
			top = trigger_rect.top - menu_height - gutter;
			$menu.addClass("drop-up");
		}
		left = Math.max(gutter, Math.min(left, window.innerWidth - menu_width - gutter));
		$dropdown.css({
			position: "fixed",
			top: `${Math.max(gutter, top)}px`,
			left: `${left}px`,
			right: "auto",
			zIndex: 1050,
		});
	}

	get_primary_action_html(card, is_pending, status_class) {
		const status = get_card_status(card);
		if (status === "Completed") {
			return `<span class="se-state-pill ${status_class}">✓</span>`;
		}
		if (status === "Cancelled") {
			return `<span class="se-state-pill ${status_class}">×</span>`;
		}
		if (is_pending) {
			return `<button class="btn btn-xs btn-primary se-plan">${__("Plan")}</button>`;
		}
		if (status === "In Progress") {
			return `
				<button class="btn btn-xs btn-default se-pause">${__("Pause")}</button>
				<button class="btn btn-xs btn-primary se-complete">${__("Complete Process")}</button>
			`;
		}
		if (status === "Paused") {
			return `
				<button class="btn btn-xs btn-default se-resume">${__("Resume")}</button>
				<button class="btn btn-xs btn-primary se-complete">${__("Complete Process")}</button>
			`;
		}
		if (card.start_allowed === false) {
			const reason = frappe.utils.escape_html(card.start_block_reason || __("Schedule the operation before starting production."));
			return `<button class="btn btn-xs btn-primary se-start" disabled title="${reason}" aria-label="${reason}">▶ ${__("Start")}</button>`;
		}
		return `<button class="btn btn-xs btn-primary se-start" title="${__("Start Production")}" aria-label="${__("Start Production")}">▶ ${__("Start")}</button>`;
	}

	get_status_line_html(card, planned_time, status_class) {
		const status = get_card_status(card);
		if (status === "In Progress") {
			const running_label = card.operator_name
				? `${__("RUNNING")} • ${frappe.utils.escape_html(card.operator_name)}`
				: __("RUNNING");
			return `
				<div class="se-running-line ${status_class}">● ${running_label}</div>
				${get_timer_html(card, true)}
			`;
		}
		if (status === "Paused") {
			const reason = card.downtime_reason ? frappe.utils.escape_html(card.downtime_reason) : __("Paused");
			const employee = card.operator_name ? `<div class="se-running-line">${__("Employee")}: ${frappe.utils.escape_html(card.operator_name)}</div>` : "";
			return `
				<div class="se-card-time" title="${reason}">${__("PAUSED")} — ${reason}</div>
				${employee}
				${get_timer_html(card, false)}
				${get_downtime_timer_html(card)}
			`;
		}
		if (status === "Completed") {
			return `
				<div class="se-card-time">${__("Completed")}${planned_time ? ` • ${frappe.utils.escape_html(planned_time)}` : ""}</div>
				${get_timer_html(card, false)}
			`;
		}
		if (card.job_completion_type === "Partial Completion" || card.work_order_is_partial) {
			const remaining = get_remaining_qty_label(card);
			const start_block = card.start_allowed === false && card.start_block_reason
				? `<div class="se-card-time">${frappe.utils.escape_html(card.start_block_reason)}</div>`
				: "";
			return `
				<div class="se-card-time">${__("Partially Completed")}${remaining ? ` • ${frappe.utils.escape_html(remaining)}` : ""}</div>
				${start_block}
				${get_timer_html(card, false)}
			`;
		}
		if (card.start_allowed === false && card.start_block_reason) {
			return `
				${planned_time ? `<div class="se-card-time">${__("Planned")} ${frappe.utils.escape_html(planned_time)}</div>` : ""}
				<div class="se-card-time">${frappe.utils.escape_html(card.start_block_reason)}</div>
			`;
		}
		if (status === "Ready") {
			const released = get_released_qty_label(card);
			return `<div class="se-card-time">${__("Ready")}${released ? ` • ${frappe.utils.escape_html(released)}` : ""}</div>`;
		}
		if (planned_time) {
			return `<div class="se-card-time">${__("Planned")} ${frappe.utils.escape_html(planned_time)}</div>`;
		}
		return `<div class="se-card-time">${__("Pending workstation schedule")}</div>`;
	}

	make_empty_state(message) {
		return $(`
			<div class="se-empty">
				<span class="se-empty-icon">↧</span>
				<span>${frappe.utils.escape_html(message)}</span>
			</div>
		`);
	}

	async open_schedule_dialog(card, date_key, default_workstation = null) {
		const response = await frappe.call({
			method: `${this.method}.get_compatible_workstations`,
			args: {
				operation: card.operation,
				operation_row: card.operation_row,
			},
		});
		const workstations = response.message || [];
		if (card.plan_type === "Required Material" && card.workstation && !workstations.includes(card.workstation)) {
			workstations.unshift(card.workstation);
		}
		if (!workstations.length) {
			frappe.throw(__("No compatible Workstation found for Operation {0}.", [card.operation]));
		}
		const default_start = get_time_from_datetime(card.planned_start) || "09:00";
		const default_duration = card.duration_hours || 1;
		const dialog = new frappe.ui.Dialog({
			title: __("Plan Operation"),
			fields: [
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{ fieldtype: "Date", fieldname: "planned_date", label: __("Date"), reqd: 1, default: date_key },
				{
					fieldtype: "Select",
					fieldname: "workstation",
					label: __("Workstation"),
					reqd: 1,
					options: workstations.join("\n"),
					default: default_workstation || card.workstation || workstations[0],
				},
				{ fieldtype: "Time", fieldname: "planned_start", label: __("Planned Start"), reqd: 1, default: default_start },
				{
					fieldtype: "Float",
					fieldname: "duration_hours",
					label: __("Expected Duration (Hours)"),
					reqd: 1,
					default: default_duration,
				},
				{
					fieldtype: "Data",
					fieldname: "planned_end",
					label: __("Planned End"),
					read_only: 1,
				},
				{ fieldtype: "Check", fieldname: "override_sequence", label: __("Override sequence warning") },
				{ fieldtype: "Check", fieldname: "override_capacity", label: __("Override capacity warning") },
			],
			primary_action_label: __("Schedule"),
			primary_action: async (values) => {
				if (!validate_schedule_in_browser(values.planned_date, values.planned_start)) return;
				await frappe.call({
						method: `${this.method}.schedule_operation`,
						args: {
							plan_entry: card.id,
							planned_date: values.planned_date,
							workstation: values.workstation,
							planned_start: values.planned_start,
							duration_hours: values.duration_hours,
							priority: card.priority,
							override_sequence: values.override_sequence,
							override_capacity: values.override_capacity,
						},
				});
				dialog.hide();
				await this.refresh();
			},
		});
		dialog.show();
		set_schedule_date_limits(dialog, "planned_date", "planned_start");
		const update_end = () => {
			const values = dialog.get_values(true) || {};
			dialog.set_value("planned_end", compute_planned_end(values.planned_start, values.duration_hours));
		};
		dialog.fields_dict.planned_start.df.onchange = update_end;
		dialog.fields_dict.duration_hours.df.onchange = update_end;
		dialog.fields_dict.planned_start.$input?.on("change", update_end);
		dialog.fields_dict.duration_hours.$input?.on("change", update_end);
		update_end();
	}

	async unplan(card) {
		frappe.confirm(__("Remove this operation from the schedule?"), async () => {
			await frappe.call({
				method: `${this.method}.unplan_operation`,
				args: {
					plan_entry: card.id,
				},
			});
			await this.refresh();
		});
	}

	async plan_full_production(card) {
		const dialog = new frappe.ui.Dialog({
			title: __("Plan Full Production"),
			fields: [
				{ fieldtype: "Data", fieldname: "production", label: __("Production"), read_only: 1, default: card.production },
				{ fieldtype: "Date", fieldname: "start_date", label: __("Start Date"), reqd: 1, default: card.planned_date || this.state.week_start },
				{ fieldtype: "Time", fieldname: "start_time", label: __("Start Time"), reqd: 1, default: "09:00" },
			],
			primary_action_label: __("Create Plan"),
			primary_action: async (values) => {
				if (!validate_schedule_in_browser(values.start_date, values.start_time)) return;
				await frappe.call({
					method: `${this.method}.plan_full_production`,
					args: {
						production: card.production,
						start_date: values.start_date,
						start_time: values.start_time,
					},
				});
				dialog.hide();
				await this.refresh();
			},
		});
		dialog.show();
		set_schedule_date_limits(dialog, "start_date", "start_time");
	}

	async open_start_dialog(card) {
		const response = await frappe.call({
			method: `${this.method}.get_start_job_context`,
			args: { plan_entry: card.id },
		});
		const context = response.message || {};
		const compatible_workstations = context.compatible_workstations || [];
		let starting = false;
		const dialog = new frappe.ui.Dialog({
			title: __("Start Production"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "production_definition",
					label: __("Production Definition"),
					read_only: 1,
					default: context.production_definition || card.production,
				},
				{
					fieldtype: "Data",
					fieldname: "finished_item",
					label: context.is_material_production ? __("Production Item") : __("Finished Item"),
					read_only: 1,
					default: context.finished_item || card.item_name || card.finished_item,
				},
				{ fieldtype: "Section Break", label: __("Material Production"), hidden: !context.is_material_production },
				{ fieldtype: "Data", fieldname: "material_type", label: __("Material Type"), read_only: 1, hidden: !context.is_material_production, default: context.material_type },
				{ fieldtype: "Link", fieldname: "work_order", label: __("Work Order"), options: "Work Order", read_only: 1, hidden: !context.is_material_production, default: context.work_order },
				{ fieldtype: "Link", fieldname: "bom_no", label: __("BOM"), options: "BOM", read_only: 1, hidden: !context.is_material_production, default: context.bom_no },
				{ fieldtype: "Column Break", hidden: !context.is_material_production },
				{ fieldtype: "Int", fieldname: "planned_batches", label: __("No. of Batches"), read_only: 1, hidden: !context.is_material_production, default: context.planned_batches },
				{ fieldtype: "Float", fieldname: "weight_per_batch", label: __("Weight per Batch ({0})", [context.quantity_uom || ""]), precision: 6, read_only: 1, hidden: !context.is_material_production, default: context.weight_per_batch },
				{ fieldtype: "Float", fieldname: "total_planned_quantity", label: __("Total Planned Quantity ({0})", [context.quantity_uom || ""]), precision: 6, read_only: 1, hidden: !context.is_material_production, default: context.total_planned_quantity },
				{ fieldtype: "Section Break", label: __("Warehouses"), hidden: !context.is_material_production },
				{ fieldtype: "Link", fieldname: "source_warehouse", label: __("Source Warehouse"), options: "Warehouse", read_only: 1, hidden: !context.is_material_production, default: context.source_warehouse },
				{ fieldtype: "Link", fieldname: "wip_warehouse", label: __("Work-in-Progress Warehouse"), options: "Warehouse", read_only: 1, hidden: !context.is_material_production, default: context.wip_warehouse },
				{ fieldtype: "Link", fieldname: "target_warehouse", label: __("Target Warehouse"), options: "Warehouse", read_only: 1, hidden: !context.is_material_production, default: context.target_warehouse },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{
					fieldtype: "Link",
					fieldname: "workstation",
					label: __("Workstation"),
					options: "Workstation",
					reqd: 1,
					read_only: !context.can_edit_workstation,
					default: context.workstation || card.workstation,
					get_query: () => ({
						filters: {
							name: ["in", compatible_workstations],
							disabled: 0,
						},
					}),
				},
				{
					fieldtype: "Float",
					fieldname: "planned_meter",
					label: context.is_material_production ? __("Planned Batches") : __("Planned Meter"),
					precision: 6,
					read_only: 1,
					default: context.planned_qty_meter,
				},
				{
					fieldtype: "Float",
					fieldname: "planned_kg",
					label: context.is_material_production ? __("Planned Quantity ({0})", [context.quantity_uom || ""]) : __("Planned Kg"),
					precision: 6,
					read_only: 1,
					default: context.planned_qty_kg,
				},
				{
					fieldtype: "Link",
					fieldname: "operator_employee",
					label: __("Employee"),
					options: "Employee",
					reqd: 1,
					default: context.operator_employee,
					get_query: () => {
						const filters = { status: "Active" };
						if (context.company) {
							filters.company = context.company;
						}
						return { filters };
					},
				},
				{
					fieldtype: "Link",
					fieldname: "shift",
					label: __("Shift"),
					options: "Shift Type",
					read_only: 1,
					default: context.shift,
				},
				{
					fieldtype: "Datetime",
					fieldname: "planned_start",
					label: __("Planned Start"),
					read_only: 1,
					default: context.planned_start,
				},
			],
			primary_action_label: __("Start Production"),
			primary_action: async (values) => {
				if (starting) {
					return;
				}
				starting = true;
				const $primary = dialog.get_primary_btn();
				$primary.prop("disabled", true);
				try {
					const start_response = await frappe.call({
						method: `${this.method}.start_production_job`,
						args: {
							plan_entry: card.id,
							operator_employee: values.operator_employee,
							workstation: values.workstation,
							shift: values.shift,
						},
					});
					dialog.hide();
					await this.refresh();
					if (start_response.message?.production_job) {
						frappe.set_route("Form", "SmartEdge Production Job", start_response.message.production_job);
					}
				} finally {
					starting = false;
					$primary.prop("disabled", false);
				}
			},
		});
		dialog.show();
		if (dialog.set_secondary_action_label && dialog.set_secondary_action) {
			dialog.set_secondary_action_label(__("Cancel"));
			dialog.set_secondary_action(() => dialog.hide());
		}
	}

	async pause_production_job(card) {
		let pausing = false;
		const dialog = new frappe.ui.Dialog({
			title: __("Pause Production"),
			fields: [
				{ fieldtype: "Data", fieldname: "production_job", label: __("Production Job"), read_only: 1, default: card.production_job },
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{ fieldtype: "Data", fieldname: "operator_name", label: __("Employee"), read_only: 1, default: card.operator_name || "" },
				{
					fieldtype: "Link",
					fieldname: "downtime_reason",
					label: __("Downtime Reason"),
					options: "SmartEdge Downtime Reason",
					reqd: 1,
					get_query: () => ({ filters: { disabled: 0 } }),
				},
				{ fieldtype: "Small Text", fieldname: "downtime_remarks", label: __("Remarks") },
			],
			primary_action_label: __("Confirm Pause"),
			primary_action: async (values) => {
				if (pausing) {
					return;
				}
				pausing = true;
				const $primary = dialog.get_primary_btn();
				$primary.prop("disabled", true);
				try {
					await frappe.call({
						method: `${this.method}.pause_production_job`,
						args: {
							plan_entry: card.id,
							downtime_reason: values.downtime_reason,
							downtime_remarks: values.downtime_remarks,
						},
					});
					dialog.hide();
					await this.refresh();
				} finally {
					pausing = false;
					$primary.prop("disabled", false);
				}
			},
		});
		dialog.show();
		if (dialog.set_secondary_action_label && dialog.set_secondary_action) {
			dialog.set_secondary_action_label(__("Cancel"));
			dialog.set_secondary_action(() => dialog.hide());
		}
	}

	async resume_production_job(card) {
		await frappe.call({
			method: `${this.method}.resume_production_job`,
			args: { plan_entry: card.id },
		});
		await this.refresh();
	}

	async open_complete_dialog(card) {
		const response = await frappe.call({
			method: `${this.method}.get_complete_job_context`,
			args: { plan_entry: card.id },
		});
		const context = response.message || {};
		const is_material = Boolean(context.is_material_production);
		const meter_label = is_material ? __("Completed Batches") : __("Actual Completed Qty") + " (m)";
		const kg_required = flt(context.planned_qty_kg) > 0;
		let completing = false;
		const dialog = new frappe.ui.Dialog({
			title: __("Complete Process"),
			fields: [
				{ fieldtype: "Section Break", label: __("Production Information") },
				{ fieldtype: "Data", fieldname: "production_job", label: __("Production Job"), read_only: 1, default: context.production_job || card.production_job },
				{
					fieldtype: "Data",
					fieldname: "finished_item",
					label: is_material ? __("Production Item") : __("Finished Item"),
					read_only: 1,
					default: context.finished_item || card.item_name || card.finished_item,
				},
				{ fieldtype: "Data", fieldname: "material_type", label: __("Material Type"), read_only: 1, hidden: !is_material, default: context.material_type },
				{ fieldtype: "Link", fieldname: "work_order", label: __("Work Order"), options: "Work Order", read_only: 1, hidden: !is_material, default: context.work_order },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: context.operation || card.operation },
				{ fieldtype: "Data", fieldname: "workstation", label: __("Workstation"), read_only: 1, default: context.workstation || card.workstation },
				{ fieldtype: "Data", fieldname: "operator_name", label: __("Employee"), read_only: 1, default: context.operator_name || card.operator_name || "" },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Float", fieldname: "planned_qty_meter", label: is_material ? __("Planned Batches") : __("Planned Qty Meter"), precision: 6, read_only: 1, default: context.planned_qty_meter || card.qty || 0 },
				{ fieldtype: "Float", fieldname: "planned_qty_kg", label: is_material ? __("Planned Quantity ({0})", [context.quantity_uom || ""]) : __("Planned Kg"), precision: 6, read_only: 1, default: context.planned_qty_kg || card.planned_weight_kg || 0 },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "actual_running_time", label: __("Actual Running Time"), read_only: 1, default: format_duration_seconds(context.productive_elapsed_seconds || card.job_productive_elapsed_seconds || 0) },
				{ fieldtype: "Data", fieldname: "total_downtime", label: __("Total Downtime"), read_only: 1, default: format_duration_seconds(context.total_downtime_seconds || 0) },
				{ fieldtype: "Section Break", label: __("Completion Details") },
				{
					fieldtype: "Select",
					fieldname: "completion_type",
					label: __("Completion Type"),
					options: "Full Completion\nPartial Completion",
					reqd: 1,
					default: "Full Completion",
				},
				{ fieldtype: "Float", fieldname: "actual_qty_meter", label: meter_label, precision: 6, reqd: 1, default: context.actual_qty_meter || context.planned_qty_meter || card.qty || 0 },
				{ fieldtype: "Float", fieldname: "actual_qty_kg", label: is_material ? __("Actual Completed Quantity ({0})", [context.quantity_uom || ""]) : __("Actual Kg"), precision: 6, reqd: kg_required, hidden: !kg_required, read_only: is_material, default: context.actual_qty_kg || context.planned_qty_kg || card.planned_weight_kg || 0 },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Float", fieldname: "good_qty", label: __("Good Qty"), precision: 6, default: context.good_qty || (is_material ? context.total_planned_quantity : card.qty) || 0 },
				{ fieldtype: "Float", fieldname: "rejected_qty", label: __("Rejected Qty"), precision: 6, default: context.rejected_qty || 0 },
				{ fieldtype: "Float", fieldname: "rework_qty", label: __("Rework Qty"), precision: 6, default: context.rework_qty || 0 },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Float", fieldname: "usable_scrap", label: __("Usable Scrap"), precision: 6, default: context.usable_scrap || 0 },
				{ fieldtype: "Float", fieldname: "unusable_scrap", label: __("Unusable Scrap"), precision: 6, default: context.unusable_scrap || 0 },
				{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks"), default: context.remarks || "" },
			],
			primary_action_label: __("Confirm Completion"),
			primary_action: async (values) => {
				if (completing) {
					return;
				}
				completing = true;
				const $primary = dialog.get_primary_btn();
				$primary.prop("disabled", true);
				try {
					await frappe.call({
						method: `${this.method}.complete_production_job`,
						args: {
							plan_entry: card.id,
							completion_type: values.completion_type,
							actual_qty_meter: values.actual_qty_meter,
							actual_qty_kg: values.actual_qty_kg,
							good_qty: values.good_qty,
							rejected_qty: values.rejected_qty,
							rework_qty: values.rework_qty,
							usable_scrap: values.usable_scrap,
							unusable_scrap: values.unusable_scrap,
							remarks: values.remarks,
						},
					});
					dialog.hide();
					await this.refresh();
				} finally {
					completing = false;
					$primary.prop("disabled", false);
				}
			},
		});
		dialog.show();
		if (is_material) {
			const update_material_quantity = () => {
				const batches = cint(dialog.get_value("actual_qty_meter"));
				dialog.set_value("actual_qty_kg", flt(batches * flt(context.weight_per_batch), 6));
			};
			dialog.fields_dict.actual_qty_meter.df.onchange = update_material_quantity;
			dialog.fields_dict.actual_qty_meter.$input?.on("change", update_material_quantity);
			update_material_quantity();
		}
		if (dialog.set_secondary_action_label && dialog.set_secondary_action) {
			dialog.set_secondary_action_label(__("Cancel"));
			dialog.set_secondary_action(() => dialog.hide());
		}
	}

	async start_production_job(card) {
		const response = await frappe.call({
			method: `${this.method}.start_production_job`,
			args: {
				plan_entry: card.id,
			},
		});
		if (response.message?.production_job) {
			frappe.set_route("Form", "SmartEdge Production Job", response.message.production_job);
		}
	}

	async open_production_drawer(production, selected_card = null) {
		const response = await frappe.call({
			method: `${this.method}.get_production_plan`,
			args: { production },
		});
		const plan = response.message;
		const rows = (plan.operations || [])
			.map((row) => {
				const mark = row.planned_date ? "Planned" : "Not planned";
				return `<div class="se-drawer-row">
					<strong>${frappe.utils.escape_html(row.sequence || "")}. ${frappe.utils.escape_html(row.operation)}</strong>
					<span>${frappe.utils.escape_html(mark)}${row.planned_date ? ` - ${frappe.datetime.str_to_user(row.planned_date)}` : ""}</span>
				</div>`;
			})
			.join("");
		const dialog = new frappe.ui.Dialog({
			title: plan.production,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "summary",
					options: `
						<div class="se-plan-drawer">
							<p><strong>${frappe.utils.escape_html(plan.item_name || plan.finished_item)}</strong></p>
							<p class="text-muted">${se_format_number(plan.qty, 2)} ${frappe.utils.escape_html(plan.uom || "")} - ${se_format_number(plan.weight, 3)} Kg</p>
							${selected_card ? `
								<div class="se-drawer-focus">
									<div><strong>${__("Operation")}</strong><span>${frappe.utils.escape_html(selected_card.operation || "-")}</span></div>
									<div><strong>${__("Workstation")}</strong><span>${frappe.utils.escape_html(selected_card.workstation || "-")}</span></div>
									<div><strong>${__("Planned")}</strong><span>${frappe.utils.escape_html(get_planned_time_label(selected_card) || "-")}</span></div>
									<div><strong>${__("Employee")}</strong><span>${frappe.utils.escape_html(selected_card.operator_name || "-")}</span></div>
								</div>
							` : ""}
							<p>${__("Compound")}: ${frappe.utils.escape_html(plan.compound || "-")}<br>${__("Base Colour")}: ${frappe.utils.escape_html(plan.base_colour || "-")}<br>${__("Route")}: ${frappe.utils.escape_html(plan.route || "-")}</p>
							<hr>
							${rows}
						</div>
					`,
				},
			],
			primary_action_label: __("Open Production"),
			primary_action: () => frappe.set_route("Form", "Final EB BOM Generator", plan.production),
		});
		dialog.show();
	}

	stop_timers() {
		if (this.timer_interval) {
			clearInterval(this.timer_interval);
			this.timer_interval = null;
		}
	}

	start_timers() {
		const tick = () => {
			this.$body.find(".se-running-timer[data-elapsed]").each((_, element) => {
				const $element = $(element);
				let seconds = cint($element.attr("data-elapsed"));
				if ($element.attr("data-running") === "1") {
					seconds += Math.floor((Date.now() - cint($element.attr("data-rendered-at"))) / 1000);
				}
				$element.text(`${__("Elapsed Time")}: ${format_duration_seconds(seconds)}`);
			});
			this.$body.find(".se-downtime-timer[data-elapsed]").each((_, element) => {
				const $element = $(element);
				const seconds = cint($element.attr("data-elapsed")) + Math.floor((Date.now() - cint($element.attr("data-rendered-at"))) / 1000);
				$element.text(`${__("Downtime")}: ${format_duration_seconds(seconds)}`);
			});
		};
		tick();
		this.timer_interval = setInterval(tick, 1000);
	}
};

function get_monday(date_value) {
	const date = new Date(`${date_value}T00:00:00`);
	const day = date.getDay();
	const diff = date.getDate() - day + (day === 0 ? -6 : 1);
	date.setDate(diff);
	return date.toISOString().slice(0, 10);
}

function se_format_number(value, precision) {
	return flt(value || 0, precision).toLocaleString(undefined, {
		minimumFractionDigits: 0,
		maximumFractionDigits: precision,
	});
}

function get_time_from_datetime(value) {
	if (!value) return "";
	const parts = String(value).split(" ");
	return (parts[1] || parts[0] || "").slice(0, 5);
}

function get_planned_time_label(card) {
	const start = get_time_from_datetime(card.planned_start);
	const end = get_time_from_datetime(card.planned_end);
	if (start && end) {
		return `${start}–${end}`;
	}
	return start || "";
}

function get_card_status(card) {
	if (
		card.work_order_is_partial &&
		card.job_status !== "In Progress" &&
		card.job_status !== "Paused" &&
		card.status !== "Completed" &&
		card.status !== "Cancelled"
	) {
		return "Partially Completed";
	}
	if (card.job_status === "Draft") {
		return card.status;
	}
	if (card.plan_type === "Required Material" && card.job_status === "Completed" && card.status !== "Completed") {
		return card.status;
	}
	if (card.job_status === "Completed" && card.job_completion_type === "Partial Completion") {
		return card.status;
	}
	return card.job_status || card.status;
}

function get_status_label(status) {
	if (status === "In Progress") {
		return __("Running");
	}
	if (status === "On Hold") {
		return __("Paused");
	}
	return __(status || "Pending");
}

function get_remaining_qty_label(card) {
	const parts = [];
	if (cint(card.work_order_remaining_batches)) {
		parts.push(`${cint(card.work_order_remaining_batches)} ${__("batches remaining")}`);
	}
	if (flt(card.work_order_remaining_qty)) {
		parts.push(`${se_format_number(card.work_order_remaining_qty, 3)} ${card.uom || ""} ${__("remaining")}`);
	}
	if (flt(card.job_remaining_qty_meter)) {
		parts.push(`${se_format_number(card.job_remaining_qty_meter, 2)} m ${__("remaining")}`);
	}
	if (flt(card.job_remaining_qty_kg)) {
		parts.push(`${se_format_number(card.job_remaining_qty_kg, 3)} Kg ${__("remaining")}`);
	}
	return parts.join(" • ");
}

function get_released_qty_label(card) {
	const parts = [];
	if (flt(card.released_qty_meter)) {
		parts.push(`${se_format_number(card.released_qty_meter, 2)} m ${__("eligible")}`);
	}
	if (flt(card.released_qty_kg)) {
		parts.push(`${se_format_number(card.released_qty_kg, 3)} Kg ${__("eligible")}`);
	}
	return parts.join(" • ");
}

function get_timer_html(card, running) {
	const seconds = cint(card.job_productive_elapsed_seconds);
	if (!running && !seconds) {
		return "";
	}
	return `<div class="se-running-timer" data-elapsed="${seconds}" data-rendered-at="${Date.now()}" data-running="${running ? 1 : 0}">${__("Elapsed Time")}: ${format_duration_seconds(seconds)}</div>`;
}

function get_downtime_timer_html(card) {
	const seconds = cint(card.downtime_elapsed_seconds);
	if (!card.downtime_start && !seconds) {
		return "";
	}
	return `<div class="se-downtime-timer" data-elapsed="${seconds}" data-rendered-at="${Date.now()}">${__("Downtime")}: ${format_duration_seconds(seconds)}</div>`;
}

function compute_planned_end(start, duration_hours) {
	if (!start || !duration_hours) return "";
	const parts = String(start).split(":").map((part) => cint(part));
	const date = new Date();
	date.setHours(parts[0] || 0, parts[1] || 0, 0, 0);
	date.setMinutes(date.getMinutes() + flt(duration_hours) * 60);
	return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function set_schedule_date_limits(dialog, date_field, time_field) {
	const today = frappe.datetime.get_today();
	const date_input = dialog.fields_dict[date_field]?.$input;
	date_input?.attr("min", today);
	const update_time_min = () => {
		const selected_date = dialog.get_value(date_field);
		const time_input = dialog.fields_dict[time_field]?.$input;
		if (!time_input) return;
		if (selected_date === today) {
			const system_now = frappe.datetime.system_datetime();
			time_input.attr("min", system_now.slice(11, 16));
		} else {
			time_input.removeAttr("min");
		}
	};
	dialog.fields_dict[date_field]?.$input?.on("change", update_time_min);
	update_time_min();
}

function validate_schedule_in_browser(date_value, time_value) {
	if (!date_value || !time_value) return;
	const parts = String(time_value).split(":").map((part) => cint(part));
	const selected = `${date_value} ${String(parts[0] || 0).padStart(2, "0")}:${String(parts[1] || 0).padStart(2, "0")}:00`;
	const system_now = frappe.datetime.system_datetime().slice(0, 16);
	if (selected.slice(0, 16) < system_now) {
		frappe.msgprint({
			title: __("Invalid Production Planning Schedule"),
			message: `<div class="text-center">
				<div class="fa fa-exclamation-triangle text-warning" style="font-size: 24px; margin-bottom: 12px;" aria-hidden="true"></div>
				<div>${__("Production planning cannot be scheduled for a past date or time.")}</div>
			</div>`,
			indicator: "orange",
		});
		return false;
	}
	return true;
}

function format_duration_seconds(seconds) {
	seconds = Math.max(cint(seconds), 0);
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remaining = seconds % 60;
	return [hours, minutes, remaining].map((value) => String(value).padStart(2, "0")).join(":");
}

function format_qty(value, uom) {
	if (!flt(value)) return "";
	return `${se_format_number(value, 2)} ${uom}`;
}

const CATEGORY_COLOR_CLASSES = {
	blue: "category-extrusion",
	purple: "category-printing",
	indigo: "category-slitting",
	green: "category-qc",
	teal: "category-packing",
	orange: "category-mixing",
	gray: "category-other",
};
