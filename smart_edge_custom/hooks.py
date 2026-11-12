app_name = "smart_edge_custom"
app_publisher = "Smart Edge"
app_description = "Custom ERPNext and HRMS customizations"
app_email = "info@smartedge.com"
app_license = "mit"
app_title = "SmartEdge Custom"
app_logo_url = "/assets/smart_edge_custom/images/smartedge-logo.svg"
app_home = "/app/smartedge-manufacturing"

add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": app_title,
		"route": app_home,
		"sequence_id": 50,
	}
]

fixtures = [
	{"dt": "Custom Field", "filters": [["module", "=", "smart_edge_custom"]]},
	{"dt": "Property Setter", "filters": [["module", "=", "smart_edge_custom"]]},
	{"dt": "Workspace", "filters": [["module", "=", "smart_edge_custom"]]},
	{"dt": "Number Card", "filters": [["module", "=", "smart_edge_custom"]]},
	{"dt": "SmartEdge Cost Head"},
	{"dt": "SmartEdge Process Route"},
]

# Apps
# ------------------

required_apps = ["frappe/erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "smart_edge_custom",
# 		"logo": "/assets/smart_edge_custom/logo.png",
# 		"title": "smart_edge_custom",
# 		"route": "/smart_edge_custom",
# 		"has_permission": "smart_edge_custom.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/smart_edge_custom/css/base_colour.css"
# app_include_js = "/assets/smart_edge_custom/js/smart_edge_custom.js"

# include js, css files in header of web template
# web_include_css = "/assets/smart_edge_custom/css/smart_edge_custom.css"
# web_include_js = "/assets/smart_edge_custom/js/smart_edge_custom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "smart_edge_custom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
page_js = {"smartedge-packing": "public/js/smartedge_packing.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_js = {
	"Batch": "public/js/batch.js",
	"Base Colour": "public/js/base_colour.js",
	"BOM": "public/js/bom.js",
	"Compound": "public/js/compound.js",
	"Item": "public/js/item.js",
	"Sales Order": "public/js/sales_order.js",
	"Workstation": "public/js/workstation.js",
	"Work Order": "public/js/work_order.js",
	"SmartEdge Production Lot": "public/js/smartedge_production_lot.js",
}
doctype_list_js = {
	"Sales Order": "public/js/sales_order_list.js",
	"Batch": "public/js/batch_list.js",
	"Base Colour": "public/js/base_colour_list.js",
	"Compound": "public/js/manufacturing_masters.js",
	"Size Weight": "public/js/manufacturing_masters.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

override_whitelisted_methods = {
	"erpnext.controllers.queries.get_batch_no": "smart_edge_custom.manufacturing.operation_batch.get_batch_no",
	"erpnext.controllers.queries.get_batch_numbers": "smart_edge_custom.manufacturing.operation_batch.get_batch_numbers",
}

override_doctype_class = {
	"Quality Inspection": "smart_edge_custom.manufacturing.quality_control.SmartEdgeQualityInspection",
}

jinja = {
	"methods": ["smart_edge_custom.manufacturing.production_lot.get_label_qr_code"],
}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "smart_edge_custom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "smart_edge_custom.utils.jinja_methods",
# 	"filters": "smart_edge_custom.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "smart_edge_custom.install.before_install"
after_install = "smart_edge_custom.setup.setup_smartedge_manufacturing"

# Uninstallation
# ------------

# before_uninstall = "smart_edge_custom.uninstall.before_uninstall"
# after_uninstall = "smart_edge_custom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "smart_edge_custom.utils.before_app_install"
# after_app_install = "smart_edge_custom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "smart_edge_custom.utils.before_app_uninstall"
# after_app_uninstall = "smart_edge_custom.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "smart_edge_custom.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

notification_config = "smart_edge_custom.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

doc_events = {
	"Quality Inspection": {
		"validate": "smart_edge_custom.manufacturing.quality_control.validate_quality_inspection",
	},
	"Batch": {"validate": "smart_edge_custom.manufacturing.operation_batch.validate_batch"},
	"Serial and Batch Bundle": {"before_validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Stock Ledger Entry": {"before_insert": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Pick List": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Stock Reservation Entry": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Delivery Note": {
		"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock",
		"on_submit": "smart_edge_custom.manufacturing.packing.on_delivery_note_submit",
		"on_cancel": "smart_edge_custom.manufacturing.packing.on_delivery_note_cancel",
	},
	"Purchase Receipt": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Purchase Invoice": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Sales Invoice": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Stock Reconciliation": {"validate": "smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock"},
	"Base Colour": {
		"validate": "smart_edge_custom.base_colour.validate_base_colour",
	},
	"BOM": {
		"before_validate": "smart_edge_custom.bom.set_recipe_quantity",
		"validate": "smart_edge_custom.bom.validate_bom",
	},
	"Item": {
		"validate": "smart_edge_custom.item.validate_pvc_edge_band_item",
	},
	"Stock Entry": {
		"before_cancel": [
			"smart_edge_custom.manufacturing.physical_operation_stock.prevent_upstream_cancellation",
			"smart_edge_custom.manufacturing.scrap_recovery.prevent_recovery_cancellation",
			"smart_edge_custom.manufacturing.unusable_scrap.prevent_unusable_scrap_cancellation",
		],
		"before_validate": [
			"smart_edge_custom.manufacturing.operation_batch.reject_operation_batches_in_stock",
			"smart_edge_custom.manufacturing.unusable_scrap.reject_unusable_scrap_consumption",
			"smart_edge_custom.manufacturing.batch_manufacturing.validate_batch_stock_entry_quantity",
		],
		"before_submit": "smart_edge_custom.manufacturing.batch_manufacturing.allocate_finished_batches",
		"on_submit": [
			"smart_edge_custom.manufacturing.material_production.sync_material_readiness_from_stock_entry",
			"smart_edge_custom.manufacturing.batch_genealogy.sync_genealogy_from_stock_entry",
		],
		"on_cancel": [
			"smart_edge_custom.manufacturing.material_production.sync_material_readiness_from_stock_entry",
			"smart_edge_custom.manufacturing.batch_genealogy.sync_genealogy_from_stock_entry",
			"smart_edge_custom.manufacturing.scrap_recovery.sync_recovery_cancellation",
			"smart_edge_custom.manufacturing.unusable_scrap.sync_unusable_scrap_cancellation",
		],
	},
	"SmartEdge Production Job": {
		"on_update": "smart_edge_custom.manufacturing.batch_genealogy.refresh_genealogy_from_production_job",
	},
	"Sales Order": {
		"after_insert": "smart_edge_custom.manufacturing.sales_order_planning_status.update_sales_order_planning_status",
		"on_update": "smart_edge_custom.manufacturing.sales_order_planning_status.update_sales_order_planning_status",
		"on_update_after_submit": "smart_edge_custom.manufacturing.sales_order_planning_status.update_sales_order_planning_status",
		"on_cancel": "smart_edge_custom.manufacturing.sales_order_planning_status.update_sales_order_planning_status",
	},
	"Final EB BOM Generator": {
		"after_insert": "smart_edge_custom.manufacturing.sales_order_planning_status.update_from_final_eb_bom_generator",
		"on_update": "smart_edge_custom.manufacturing.sales_order_planning_status.update_from_final_eb_bom_generator",
		"on_cancel": "smart_edge_custom.manufacturing.sales_order_planning_status.update_from_final_eb_bom_generator",
		"on_trash": "smart_edge_custom.manufacturing.sales_order_planning_status.update_from_final_eb_bom_generator",
	},
	"Work Order": {
		"before_validate": "smart_edge_custom.manufacturing.batch_manufacturing.set_work_order_batch_quantity",
		"on_update": "smart_edge_custom.manufacturing.material_production.sync_material_readiness_from_work_order",
		"on_cancel": "smart_edge_custom.manufacturing.material_production.sync_material_readiness_from_work_order",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"smart_edge_custom.tasks.all"
# 	],
# 	"daily": [
# 		"smart_edge_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"smart_edge_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"smart_edge_custom.tasks.weekly"
# 	],
# 	"monthly": [
# 		"smart_edge_custom.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "smart_edge_custom.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
extend_doctype_class = {
	"Job Card": ["smart_edge_custom.manufacturing.workstation_capacity.SmartEdgeJobCardCapacityMixin"],
	"Work Order": ["smart_edge_custom.manufacturing.workstation_capacity.SmartEdgeWorkOrderCapacityMixin"],
	"Workstation": ["smart_edge_custom.manufacturing.workstation_capacity.SmartEdgeWorkstationCapacityMixin"],
}

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "smart_edge_custom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "smart_edge_custom.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["smart_edge_custom.utils.before_request"]
# after_request = ["smart_edge_custom.utils.after_request"]

# Job Events
# ----------
# before_job = ["smart_edge_custom.utils.before_job"]
# after_job = ["smart_edge_custom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"smart_edge_custom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
