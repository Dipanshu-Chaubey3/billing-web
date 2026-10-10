from flask import Flask, render_template, request, redirect, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from pathlib import Path
from werkzeug.utils import secure_filename
import pytz
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import json

BASE_DIR = Path(__file__).resolve().parent
INVOICE_DIR = BASE_DIR / "invoices"
INVOICE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///billing_web.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    gst_rate = db.Column(db.Float, nullable=False)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    total = db.Column(db.Float, nullable=False)
    gst = db.Column(db.Float, nullable=False)
    final_total = db.Column(db.Float, nullable=False)
    date_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    items = db.relationship("BillItem", backref="bill", lazy=True, cascade="all, delete-orphan")

class BillItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    gst_amount = db.Column(db.Float, nullable=False)

with app.app_context():
    db.create_all()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/add_product", methods=["GET", "POST"])
def add_product():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            price = float(request.form.get("price", ""))
            stock = int(request.form.get("stock", ""))
            gst = float(request.form.get("gst", ""))
            if not name or len(name) > 100:
                raise ValueError("Product name is required (max 100 characters).")
            if not (0 <= price <= 1_000_000_000) or stock < 0 or not (0 <= gst <= 100):
                raise ValueError("Price/stock/GST value is out of range.")
        except (ValueError, TypeError):
            return "Invalid product data. Check name, non-negative price/stock, and GST from 0 to 100.", 400
        db.session.add(Product(name=name, price=price, stock=stock, gst_rate=gst))
        db.session.commit()
        return redirect("/add_product")
    return render_template("add_product.html", products=Product.query.all())

@app.route("/add_customer", methods=["GET", "POST"])
def add_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if not name or len(name) > 100 or not phone or len(phone) > 20:
            return "Customer name and phone are required (name max 100, phone max 20 characters).", 400
        db.session.add(Customer(name=name, phone=phone))
        db.session.commit()
        return redirect("/add_customer")
    return render_template("add_customer.html", customers=Customer.query.all())

@app.route("/billing", methods=["GET", "POST"])
def billing():
    products = Product.query.all()
    customers = Customer.query.all()
    if request.method == "GET":
        return render_template("billing.html", products=products, customers=customers)

    try:
        customer_id = int(request.form.get("customer", ""))
        products_data = json.loads(request.form.get("products_data", ""))
        if not isinstance(products_data, list) or not products_data:
            raise ValueError("Add at least one item to the bill.")
    except (ValueError, TypeError, json.JSONDecodeError):
        return "Invalid bill data. Select a customer and at least one product.", 400

    customer = db.session.get(Customer, customer_id)
    if customer is None:
        return "Customer not found.", 404

    subtotal = 0.0
    total_gst = 0.0
    pending_items = []
    try:
        for item in products_data:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
            if quantity <= 0:
                raise ValueError("Quantity must be a positive integer.")
            product = db.session.get(Product, product_id)
            if product is None:
                raise LookupError(f"Product ID {product_id} was not found.")
            if product.stock < quantity:
                raise ValueError(f"Not enough stock for {product.name}; available: {product.stock}.")
            line_subtotal = round(product.price * quantity, 2)
            line_gst = round(line_subtotal * product.gst_rate / 100, 2)
            subtotal += line_subtotal
            total_gst += line_gst
            pending_items.append((product, quantity, line_gst))
    except LookupError as exc:
        db.session.rollback()
        return str(exc), 404
    except (KeyError, ValueError, TypeError) as exc:
        db.session.rollback()
        return f"Invalid bill item: {exc}", 400

    bill = Bill(customer_id=customer_id, total=round(subtotal, 2),
                gst=round(total_gst, 2), final_total=round(subtotal + total_gst, 2))
    try:
        db.session.add(bill)
        db.session.flush()
        for product, quantity, line_gst in pending_items:
            db.session.add(BillItem(
                bill_id=bill.id, product_id=product.id, quantity=quantity,
                price=product.price, gst_amount=line_gst
            ))
            product.stock -= quantity
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not save bill")
        return "Could not save the bill. No changes were saved; please try again.", 500

    try:
        generate_pdf_invoice(bill.id)
    except Exception:
        app.logger.exception("Invoice PDF generation failed for bill %s", bill.id)
        return f"Bill #{bill.id} was saved, but the PDF could not be generated. Please retry or contact support.", 500
    return redirect(f"/invoice/{bill.id}")

@app.route("/invoice/<int:bill_id>")
def invoice(bill_id):
    path = INVOICE_DIR / f"Invoice_{bill_id}.pdf"
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)

def generate_pdf_invoice(bill_id):
    bill = db.session.get(Bill, bill_id)
    if bill is None:
        raise ValueError("Bill not found")
    customer = db.session.get(Customer, bill.customer_id)
    if customer is None:
        raise ValueError("Customer not found")
    utc_value = bill.date_time.replace(tzinfo=timezone.utc) if bill.date_time.tzinfo is None else bill.date_time
    date_time = utc_value.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M %p")
    items = BillItem.query.filter_by(bill_id=bill_id).all()
    filename = INVOICE_DIR / f"Invoice_{bill_id}.pdf"

    doc = SimpleDocTemplate(str(filename), pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()
    if (BASE_DIR / "static" / "logo.png").is_file():
        elements.append(Image(str(BASE_DIR / "static" / "logo.png"), width=80, height=80))
    elements.extend([
        Paragraph("CJ - Billing Invoice", styles["Heading1"]),
        Paragraph(f"Invoice ID: {bill.id}", styles["Normal"]),
        Paragraph(f"Customer: {customer.name}", styles["Normal"]),
        Paragraph(f"Phone: {customer.phone}", styles["Normal"]),
        Paragraph(f"Date &amp; Time (IST): {date_time}", styles["Normal"]),
        Spacer(1, 12)
    ])
    table_data = [["Product", "Qty", "Price", "GST", "Total"]]
    for item in items:
        product = db.session.get(Product, item.product_id)
        if product is None:
            raise ValueError(f"Product {item.product_id} no longer exists")
        line_total = item.price * item.quantity + item.gst_amount
        table_data.append([
            product.name, item.quantity, f"₹{item.price:.2f}",
            f"₹{item.gst_amount:.2f}", f"₹{line_total:.2f}"
        ])
    table = Table(table_data, colWidths=[200, 40, 80, 70, 80])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")
    ]))
    elements.extend([
        table, Spacer(1, 12),
        Paragraph(f"Subtotal: ₹{bill.total:.2f}", styles["Normal"]),
        Paragraph(f"Total GST: ₹{bill.gst:.2f}", styles["Normal"]),
        Paragraph(f"Final Total: ₹{bill.final_total:.2f}", styles["Normal"]),
        Spacer(1, 24),
        Paragraph("Thank you for your purchase! Visit again.", styles["Normal"])
    ])
    doc.build(elements)

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
