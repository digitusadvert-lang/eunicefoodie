# app.py - Updated with Payment System and Admin Settings
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g
import sqlite3
import uuid
from datetime import datetime
import requests
import os
from functools import wraps
import json
import urllib.parse
import os
import re
import uuid
from werkzeug.utils import secure_filename

def allowed_file(filename):
    """Check if the file extension is allowed"""
    allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'webp', 'bmp'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def validate_file_size(file):
    """Check if file size is within limit (5MB)"""
    file.seek(0, os.SEEK_END)
    file_length = file.tell()
    file.seek(0)  # Reset file pointer
    return file_length <= 5 * 1024 * 1024  # 5MB

def generate_secure_filename(original_filename, order_id):
    """Generate a secure and unique filename"""
    import re
    
    # Get file extension
    file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
    
    # Clean the base name
    base_name = re.sub(r'[^\w\s.-]', '', original_filename.rsplit('.', 1)[0])
    base_name = base_name.strip().replace(' ', '_')
    
    # Generate unique filename
    unique_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    return f"receipt_{order_id}_{timestamp}_{unique_id}.{file_ext}"

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['DATABASE'] = 'store.db'
app.config['UPLOAD_FOLDER'] = 'static/receipts'

# ================ ADD TEMPLATE FILTER HERE ================
@app.template_filter('datetimeformat')
def datetimeformat(value, format='%d %b %Y, %I:%M %p'):
    """Custom template filter to format datetime"""
    if value:
        if isinstance(value, str):
            # Parse string to datetime
            try:
                # Try different datetime formats
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
                    try:
                        value = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        continue
            except:
                return value
        if isinstance(value, datetime):
            return value.strftime(format)
    return ''
# ==========================================================

# Create upload folder if not exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Telegram Configuration
TELEGRAM_BOT_TOKEN = "8492843990:AAF7DFgY1tLaVhcvZGoxqcijTdtnUlZJ_Fc"
ADMIN_CHAT_ID = "1572211466"

# Shipping rates
SHIPPING_RATES = {
    'west': 7.00,
    'east': 15.00
}

# State to region mapping
STATE_REGIONS = {
    'west': ['Johor', 'Kedah', 'Kelantan', 'Melaka', 'Negeri Sembilan', 
             'Pahang', 'Penang', 'Perak', 'Perlis', 'Selangor', 'Kuala Lumpur', 'Terengganu'],
    'east': ['Sabah', 'Sarawak', 'Labuan']
}

# Payment methods
PAYMENT_METHODS = ['Bank Transfer', 'Touch \'n Go (TnG)']

