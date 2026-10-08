import frappe

DEFAULT_COST_HEADS = (
	{
		"cost_head_name": "Primer",
		"cost_type": "Consumable",
		"basis": "Per mm per meter",
		"rate": 0.020,
	},
	{
		"cost_head_name": "Printing Ink",
		"cost_type": "Consumable",
		"basis": "Per mm per meter",
		"rate": 0.050,
	},
	{
		"cost_head_name": "UV Coating",
		"cost_type": "Consumable",
		"basis": "Per mm per meter",
		"rate": 0.025,
	},
)

DEFAULT_WORKSTATIONS = (
	"Single Mould Extrusion",
	"Wide Sheet Extrusion",
	"400 mm Slitting Machine",
	"80 mm Small Slitting Machine",
	"Offline Printing Machine",
	"QC After Extrusion",
	"QC After Offline Printing",
	"Packing",
)

DEFAULT_OPERATIONS = (
	("Single Mould Extrusion", "Single Mould Extrusion"),
	("Wide Sheet Extrusion", "Wide Sheet Extrusion"),
	("Single Mould Extrusion + Integrated Online Printing", "Single Mould Extrusion"),
	("Online 80 mm Slitting", "80 mm Small Slitting Machine"),
	("400 mm Slitting", "400 mm Slitting Machine"),
	("Offline 80 mm Slitting", "80 mm Small Slitting Machine"),
	("Offline Printing", "Offline Printing Machine"),
	("QC After Extrusion", "QC After Extrusion"),
	("QC After Offline Printing", "QC After Offline Printing"),
	("Packing", "Packing"),
)

