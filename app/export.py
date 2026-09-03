"""CSV exports.

Four shapes, because a bookkeeper needs different things at different
points in the workflow:

- summary     : one row per document, for reconciling against a bank feed
- line_items  : one row per line item, for coding to expense accounts
- xero        : shaped for Xero's bills import
- quickbooks  : shaped for QuickBooks Online bill import

IMPORTANT CAVEAT on the accounting formats: Xero's and QuickBooks'
import templates vary by region and change over time, and their public
documentation could not be verified at build time. These exports use the
column names and layout their templates conventionally use, which gets
you most of the way - but the column headers should be checked against
the template downloaded from your own Xero/QBO account before the first
import. Account codes and tax types are left blank deliberately: only
the bookkeeper knows their own chart of accounts, and guessing an
account code would post entries to the wrong place.
"""
import csv
import io

SUMMARY_COLUMNS = [
    "uploaded_at", "client", "filename", "status", "document_type", "vendor_name",
    "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "tax", "shipping", "total", "edited", "possible_duplicate",
]

LINE_ITEM_COLUMNS = [
    "uploaded_at", "client", "filename", "vendor_name", "invoice_number", "invoice_date",
    "currency", "description", "quantity", "unit_price", "amount",
]

# Xero bills import: ContactName is the one field Xero always requires.
XERO_COLUMNS = [
    "*ContactName", "*InvoiceNumber", "*InvoiceDate", "*DueDate",
    "Description", "*Quantity", "*UnitAmount", "*AccountCode", "*TaxType",
    "TaxAmount", "Currency",
]

QUICKBOOKS_COLUMNS = [
    "Bill No", "Supplier", "Bill Date", "Due Date", "Terms", "Memo",
    "Expense Account", "Expense Description", "Expense Line Amount", "Currency",
]


def _dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _client_name(doc) -> str:
    return doc.client.name if getattr(doc, "client", None) else ""


def documents_to_csv(documents, shape: str = "summary") -> str:
    out = io.StringIO()
    w = csv.writer(out)

    if shape == "line_items":
        w.writerow(LINE_ITEM_COLUMNS)
        for d in documents:
            for i in d.line_items:
                w.writerow([_dt(d.uploaded_at), _client_name(d), d.filename, d.vendor_name,
                            d.invoice_number, d.invoice_date, d.currency,
                            i.description, i.quantity, i.unit_price, i.amount])
        return out.getvalue()

    if shape == "xero":
        w.writerow(XERO_COLUMNS)
        for d in documents:
            items = d.line_items or []
            if not items:
                # No line items - emit the document total as a single line so
                # the bill still imports rather than being dropped silently.
                w.writerow([d.vendor_name, d.invoice_number, d.invoice_date, d.due_date,
                            f"{d.document_type or 'document'} {d.filename}".strip(),
                            1, d.total if d.total is not None else "", "", "",
                            d.tax if d.tax is not None else "", d.currency])
                continue
            for i in items:
                w.writerow([d.vendor_name, d.invoice_number, d.invoice_date, d.due_date,
                            i.description, i.quantity if i.quantity is not None else 1,
                            i.unit_price if i.unit_price is not None else i.amount,
                            "", "", "", d.currency])
        return out.getvalue()

    if shape == "quickbooks":
        w.writerow(QUICKBOOKS_COLUMNS)
        for d in documents:
            items = d.line_items or []
            if not items:
                w.writerow([d.invoice_number, d.vendor_name, d.invoice_date, d.due_date, "",
                            d.filename, "", d.document_type or "", 
                            d.total if d.total is not None else "", d.currency])
                continue
            for i in items:
                w.writerow([d.invoice_number, d.vendor_name, d.invoice_date, d.due_date, "",
                            d.filename, "", i.description,
                            i.amount if i.amount is not None else "", d.currency])
        return out.getvalue()

    w.writerow(SUMMARY_COLUMNS)
    for d in documents:
        w.writerow([_dt(d.uploaded_at), _client_name(d), d.filename, d.status,
                    d.document_type, d.vendor_name, d.invoice_number, d.invoice_date,
                    d.due_date, d.currency, d.subtotal, d.tax, d.shipping, d.total,
                    "yes" if d.edited_at else "", "yes" if d.duplicate_of_id else ""])
    return out.getvalue()
