# Billing Web

A learning project using Flask, Flask-SQLAlchemy, SQLite, and ReportLab to manage products/customers and generate PDF invoices.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
``
Open http://127.0.0.1:5000.

## Workflow
1. Add products with price, stock, and GST percentage.
2. Add a customer.
3. Create a bill with positive quantities that do not exceed available stock.
4. Download the generated PDF invoice.

This is a local prototype, not production accounting software. It has no authentication or role-based access control. Review tax calculations and business requirements before using it for real transactions.