DEFAULT_ROUTES = (
	{
		"route_name": "Solid EB - Single Mould",
		"printing_type": "Solid",
		"printing_route": "None",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_type": "None",
		"operations": [
			("Single Mould Extrusion", "Single Mould Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Primer"],
	},
	{
		"route_name": "Solid EB - Wide Sheet",
		"printing_type": "Solid",
		"printing_route": "None",
		"extrusion_type": "Wide Sheet Extrusion",
		"slitting_type": "400 mm Wide Sheet Slitting",
		"operations": [
			("Wide Sheet Extrusion", "Wide Sheet Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("400 mm Slitting", "400 mm Slitting Machine"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Primer"],
	},
	{
		"route_name": "Online Printed EB",
		"printing_type": "",
		"printing_route": "Online",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_type": "None",
		"operations": [
			("Single Mould Extrusion + Integrated Online Printing", "Single Mould Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Primer", "Printing Ink", "UV Coating"],
	},
	{
		"route_name": "Online Printed EB - 80 mm Slitting",
		"printing_type": "",
		"printing_route": "Online",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_type": "Online 80 mm Slitting",
		"operations": [
			("Single Mould Extrusion + Integrated Online Printing", "Single Mould Extrusion"),
			("Online 80 mm Slitting", "80 mm Small Slitting Machine"),
			("QC After Extrusion", "QC After Extrusion"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Primer", "Printing Ink", "UV Coating"],
	},
	{
		"route_name": "Offline Printed EB - Single Mould",
		"printing_type": "",
		"printing_route": "Offline",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_type": "None",
		"operations": [
			("Single Mould Extrusion", "Single Mould Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("Offline Printing", "Offline Printing Machine"),
			("QC After Offline Printing", "QC After Offline Printing"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Printing Ink", "UV Coating"],
	},
	{
		"route_name": "Offline Printed EB - 80 mm Slitting",
		"printing_type": "",
		"printing_route": "Offline",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_type": "Offline 80 mm Slitting",
		"operations": [
			("Single Mould Extrusion", "Single Mould Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("Offline 80 mm Slitting", "80 mm Small Slitting Machine"),
			("Offline Printing", "Offline Printing Machine"),
			("QC After Offline Printing", "QC After Offline Printing"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Printing Ink", "UV Coating"],
	},
	{
		"route_name": "Offline Printed EB - Wide Sheet",
		"printing_type": "",
		"printing_route": "Offline",
		"extrusion_type": "Wide Sheet Extrusion",
		"slitting_type": "400 mm Wide Sheet Slitting",
		"operations": [
			("Wide Sheet Extrusion", "Wide Sheet Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("400 mm Slitting", "400 mm Slitting Machine"),
			("Offline Printing", "Offline Printing Machine"),
			("QC After Offline Printing", "QC After Offline Printing"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Printing Ink", "UV Coating"],
	},
	{
		"route_name": "Offline Printed EB - Wide Sheet 80 mm Slitting",
		"product_type": "Solid",
		"printing_type": "High Gloss",
		"printing_route": "Offline",
		"extrusion_type": "Wide Sheet Extrusion",
		"slitting_type": "Offline 80 mm Slitting",
		"operations": [
			("Wide Sheet Extrusion", "Wide Sheet Extrusion"),
			("QC After Extrusion", "QC After Extrusion"),
			("Offline 80 mm Slitting", "80 mm Small Slitting Machine"),
			("Offline Printing", "Offline Printing Machine"),
			("QC After Offline Printing", "QC After Offline Printing"),
			("Packing", "Packing"),
		],
		"cost_heads": ["Printing Ink", "UV Coating"],
	},
)


def setup_smartedge_manufacturing():
	ensure_workstation_cost_fields()
	ensure_operation_planning_fields()
	ensure_workstations()
	ensure_operations()
	ensure_cost_heads()
	ensure_process_routes()


def ensure_workstation_cost_fields():
	create_custom_field(
		"Workstation",
		"custom_machine_cost_per_hour",
		{
			"fieldtype": "Currency",
			"label": "SmartEdge Machine Cost / Hour",
			"insert_after": "hour_rate",
		},
	)
	create_custom_field(
		"Workstation",
		"custom_labour_cost_per_hour",
		{
			"fieldtype": "Currency",
			"label": "SmartEdge Labour Cost / Hour",
			"insert_after": "custom_machine_cost_per_hour",
		},
	)


def ensure_operation_planning_fields():
	create_custom_field(
		"Operation",
		"custom_planning_category",
		{
			"fieldtype": "Select",
			"label": "Planning Category",
			"options": "\nExtrusion\nSlitting\nPrinting\nQC\nPacking\nMixing\nOther",
			"insert_after": "workstation",
			"default": "Other",
		},
	)


def create_custom_field(dt, fieldname, values):
	name = f"{dt}-{fieldname}"
	if frappe.db.exists("Custom Field", name):
		return

	doc = frappe.new_doc("Custom Field")
	doc.dt = dt
	doc.fieldname = fieldname
	doc.module = "smart_edge_custom"
	for key, value in values.items():
		if value is not None:
			doc.set(key, value)
	doc.insert(ignore_permissions=True)


def ensure_workstations():
	for workstation_name in DEFAULT_WORKSTATIONS:
		if frappe.db.exists("Workstation", workstation_name):
			continue

		doc = frappe.new_doc("Workstation")
		doc.workstation_name = workstation_name
		doc.production_capacity = 1
		doc.insert(ignore_permissions=True)


def ensure_operations():
	for operation_name, workstation in DEFAULT_OPERATIONS:
		if frappe.db.exists("Operation", operation_name):
			continue

		doc = frappe.new_doc("Operation")
		doc.name = operation_name
		doc.workstation = workstation if frappe.db.exists("Workstation", workstation) else None
		doc.insert(ignore_permissions=True)


def ensure_cost_heads():
	if not frappe.db.exists("DocType", "SmartEdge Cost Head"):
		return

	for row in DEFAULT_COST_HEADS:
		if frappe.db.exists("SmartEdge Cost Head", row["cost_head_name"]):
			continue

		doc = frappe.new_doc("SmartEdge Cost Head")
		doc.cost_head_name = row["cost_head_name"]
		doc.cost_type = row["cost_type"]
		doc.basis = row["basis"]
		doc.rate = row["rate"]
		doc.enabled = 1
		doc.insert(ignore_permissions=True)


def ensure_process_routes():
	if not frappe.db.exists("DocType", "SmartEdge Process Route"):
		return

	for route in DEFAULT_ROUTES:
		if frappe.db.exists("SmartEdge Process Route", route["route_name"]):
			continue

		doc = frappe.new_doc("SmartEdge Process Route")
		doc.route_name = route["route_name"]
		doc.enabled = 1
		doc.product_type = route.get("product_type") or ""
		doc.printing_type = route["printing_type"]
		doc.printing_route = route["printing_route"]
		doc.extrusion_type = route["extrusion_type"]
		doc.slitting_type = route["slitting_type"]
		for sequence, (operation, workstation) in enumerate(route["operations"], start=1):
			doc.append(
				"operations",
				{
					"sequence": sequence,
					"operation": operation,
					"workstation": workstation,
					"planning_category": infer_planning_category(operation),
					"mandatory": 1,
				},
			)
		for cost_head in route["cost_heads"]:
			doc.append("cost_heads", {"cost_head": cost_head, "mandatory": 1})
		doc.insert(ignore_permissions=True)


def infer_planning_category(operation):
	operation_text = (operation or "").lower()
	if "extrusion" in operation_text:
		return "Extrusion"
	if "printing" in operation_text:
		return "Printing"
	if "slitting" in operation_text:
		return "Slitting"
	if "qc" in operation_text:
		return "QC"
	if "packing" in operation_text:
		return "Packing"
	return "Other"