def init_db():
    """Initialize database with your products"""
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    
    # Products table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            weight REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            total_price REAL NOT NULL,
            shipping_fee REAL NOT NULL,
            address TEXT NOT NULL,
            postcode TEXT NOT NULL,
            state TEXT NOT NULL,
            region TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            payment_method TEXT,
            payment_status TEXT DEFAULT 'pending',
            payment_receipt TEXT,
            payment_verified BOOLEAN DEFAULT 0,
            payment_verified_at TIMESTAMP,
            payment_verified_by TEXT,
            tracking_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Order items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            weight REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (order_id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')
    
    # Admin users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    # Admin settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE NOT NULL,
            setting_value TEXT,
            setting_type TEXT DEFAULT 'text',
            description TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default admin if not exists
    cursor.execute("SELECT * FROM admin_users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO admin_users (username, password) VALUES (?, ?)",
            ('admin', 'admin123')
        )
    
    # Insert default settings if not exists
    default_settings = [
        ('bank_account_name', 'YOUR BANK ACCOUNT NAME', 'text', 'Bank Account Holder Name'),
        ('bank_account_number', '1234567890', 'text', 'Bank Account Number'),
        ('bank_name', 'MAYBANK', 'text', 'Bank Name'),
        ('tng_phone_number', '+60123456789', 'text', 'Touch \'n Go Phone Number'),
        ('whatsapp_message', 'Hi {customer_name}, your order {order_id} is ready for payment of RM{total_price}. Please make payment via {payment_method} and upload receipt. Thank you!', 'textarea', 'WhatsApp Message Template'),
        ('admin_whatsapp_number', '+60123456789', 'text', 'Admin WhatsApp Number'),
        ('shipping_message', 'Hi {customer_name}, your order {order_id} has been shipped! Tracking number: {tracking_number}', 'textarea', 'Shipping Notification Template'),
        ('payment_instructions', 'Please make payment and upload receipt. Once verified, we will ship your order.', 'textarea', 'Payment Instructions')
    ]
    
    for key, value, type_, desc in default_settings:
        cursor.execute(
            "INSERT OR IGNORE INTO admin_settings (setting_key, setting_value, setting_type, description) VALUES (?, ?, ?, ?)",
            (key, value, type_, desc)
        )
    
    # Insert your products
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        products = [
            ('Chicken floss roll', 25.00, 0.33),
            ('Crispy crab stick', 16.00, 0.21),
            ('Crispy seaweed + chicken floss cracker', 16.00, 0.16),
            ('Crispy seaweed cracker', 10.00, 0.16),
            ('Crispy vegie snack', 12.00, 0.19),
            ('Homemade salted egg muruku', 28.00, 0.44),
            ('Low sugar twisted roll', 15.00, 0.28),
            ('Mild spicy crispy cracker', 20.00, 0.36),
            ('Peanut Cookies', 22.00, 0.32),
            ('Premium Choco Cookies', 22.00, 0.32),
            ('Scs pineapple roll', 22.00, 0.41),
            ('Soy chips original', 15.00, 0.23)
        ]
        cursor.executemany(
            "INSERT INTO products (name, price, weight) VALUES (?, ?, ?)",
            products
        )
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

@app.before_request
def before_request():
    """Set database connection before each request"""
    g.conn = get_db_connection()

@app.teardown_request
def teardown_request(exception):
    """Close database connection after each request"""
    if hasattr(g, 'conn'):
        g.conn.close()

def admin_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_settings():
    """Get all admin settings"""
    settings = g.conn.execute('SELECT * FROM admin_settings').fetchall()
    return {row['setting_key']: row['setting_value'] for row in settings}

def send_telegram_message(message):
    """Send message to admin via Telegram bot"""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️ Telegram bot token not configured!")
        return True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            print("✅ Telegram notification sent successfully")
            return True
        else:
            print(f"⚠️ Failed to send Telegram notification. Status: {response.status_code}")
            return True
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")
        return True

# ============== USER ROUTES ==============

@app.route('/')
def index():
    """Home page - redirects to user products"""
    return redirect(url_for('user_products'))

@app.route('/user/products')
def user_products():
    """Display products to user sorted by sales"""
    products = g.conn.execute('''
        SELECT p.*, 
               COALESCE(SUM(oi.quantity), 0) as total_sold,
               COALESCE(COUNT(DISTINCT oi.order_id), 0) as order_count
        FROM products p
        LEFT JOIN order_items oi ON p.id = oi.product_id
        GROUP BY p.id
        ORDER BY total_sold DESC, order_count DESC, p.name
    ''').fetchall()
    
    ranked_products = []
    for i, product in enumerate(products, 1):
        product_dict = dict(product)
        product_dict['rank'] = i
        ranked_products.append(product_dict)
    
    return render_template('user_products.html', products=ranked_products)

@app.route('/user/cart/add', methods=['POST'])
def add_to_cart():
    """Add selected products to cart and go directly to checkout"""
    cart_items = []
    
    for key, value in request.form.items():
        if key.startswith('quantity_'):
            product_id = key.replace('quantity_', '')
            quantity = int(value) if value else 0
            
            if quantity > 0:
                product = g.conn.execute(
                    'SELECT * FROM products WHERE id = ?', 
                    (product_id,)
                ).fetchone()
                
                if product:
                    cart_items.append({
                        'id': product['id'],
                        'name': product['name'],
                        'price': product['price'],
                        'weight': product['weight'],
                        'quantity': quantity
                    })
    
    session['cart'] = {str(item['id']): item for item in cart_items}
    
    if cart_items:
        return redirect(url_for('user_checkout'))
    else:
        return redirect(url_for('user_products'))

@app.route('/user/checkout', methods=['GET', 'POST'])
def user_checkout():
    """Checkout page - users reserve items here"""
    cart = session.get('cart', {})
    
    if not cart:
        return redirect(url_for('user_products'))
    
    cart_items = list(cart.values())
    subtotal = sum(item['price'] * item['quantity'] for item in cart_items)
    
    # Get settings for the template
    settings = get_settings()
    
    if request.method == 'POST':
        # Get customer information
        customer_name = request.form.get('customer_name')
        contact_number = request.form.get('contact_number')
        address = request.form.get('address')
        postcode = request.form.get('postcode')
        state = request.form.get('state')
        
        # Validate required fields
        if not all([customer_name, contact_number, address, postcode, state]):
            return render_template('user_checkout.html', 
                                 cart_items=cart_items, 
                                 subtotal=subtotal,
                                 states=STATE_REGIONS['west'] + STATE_REGIONS['east'],
                                 payment_methods=PAYMENT_METHODS,  # Add this
                                 settings=settings,  # Add this
                                 error='Please fill in all required fields')
        
        # Validate contact number
        import re
        if not re.match(r'^[0-9]{10,11}$', contact_number):
            return render_template('user_checkout.html', 
                                 cart_items=cart_items, 
                                 subtotal=subtotal,
                                 states=STATE_REGIONS['west'] + STATE_REGIONS['east'],
                                 payment_methods=PAYMENT_METHODS,  # Add this
                                 settings=settings,  # Add this
                                 error='Please enter a valid contact number (10-11 digits)')
        
        # Determine region and shipping fee
        region = 'west' if state in STATE_REGIONS['west'] else 'east'
        shipping_fee = SHIPPING_RATES[region]
        
        # Calculate total
        total_price = subtotal + shipping_fee
        
        # Generate order ID starting with EF
        import random
        order_number = str(random.randint(1000, 9999))
        order_id = f"EF{order_number}"
        
        # Check if order ID already exists
        existing_order = g.conn.execute(
            'SELECT * FROM orders WHERE order_id = ?', 
            (order_id,)
        ).fetchone()
        
        if existing_order:
            order_number = str(random.randint(1000, 9999))
            order_id = f"EF{order_number}"
        
        try:
            # Insert order as RESERVED
            g.conn.execute('''
                INSERT INTO orders (order_id, customer_name, contact_number, total_price, shipping_fee, 
                                  address, postcode, state, region, status, payment_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', 'pending')
            ''', (order_id, customer_name, contact_number, total_price, shipping_fee, 
                  address, postcode, state, region))
            
            # Insert order items
            for item in cart_items:
                g.conn.execute('''
                    INSERT INTO order_items (order_id, product_id, product_name, 
                                           quantity, price, weight)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (order_id, item['id'], item['name'], 
                      item['quantity'], item['price'], item['weight']))
            
            g.conn.commit()
            
            # Send Telegram notification
            message = format_order_reservation(
                order_id, customer_name, contact_number, cart_items, 
                shipping_fee, total_price, address, postcode, state
            )
            send_telegram_message(message)
            
            # Clear cart
            session.pop('cart', None)
            
            # Redirect to RESERVATION SUCCESS page
            return redirect(url_for('reservation_complete', order_id=order_id))
            
        except Exception as e:
            print(f"Error processing order: {e}")
            return render_template('user_checkout.html', 
                                 cart_items=cart_items, 
                                 subtotal=subtotal,
                                 states=STATE_REGIONS['west'] + STATE_REGIONS['east'],
                                 payment_methods=PAYMENT_METHODS,  # Add this
                                 settings=settings,  # Add this
                                 error=f'Error processing order: {str(e)}')
    
    # GET request - render the form
    return render_template('user_checkout.html', 
                          cart_items=cart_items, 
                          subtotal=subtotal,
                          states=STATE_REGIONS['west'] + STATE_REGIONS['east'],
                          payment_methods=PAYMENT_METHODS,  # Add this
                          settings=settings)  # Add this

def format_order_reservation(order_id, customer_name, contact_number, cart_items, shipping_fee, total_price, address, postcode, state):
    """Format reservation details for Telegram notification"""
    message = f"📋 *NEW ORDER RESERVATION!*\n\n"
    message += f"📦 Order ID: {order_id}\n"
    message += f"👤 Customer: {customer_name}\n"
    message += f"📱 WhatsApp: +6{contact_number}\n"
    message += f"📍 Address: {postcode} {state}\n\n"
    
    message += "*📦 Items Reserved:*\n"
    sorted_items = sorted(cart_items, key=lambda x: x['quantity'], reverse=True)
    
    for i, item in enumerate(sorted_items, 1):
        item_total = item['price'] * item['quantity']
        message += f"{i}. {item['name']} - {item['quantity']} qty (RM{item_total:.2f})\n"
    
    subtotal = sum(item['price'] * item['quantity'] for item in cart_items)
    message += f"\n💰 Subtotal: RM{subtotal:.2f}\n"
    message += f"🚚 Shipping: RM{shipping_fee:.2f}\n"
    message += f"💵 Total: RM{total_price:.2f}\n\n"
    
    message += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    # Add WhatsApp link for admin to contact customer
    whatsapp_message = f"Hi {customer_name}, your order {order_id} for RM{total_price:.2f} is ready for payment. Please use this link: {request.host_url}payment/{order_id}"
    whatsapp_link = f"https://wa.me/6{contact_number}?text={whatsapp_message}"
    
    message += f"📲 *Contact Customer:* {whatsapp_link}"
    
    return message

@app.route('/reservation/complete/<order_id>')
def reservation_complete(order_id):
    """Show reservation complete page"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return redirect(url_for('user_products'))
    
    items = g.conn.execute('SELECT * FROM order_items WHERE order_id = ?', (order_id,)).fetchall()
    
    # Generate WhatsApp message for admin
    settings = get_settings()
    whatsapp_message = f"Hi, I've placed order {order_id} for RM{order['total_price']:.2f}. Please contact me for payment details."
    whatsapp_link = f"https://wa.me/6{order['contact_number']}?text={whatsapp_message}"
    
    return render_template('reservation_success.html', 
                         order=order,
                         items=items,
                         whatsapp_link=whatsapp_link,
                         settings=settings)

@app.route('/payment/<order_id>', methods=['GET', 'POST'])
def payment_page(order_id):
    """Payment page - only accessible when admin sends link"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return render_template('payment_not_found.html', order_id=order_id)
    
    # Check if order is in reserved status
    if order['status'] != 'reserved' or order['payment_status'] != 'pending':
        return render_template('payment_not_available.html', 
                             order=order,
                             message="This order is no longer available for payment.")
    
    items = g.conn.execute('SELECT * FROM order_items WHERE order_id = ?', (order_id,)).fetchall()
    settings = get_settings()
    
    if request.method == 'POST':
        payment_method = request.form.get('payment_method')
        
        if not payment_method:
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error="Please select a payment method")
        
        # Check if receipt is uploaded
        if 'receipt' not in request.files or request.files['receipt'].filename == '':
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error="Please upload payment receipt")
        
        file = request.files['receipt']
        
        # ============ FILE VALIDATION START ============
        # 1. Check allowed file extensions
        allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'pdf', 'webp', 'bmp'}
        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        
        if not file_ext or file_ext not in allowed_extensions:
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error=f"File type '{file_ext}' not supported. Please upload JPG, PNG, GIF, PDF, WebP, or BMP files only.")
        
        # 2. Check file size (limit to 5MB)
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_length > 5 * 1024 * 1024:  # 5MB
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error="File too large. Maximum size is 5MB.")
        
        # 3. Secure the filename
        import re
        import uuid
        
        # Generate a secure filename
        original_filename = file.filename
        secure_name = re.sub(r'[^\w\s.-]', '', original_filename)
        secure_name = secure_name.strip().replace(' ', '_')
        
        # Generate unique filename to prevent collisions
        unique_id = str(uuid.uuid4())[:8]
        filename = f"receipt_{order_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{unique_id}.{file_ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # 4. Try to save the file
        try:
            file.save(filepath)
            print(f"✅ Receipt saved: {filename}")
            
            # Verify file was saved (optional)
            if not os.path.exists(filepath):
                return render_template('payment_page.html',
                                     order=order,
                                     items=items,
                                     settings=settings,
                                     payment_methods=PAYMENT_METHODS,
                                     error="Failed to save receipt. Please try again.")
            
        except Exception as e:
            print(f"❌ Error saving file: {e}")
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error=f"Error saving file: {str(e)}. Please try again.")
        # ============ FILE VALIDATION END ============
        
        # Update order with payment method and receipt
        try:
            g.conn.execute('''
                UPDATE orders 
                SET payment_method = ?, 
                    payment_status = 'pending_verification',
                    payment_receipt = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (payment_method, filename, order_id))
            g.conn.commit()
            
        except Exception as e:
            print(f"❌ Database error: {e}")
            # Try to delete the uploaded file if database update fails
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
            
            return render_template('payment_page.html',
                                 order=order,
                                 items=items,
                                 settings=settings,
                                 payment_methods=PAYMENT_METHODS,
                                 error=f"Database error: {str(e)}. Please try again.")
        
        # Send Telegram notification
        try:
            message = f"💰 *PAYMENT SUBMITTED*\n\n"
            message += f"📦 Order ID: {order_id}\n"
            message += f"👤 Customer: {order['customer_name']}\n"
            message += f"📱 WhatsApp: +6{order['contact_number']}\n"
            message += f"💵 Amount: RM{order['total_price']:.2f}\n"
            message += f"💳 Method: {payment_method}\n"
            message += f"📎 Receipt: {filename}\n"
            message += f"📏 File size: {file_length / 1024:.1f} KB\n"
            message += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            send_telegram_message(message)
        except Exception as e:
            print(f"⚠️ Telegram notification failed: {e}")
            # Continue even if Telegram fails
        
        # Show success page
        return render_template('payment_submitted.html',
                             order_id=order_id,
                             customer_name=order['customer_name'],
                             current_time=datetime.now().strftime('%d %b %Y, %I:%M %p'))
    
    # GET request - render the form
    return render_template('payment_page.html', 
                         order=order, 
                         items=items, 
                         settings=settings,
                         payment_methods=PAYMENT_METHODS)

@app.route('/order/success/<order_id>')
def order_success(order_id):
    """Order success page (for old system, kept for compatibility)"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return redirect(url_for('user_products'))
    
    items = g.conn.execute('SELECT * FROM order_items WHERE order_id = ?', (order_id,)).fetchall()
    
    return render_template('order_success.html', 
                         order=order,
                         items=items)

# ============== ADMIN ROUTES ==============

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = g.conn.execute(
            'SELECT * FROM admin_users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        
        if admin:
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_dashboard'))
        
        return render_template('admin_login.html', error='Invalid credentials')
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    # Get counts for dashboard
    product_count = g.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    order_count = g.conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
    
    # Get pending payments (orders with payment_status = 'pending_verification')
    pending_payments = g.conn.execute(
        'SELECT COUNT(*) FROM orders WHERE payment_status = "pending_verification"'
    ).fetchone()[0]
    
    # Get recent orders including payment status
    recent_orders = g.conn.execute('''
        SELECT * FROM orders 
        ORDER BY created_at DESC 
        LIMIT 10
    ''').fetchall()
    
    # Get orders awaiting verification
    orders_to_verify = g.conn.execute('''
        SELECT * FROM orders 
        WHERE payment_status = 'pending_verification'
        ORDER BY created_at DESC
    ''').fetchall()
    
    # Get total revenue
    total_revenue_result = g.conn.execute(
        'SELECT SUM(total_price) FROM orders WHERE payment_verified = 1'
    ).fetchone()
    total_revenue = total_revenue_result[0] if total_revenue_result[0] else 0
    
    return render_template('admin_dashboard.html', 
                          product_count=product_count,
                          order_count=order_count,
                          pending_payments=pending_payments,
                          recent_orders=recent_orders,
                          orders_to_verify=orders_to_verify,
                          total_revenue=total_revenue)

@app.route('/admin/products')
@admin_required
def admin_products():
    """Manage products"""
    products = g.conn.execute('SELECT * FROM products ORDER BY created_at DESC').fetchall()
    return render_template('admin_products.html', products=products)

@app.route('/admin/products/add', methods=['GET', 'POST'])
@admin_required
def add_product():
    """Add new product"""
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price'))
        weight = float(request.form.get('weight'))
        
        g.conn.execute(
            'INSERT INTO products (name, price, weight) VALUES (?, ?, ?)',
            (name, price, weight)
        )
        g.conn.commit()
        
        return redirect(url_for('admin_products'))
    
    return render_template('add_product.html')

@app.route('/admin/products/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_product(id):
    """Edit product"""
    product = g.conn.execute('SELECT * FROM products WHERE id = ?', (id,)).fetchone()
    
    if not product:
        return redirect(url_for('admin_products'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price'))
        weight = float(request.form.get('weight'))
        
        g.conn.execute(
            'UPDATE products SET name = ?, price = ?, weight = ? WHERE id = ?',
            (name, price, weight, id)
        )
        g.conn.commit()
        
        return redirect(url_for('admin_products'))
    
    return render_template('edit_product.html', product=product)

@app.route('/admin/products/delete/<int:id>')
@admin_required
def delete_product(id):
    """Delete product"""
    g.conn.execute('DELETE FROM products WHERE id = ?', (id,))
    g.conn.commit()
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    """View all orders"""
    orders = g.conn.execute('''
        SELECT o.*, 
               (SELECT COUNT(*) FROM order_items WHERE order_id = o.order_id) as item_count
        FROM orders o 
        ORDER BY o.created_at DESC
    ''').fetchall()
    return render_template('admin_orders.html', orders=orders)

@app.route('/admin/orders/<order_id>')
@admin_required
def order_details(order_id):
    """View order details"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return redirect(url_for('admin_orders'))
    
    items = g.conn.execute('''
        SELECT oi.*, p.price as unit_price 
        FROM order_items oi 
        LEFT JOIN products p ON oi.product_id = p.id 
        WHERE oi.order_id = ?
    ''', (order_id,)).fetchall()
    
    return render_template('order_details.html', order=order, items=items)

@app.route('/admin/orders/send_payment_link/<order_id>')
@admin_required
def send_payment_link(order_id):
    """Generate payment link for admin to send to customer"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'})
    
    payment_link = f"{request.host_url}payment/{order_id}"
    
    # Telegram notification
    message = f"🔗 *PAYMENT LINK GENERATED*\n\n"
    message += f"📦 Order ID: {order_id}\n"
    message += f"👤 Customer: {order['customer_name']}\n"
    message += f"📱 WhatsApp: +6{order['contact_number']}\n"
    message += f"💵 Amount: RM{order['total_price']:.2f}\n\n"
    message += f"🔗 Payment Link: {payment_link}\n\n"
    message += f"📲 WhatsApp Customer: https://wa.me/6{order['contact_number']}"
    
    send_telegram_message(message)
    
    return jsonify({
        'success': True, 
        'payment_link': payment_link,
        'whatsapp_link': f"https://wa.me/6{order['contact_number']}"
    })

@app.route('/admin/orders/verify_payment/<order_id>', methods=['POST'])
@admin_required
def verify_payment(order_id):
    """Verify payment and update order status"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'})
    
    action = request.form.get('action')
    
    if action == 'verify':
        try:
            g.conn.execute('''
                UPDATE orders 
                SET payment_verified = 1, 
                    payment_status = 'verified',
                    status = 'confirmed',
                    payment_verified_at = CURRENT_TIMESTAMP,
                    payment_verified_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (session.get('admin_username', 'admin'), order_id))
            g.conn.commit()
            
            # Generate WhatsApp message for admin to send
            whatsapp_message = f"Hi {order['customer_name']}, your payment for Order {order_id} has been verified. We will proceed with shipping within 3 working days. Thank you!"
            
            # Create WhatsApp link
            whatsapp_link = f"https://wa.me/6{order['contact_number']}?text={urllib.parse.quote(whatsapp_message)}"
            
            # Send Telegram notification
            telegram_message = f"✅ *PAYMENT VERIFIED*\n\n"
            telegram_message += f"📦 Order ID: {order_id}\n"
            telegram_message += f"👤 Customer: {order['customer_name']}\n"
            telegram_message += f"💵 Amount: RM{order['total_price']:.2f}\n"
            telegram_message += f"👨‍💼 Verified by: {session.get('admin_username', 'admin')}\n\n"
            telegram_message += f"📱 WhatsApp Message to send:\n"
            telegram_message += f"{whatsapp_message}\n\n"
            telegram_message += f"🔗 WhatsApp Link: {whatsapp_link}\n\n"
            telegram_message += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            send_telegram_message(telegram_message)
            
            return jsonify({
                'success': True, 
                'message': 'Payment verified successfully',
                'whatsapp_message': whatsapp_message,
                'whatsapp_link': whatsapp_link,
                'customer_name': order['customer_name'],
                'order_id': order_id
            })
        except Exception as e:
            print(f"Error verifying payment: {e}")
            return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    
    elif action == 'reject':
        reason = request.form.get('reason', '')
        # Clean the reason string
        reason = reason.replace('#', '').strip()
        
        try:
            g.conn.execute('''
                UPDATE orders 
                SET payment_verified = 0, 
                    payment_status = 'rejected',
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            ''', (order_id,))
            g.conn.commit()
            
            # Generate WhatsApp message for rejection
            whatsapp_message = f"Hi {order['customer_name']}, your payment for Order {order_id} was rejected. Reason: {reason}. Please contact us for assistance."
            whatsapp_link = f"https://wa.me/6{order['contact_number']}?text={urllib.parse.quote(whatsapp_message)}"
            
            telegram_message = f"❌ *PAYMENT REJECTED*\n\n"
            telegram_message += f"📦 Order ID: {order_id}\n"
            telegram_message += f"👤 Customer: {order['customer_name']}\n"
            telegram_message += f"💵 Amount: RM{order['total_price']:.2f}\n"
            telegram_message += f"📝 Reason: {reason}\n\n"
            telegram_message += f"📱 WhatsApp Message to send:\n"
            telegram_message += f"{whatsapp_message}\n\n"
            telegram_message += f"🔗 WhatsApp Link: {whatsapp_link}\n\n"
            telegram_message += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            send_telegram_message(telegram_message)
            
            return jsonify({
                'success': True, 
                'message': 'Payment rejected',
                'whatsapp_message': whatsapp_message,
                'whatsapp_link': whatsapp_link,
                'customer_name': order['customer_name'],
                'order_id': order_id
            })
        except Exception as e:
            print(f"Error rejecting payment: {e}")
            return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    
    return jsonify({'success': False, 'message': 'Invalid action'})

@app.route('/admin/verify_payments')
@admin_required
def admin_verify_payments():
    """Payment verification page"""
    # Get orders awaiting verification
    orders_result = g.conn.execute('''
        SELECT * FROM orders 
        WHERE payment_status = 'pending_verification'
        ORDER BY created_at DESC
    ''').fetchall()
    
    # Get order items for each order
    orders_with_items = []
    for order in orders_result:
        # Convert Row to dict properly
        order_dict = dict(order)
        
        # Get items for this order
        items_result = g.conn.execute(
            'SELECT * FROM order_items WHERE order_id = ?', 
            (order['order_id'],)
        ).fetchall()
        
        # Convert items to list of dicts
        items_list = [dict(item) for item in items_result]
        
        # Use a different key name to avoid conflict with dict.items() method
        order_dict['order_items'] = items_list
        orders_with_items.append(order_dict)
    
    # Calculate pending payments count
    pending_payments = len(orders_with_items)
    
    return render_template('admin_verify_payments.html', 
                         orders=orders_with_items,
                         pending_payments=pending_payments)

@app.context_processor
def inject_pending_payments():
    """Inject pending payments count into all templates"""
    if hasattr(g, 'conn'):
        pending_count = g.conn.execute(
            'SELECT COUNT(*) FROM orders WHERE payment_status = "pending_verification"'
        ).fetchone()[0]
        return dict(pending_payments=pending_count)
    return dict(pending_payments=0)

@app.route('/admin/orders/add_tracking/<order_id>', methods=['POST'])
@admin_required
def add_tracking_number(order_id):
    """Add tracking number to order"""
    order = g.conn.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,)).fetchone()
    
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'})
    
    tracking_number = request.form.get('tracking_number')
    
    if not tracking_number:
        return jsonify({'success': False, 'message': 'Tracking number required'})
    
    g.conn.execute('''
        UPDATE orders 
        SET tracking_number = ?, 
            status = 'shipped',
            updated_at = CURRENT_TIMESTAMP
        WHERE order_id = ?
    ''', (tracking_number, order_id))
    g.conn.commit()
    
    # Send Telegram notification
    message = f"🚚 *ORDER SHIPPED*\n\n"
    message += f"📦 Order ID: {order_id}\n"
    message += f"👤 Customer: {order['customer_name']}\n"
    message += f"📱 WhatsApp: +6{order['contact_number']}\n"
    message += f"📮 Tracking: {tracking_number}\n"
    message += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    send_telegram_message(message)
    
    return jsonify({'success': True, 'message': 'Tracking number added successfully'})

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Admin settings page"""
    settings = get_settings()
    
    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('setting_'):
                setting_key = key.replace('setting_', '')
                g.conn.execute('''
                    UPDATE admin_settings 
                    SET setting_value = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE setting_key = ?
                ''', (value, setting_key))
        
        g.conn.commit()
        return redirect(url_for('admin_settings'))
    
    return render_template('admin_settings.html', settings=settings)

@app.route('/admin/change_password', methods=['GET', 'POST'])
@admin_required
def change_password():
    """Change admin password"""
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            return render_template('change_password.html', error='New passwords do not match')
        
        # Verify current password
        admin = g.conn.execute(
            'SELECT * FROM admin_users WHERE username = ? AND password = ?',
            (session.get('admin_username'), current_password)
        ).fetchone()
        
        if not admin:
            return render_template('change_password.html', error='Current password is incorrect')
        
        # Update password
        g.conn.execute('''
            UPDATE admin_users 
            SET password = ? 
            WHERE username = ?
        ''', (new_password, session.get('admin_username')))
        g.conn.commit()
        
        return render_template('change_password.html', success='Password changed successfully')
    
    return render_template('change_password.html')

def update_database_schema():
    """Update database schema with new columns"""
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    
    try:
        # Check and add missing columns to orders table
        cursor.execute("PRAGMA table_info(orders)")
        columns = [column[1] for column in cursor.fetchall()]
        
        new_columns = [
            ('payment_method', 'TEXT'),
            ('payment_status', 'TEXT DEFAULT "pending"'),
            ('payment_receipt', 'TEXT'),
            ('payment_verified', 'BOOLEAN DEFAULT 0'),
            ('payment_verified_at', 'TIMESTAMP'),
            ('payment_verified_by', 'TEXT'),
            ('tracking_number', 'TEXT'),
            ('updated_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        ]
        
        for column_name, column_type in new_columns:
            if column_name not in columns:
                try:
                    cursor.execute(f'ALTER TABLE orders ADD COLUMN {column_name} {column_type}')
                    print(f"✅ Added {column_name} column to orders table")
                except sqlite3.OperationalError as e:
                    print(f"⚠️ Could not add {column_name}: {e}")
        
        # Create admin_settings table if not exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT,
                setting_type TEXT DEFAULT 'text',
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
    except Exception as e:
        print(f"❌ Error updating database schema: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
    update_database_schema()
    app.run(debug=True, port=5000)