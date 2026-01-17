from flask import Flask, render_template, request, redirect, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pytz
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import json

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///billing_web.db'
db = SQLAlchemy(app)

# -------------------- Models --------------------
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
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    total = db.Column(db.Float)
    gst = db.Column(db.Float)
    final_total = db.Column(db.Float)
    date_time = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('BillItem', backref='bill', lazy=True)

class BillItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bill.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer)
    price = db.Column(db.Float)
    gst_amount = db.Column(db.Float)

# -------------------- Create DB --------------------
with app.app_context():
    db.create_all()

# -------------------- Routes --------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/add_product', methods=['GET','POST'])
def add_product():
    if request.method=='POST':
        name = request.form['name']
        price = float(request.form['price'])
        stock = int(request.form['stock'])
        gst = float(request.form['gst'])
        db.session.add(Product(name=name, price=price, stock=stock, gst_rate=gst))
        db.session.commit()
        return redirect('/add_product')
    products = Product.query.all()
    return render_template('add_product.html', products=products)

@app.route('/add_customer', methods=['GET','POST'])
def add_customer():
    if request.method=='POST':
        name = request.form['name']
        phone = request.form['phone']
        db.session.add(Customer(name=name, phone=phone))
        db.session.commit()
        return redirect('/add_customer')
    customers = Customer.query.all()
    return render_template('add_customer.html', customers=customers)

@app.route('/billing', methods=['GET','POST'])
def billing():
    products = Product.query.all()
    customers = Customer.query.all()
    if request.method == 'POST':
        customer_id = int(request.form['customer'])
        products_json = request.form['products_data']
        products_data = json.loads(products_json)

        customer = Customer.query.get(customer_id)
        if not customer:
            return "Customer not found", 404

        subtotal = 0
        total_gst = 0
        bill_items = []

        for item in products_data:
            product = Product.query.get(item['product_id'])
            if not product:
                return f"Product ID {item['product_id']} not found", 404
            qty = int(item['quantity'])
            gst_amt = product.price * qty * product.gst_rate / 100
            total_price = product.price * qty + gst_amt
            subtotal += product.price * qty
            total_gst += gst_amt
            bill_items.append({'product': product, 'qty': qty, 'price': product.price, 'gst': gst_amt, 'total': total_price})
            product.stock -= qty  # Stock update will be committed later

        final_total = subtotal + total_gst
        bill = Bill(customer_id=customer_id, total=subtotal, gst=total_gst, final_total=final_total)
        db.session.add(bill)
        db.session.commit()  # Commits bill and stock updates
        for item_data in bill_items:
            db.session.add(BillItem(bill_id=bill.id, product_id=item_data['product'].id, quantity=item_data['qty'], price=item_data['price'], gst_amount=item_data['gst']))
        db.session.commit()

        generate_pdf_invoice(bill.id)
        return redirect(f'/invoice/{bill.id}')

    return render_template('billing.html', products=products, customers=customers)

@app.route('/invoice/<int:bill_id>')
def invoice(bill_id):
    return send_file(f'invoices/Invoice_{bill_id}.pdf', as_attachment=True)

# -------------------- PDF Invoice --------------------
def generate_pdf_invoice(bill_id):
    bill = Bill.query.get(bill_id)
    ist = pytz.timezone('Asia/Kolkata')
    date_time = bill.date_time.astimezone(ist).strftime('%d-%m-%Y %I:%M %p')
    customer = Customer.query.get(bill.customer_id)
    items = BillItem.query.filter_by(bill_id=bill_id).all()

    if not os.path.exists("invoices"):
        os.mkdir("invoices")
    filename = f"invoices/Invoice_{bill_id}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=30,leftMargin=30, topMargin=30,bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()
    styleN = styles['Normal']
    styleH = styles['Heading1']

    if os.path.exists("static/logo.png"):
        im = Image("static/logo.png", width=80, height=80)
        elements.append(im)

    elements.append(Paragraph("CJ - Billing Invoice", styleH))
    elements.append(Paragraph(f"Invoice ID: {bill.id}", styleN))
    elements.append(Paragraph(f"Customer: {customer.name}", styleN))
    elements.append(Paragraph(f"Phone: {customer.phone}", styleN))
    elements.append(Paragraph(f"Date & Time (IST): {date_time}", styleN))
    elements.append(Spacer(1,12))

    table_data = [["Product","Qty","Price","GST","Total"]]
    for item in items:
        product = Product.query.get(item.product_id)
        table_data.append([product.name, item.quantity, f"₹{item.price:.2f}", f"₹{item.gst_amount:.2f}", f"₹{item.price*item.quantity+item.gst_amount:.2f}"])

    table = Table(table_data, colWidths=[200,50,80,80,80])
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.lightblue),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('ALIGN',(1,1),(-1,-1),'RIGHT'),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONT',(0,0),(-1,0),'Helvetica-Bold')
    ]))
    elements.append(table)
    elements.append(Spacer(1,12))
    elements.append(Paragraph(f"Subtotal: ₹{bill.total:.2f}", styleN))
    elements.append(Paragraph(f"Total GST: ₹{bill.gst:.2f}", styleN))
    elements.append(Paragraph(f"Final Total: ₹{bill.final_total:.2f}", styleN))
    elements.append(Spacer(1,24))
    elements.append(Paragraph("Thank you for your purchase! Visit again.", styleN))

    doc.build(elements)

if __name__=="__main__":
    app.run(debug=True)