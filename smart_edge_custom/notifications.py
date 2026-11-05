def get_notification_config():
	return {
		"for_doctype": {
			"SmartEdge Unusable Scrap Lot": {
				"status": ("in", ("Pending Storage", "Pending Disposal")),
			},
		},
	}
