from __future__ import unicode_literals
from frappe import msgprint, _
import frappe
from frappe.utils import today

invoice_party_field = {"Sales Invoice":"customer","Purchase Invoice":"supplier","Sales Order":"customer","Purchase Order":"supplier"}

@frappe.whitelist()
def get_pinv_details(c_doctype,p_doctype,item,parent):
	filters = [
		["item_code","=",item],
		["docstatus","=",1],
		["parent","!=",parent]
	]
	return frappe.get_all(c_doctype,filters=filters,fields=["item_code","qty","rate","parent","parenttype"],limit_page_length=5)
	# return frappe.render_template("trading/templates/last_transaction.html",{'data':transaction_list,'doctype':p_doctype})

@frappe.whitelist()
def get_last_transaction(c_doctype,p_doctype,item,parent,party,sinv=False):
	invoice_details = frappe.db.sql("""SELECT c.parent AS 'parent',
       c.item_code AS 'item_code',
       c.qty AS 'qty',
       c.rate AS 'rate',
	   c.parenttype AS 'parenttype'
FROM `tab{0}` AS p
INNER JOIN `tab{1}` AS c ON p.name=c.parent
WHERE p.docstatus=1 and {2}='{3}'
  AND c.item_code='{4}'
  AND c.parent<>'{5}' limit 5""".format(p_doctype,c_doctype,invoice_party_field.get(p_doctype),party,item,parent),as_dict=1)
	frappe.errprint(invoice_details)
	if sinv:
		pinv_details = get_pinv_details('Purchase Invoice Item','Purchase Invoice',item,parent)
		if pinv_details:
			invoice_details.extend(pinv_details)
	return frappe.render_template("trading/templates/last_transaction.html",{'data':invoice_details,'doctype':p_doctype})
	


	
@frappe.whitelist()
def uom_query(doctype, txt, searchfield, start, page_len, filters):
	if not filters.get('item_code'):
		frappe.throw(_("Select Item First"))
	uom_list = frappe.db.sql("""SELECT uom
FROM `tabUOM Conversion Detail`
WHERE parent=%s""",filters.get('item_code'))
	return uom_list