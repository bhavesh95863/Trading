# Copyright (c) 2013, Bhavesh and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, erpnext
from frappe import _
from frappe.utils import flt, cint, getdate, now, date_diff
from erpnext.stock.utils import add_additional_uom_columns
from erpnext.stock.report.stock_ledger.stock_ledger import get_item_group_condition

from erpnext.stock.report.stock_ageing.stock_ageing import get_fifo_queue, get_average_age
import json
from six import iteritems


def execute(filters=None):
	if not filters: filters = {}


	from_date = filters.get('from_date')
	to_date = filters.get('to_date')

	if filters.get("company"):
		company_currency = erpnext.get_company_currency(filters.get("company"))
	else:
		company_currency = frappe.db.get_single_value("Global Defaults", "default_currency")

	columns = get_columns(filters)
	items = get_items(filters)
	sle = get_stock_ledger_entries(filters, items)

	# if no stock ledger entry found return
	if not sle:
		return columns, []

	iwb_map = get_item_warehouse_map(filters, sle)
	item_map = get_item_details(items, sle, filters)
	# item_reorder_detail_map = get_item_reorder_details(item_map.keys())

	data = []
	conversion_factors = {}

	_func = lambda x: x[1]

	for (company, item, warehouse) in sorted(iwb_map):
		if item_map.get(item):
			qty_dict = iwb_map[(company, item, warehouse)]

			report_data = {
				'currency': company_currency,
				'item_code': item,
				'warehouse': warehouse,
				'company': company
			}
			report_data.update(item_map[item])
			report_data.update(qty_dict)
			data.append(report_data)
	# frappe.errprint(data)
	update_data = get_item_group_wise_balance(data)
	return columns, update_data


def get_columns(filters):
	columns = [
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 100},
		{"label": _("Balance Qty"), "fieldname": "bal_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Balance Value"), "fieldname": "bal_val", "fieldtype": "Currency", "width": 100, "options": "currency"}
	]
	return columns

def get_item_group_wise_balance(data):
	
	item_group_details = {}
	for row in data:
		old_balance_item_group_wise = item_group_details.get(row.get('item_group'))
		old_bal = old_qty = 0
		if old_balance_item_group_wise:
			old_bal = old_balance_item_group_wise.get('bal_val')
			old_qty = old_balance_item_group_wise.get('bal_qty')
		group_row = dict(
			item_group = row.get('item_group'),
			bal_qty = row.get('bal_qty') + old_qty,
			bal_val = row.get('bal_val') + old_bal
		)
		item_group_details.setdefault(row.get('item_group'),frappe._dict())
		item_group_details[row.get('item_group')] = group_row
	data = []
	for value in item_group_details.values():
		data.append(value)
	return data


def get_items(filters):
	conditions = []
	if filters.get("item_code"):
		conditions.append("item.name=%(item_code)s")
	else:
		if filters.get("item_group"):
			conditions.append(get_item_group_condition(filters.get("item_group")))

	items = []
	if conditions:
		items = frappe.db.sql_list("""select name from `tabItem` item where {}"""
			.format(" and ".join(conditions)), filters)
	return items

def filter_items_with_no_transactions(iwb_map, float_precision):
	for (company, item, warehouse) in sorted(iwb_map):
		qty_dict = iwb_map[(company, item, warehouse)]

		no_transactions = True
		for key, val in iteritems(qty_dict):
			val = flt(val, float_precision)
			qty_dict[key] = val
			if key != "val_rate" and val:
				no_transactions = False

		if no_transactions:
			iwb_map.pop((company, item, warehouse))

	return iwb_map

def get_item_details(items, sle, filters):
	item_details = {}
	if not items:
		items = list(set([d.item_code for d in sle]))

	if not items:
		return item_details

	# cf_field = cf_join = ""
	# if filters.get("include_uom"):
	# 	cf_field = ", ucd.conversion_factor"
	# 	cf_join = "left join `tabUOM Conversion Detail` ucd on ucd.parent=item.name and ucd.uom=%s" \
	# 		% frappe.db.escape(filters.get("include_uom"))

	res = frappe.db.sql("""
		select
			item.name,item.item_group
		from
			`tabItem` item
		where
			item.name in (%s)
	""" % (','.join(['%s'] *len(items))), items, as_dict=1)

	for item in res:
		item_details.setdefault(item.name, item)

	return item_details

def get_stock_ledger_entries(filters, items):
	item_conditions_sql = ''
	if items:
		item_conditions_sql = ' and sle.item_code in ({})'\
			.format(', '.join([frappe.db.escape(i, percent=False) for i in items]))

	conditions = get_conditions(filters)

	return frappe.db.sql("""
		select
			sle.item_code, warehouse, sle.posting_date, sle.actual_qty, sle.valuation_rate,
			sle.company, sle.voucher_type, sle.qty_after_transaction, sle.stock_value_difference,
			sle.item_code as name, sle.voucher_no
		from
			`tabStock Ledger Entry` sle force index (posting_sort_index)
		where sle.docstatus < 2 %s %s
		and is_cancelled = 0
		order by sle.posting_date, sle.posting_time, sle.creation, sle.actual_qty""" % #nosec
		(item_conditions_sql, conditions), as_dict=1)

def get_conditions(filters):
	conditions = ""
	if not filters.get("from_date"):
		frappe.throw(_("'From Date' is required"))

	if filters.get("to_date"):
		conditions += " and sle.posting_date <= %s" % frappe.db.escape(filters.get("to_date"))
	else:
		frappe.throw(_("'To Date' is required"))

	if filters.get("company"):
		conditions += " and sle.company = %s" % frappe.db.escape(filters.get("company"))

	return conditions


def get_item_warehouse_map(filters, sle):
	iwb_map = {}
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))

	float_precision = cint(frappe.db.get_default("float_precision")) or 3

	for d in sle:
		key = (d.company, d.item_code, d.warehouse)
		if key not in iwb_map:
			iwb_map[key] = frappe._dict({
				"opening_qty": 0.0, "opening_val": 0.0,
				"in_qty": 0.0, "in_val": 0.0,
				"out_qty": 0.0, "out_val": 0.0,
				"bal_qty": 0.0, "bal_val": 0.0,
				"val_rate": 0.0
			})

		qty_dict = iwb_map[(d.company, d.item_code, d.warehouse)]

		if d.voucher_type == "Stock Reconciliation":
			qty_diff = flt(d.qty_after_transaction) - flt(qty_dict.bal_qty)
		else:
			qty_diff = flt(d.actual_qty)

		value_diff = flt(d.stock_value_difference)

		if d.posting_date < from_date:
			qty_dict.opening_qty += qty_diff
			qty_dict.opening_val += value_diff

		elif d.posting_date >= from_date and d.posting_date <= to_date:
			if flt(qty_diff, float_precision) >= 0:
				qty_dict.in_qty += qty_diff
				qty_dict.in_val += value_diff
			else:
				qty_dict.out_qty += abs(qty_diff)
				qty_dict.out_val += abs(value_diff)

		qty_dict.val_rate = d.valuation_rate
		qty_dict.bal_qty += qty_diff
		qty_dict.bal_val += value_diff

	iwb_map = filter_items_with_no_transactions(iwb_map, float_precision)

	return iwb_map