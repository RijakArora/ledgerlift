"""CSV export of extracted documents.

Two shapes, because bookkeepers actually need both:
- "summary": one row per document (what you reconcile against a bank feed)
- "line_items": one row per line item (what you post to expense categories)
"""
import csv
import io

SUMMARY_COLUMNS = [
    "uploaded_at", "filename", "status", "document_type", "vendor_name",
    "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "tax", "shipping", "total",
]

LINE_ITEM_COLUMNS = [
    "uploaded_at", "filename", "vendor_name", "invoice_number", "invoice_date",
    "currency", "description", "quantity", "unit_price", "amount",
]


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def documents_to_csv(documents, shape: str = "summary") -> str:
    out = io.StringIO()
    writer = csv.writer(out)

    if shape == "line_items":
        writer.writerow(LINE_ITEM_COLUMNS)
        for doc in documents:
            for item in doc.line_items:
                writer.writerow([
                    _fmt_dt(doc.uploaded_at), doc.filename, doc.vendor_name,
                    doc.invoice_number, doc.invoice_date, doc.currency,
                    item.description, item.quantity, item.unit_price, item.amount,
                ])
        return out.getvalue()

    writer.writerow(SUMMARY_COLUMNS)
    for doc in documents:
        writer.writerow([
            _fmt_dt(doc.uploaded_at), doc.filename, doc.status, doc.document_type,
            doc.vendor_name, doc.invoice_number, doc.invoice_date, doc.due_date,
            doc.currency, doc.subtotal, doc.tax, doc.shipping, doc.total,
        ])
    return out.getvalue()
