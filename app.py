import os
import secrets
import requests
import json
import hmac
import hashlib
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_mail import Mail, Message
from models import db, Admin, Product, Account, Order, Category, User
from dotenv import load_dotenv
import requests
from functools import wraps
from sqlalchemy import func, or_, case
import logging

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///melody_store.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'pool_recycle': 300,
    'pool_pre_ping': True
}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db.init_app(app)

# Paystack keys
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_WEBHOOK_SECRET = os.getenv('PAYSTACK_WEBHOOK_SECRET', '')

# GROQ
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",      # Best overall
    "llama-3.1-8b-instant",          # Fast fallback
    "mixtral-8x7b-32768",            # Strong alternative
    "gemma2-9b-it",                  # Lightweight fallback
    "llama-3.2-3b-preview"           # Last resort
]

# Email configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 465))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'False') == 'True'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])

# Initialize mail
mail = Mail(app)

# ========== HELPER FUNCTIONS ==========

def admin_required(f):
    """Decorator for admin-only routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please login first', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def verify_paystack_webhook(signature, payload):
    """Verify Paystack webhook signature"""
    if not PAYSTACK_WEBHOOK_SECRET:
        return True
    hash = hmac.new(
        PAYSTACK_WEBHOOK_SECRET.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(hash, signature)

def process_successful_payment(reference, metadata, amount, customer_email):
    """
    Core business logic for processing successful payments
    Uses row-level locking to prevent race conditions
    """
    try:
        product_id = metadata.get('product_id')
        customer_name = metadata.get('customer_name')

        if not all([product_id, customer_name, customer_email]):
            return None, "Missing required customer information"

        # Use with_for_update to lock the row and prevent double-selling
        account = Account.query.filter_by(
            product_id=product_id,
            sold=False
        ).with_for_update().first()

        if not account:
            return None, "Sorry, this product is no longer available"

        product = Product.query.get(product_id)
        if not product:
            return None, "Product not found"

        # Create order
        order = Order(
            customer_name=customer_name,
            customer_email=customer_email,
            product_id=product_id,
            product_name=product.name,
            product_category=product.category_ref.name if product.category_ref else None,
            amount=amount,
            account_id=account.id,
            account_email=account.account_email,
            account_password=account.account_password,
            account_2fa=account.account_2fa,
            account_notes=account.additional_info,
            payment_ref=reference
        )
        
        # Generate order number - CRITICAL FIX!
        order.generate_order_number()

        # Mark account as sold
        account.sold = True
        account.sold_to = customer_name
        account.sold_email = customer_email
        account.sold_at = datetime.utcnow()
        account.order_number = order.order_number

        db.session.add(order)
        db.session.commit()

        logger.info(f"Payment processed successfully: {order.order_number}")
        return order, None

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error processing payment: {str(e)}")
        return None, str(e)

# ========== EMAIL FUNCTIONS ==========

def send_order_confirmation(order):
    """Send order confirmation email with account details"""
    try:
        # Create HTML email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Order Confirmation - Melody Store</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f9fafb;
                    margin: 0;
                    padding: 0;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #2563eb, #1d4ed8);
                    color: white;
                    padding: 30px 20px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 28px;
                }}
                .header p {{
                    margin: 10px 0 0;
                    opacity: 0.9;
                }}
                .content {{
                    padding: 30px;
                }}
                .order-details {{
                    background: #f3f4f6;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 20px 0;
                }}
                .credentials {{
                    background: #fff;
                    border: 2px solid #e5e7eb;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 20px 0;
                }}
                .credential {{
                    background: #f9fafb;
                    padding: 12px;
                    margin: 10px 0;
                    border-radius: 6px;
                    border-left: 4px solid #2563eb;
                }}
                .credential strong {{
                    color: #2563eb;
                    display: block;
                    margin-bottom: 5px;
                }}
                .warning {{
                    background: #fef3c7;
                    border-left: 4px solid #f59e0b;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                }}
                .footer {{
                    background: #f3f4f6;
                    padding: 20px;
                    text-align: center;
                    font-size: 12px;
                    color: #6b7280;
                }}
                .button {{
                    display: inline-block;
                    padding: 12px 24px;
                    background: #2563eb;
                    color: white;
                    text-decoration: none;
                    border-radius: 6px;
                    margin-top: 20px;
                }}
                .button:hover {{
                    background: #1d4ed8;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎉 Order Confirmed!</h1>
                    <p>Order #{order.order_number}</p>
                </div>
                <div class="content">
                    <h2>Hello {order.customer_name},</h2>
                    <p>Thank you for your purchase! Your order has been confirmed and your account details are ready.</p>
                    
                    <div class="order-details">
                        <h3>📦 Order Summary</h3>
                        <p><strong>Product:</strong> {order.product_name}</p>
                        <p><strong>Amount Paid:</strong> ₦{float(order.amount):,.2f}</p>
                        <p><strong>Payment Reference:</strong> {order.payment_ref}</p>
                    </div>
                    
                    <div class="credentials">
                        <h3>🔐 Your Account Credentials</h3>
                        <div class="credential">
                            <strong>📧 Email/Username:</strong>
                            {order.account_email}
                        </div>
                        <div class="credential">
                            <strong>🔑 Password:</strong>
                            {order.account_password}
                        </div>
                        {f'<div class="credential"><strong>🔢 2FA/Backup Code:</strong><br>{order.account_2fa}</div>' if order.account_2fa else ''}
                        {f'<div class="credential"><strong>📝 Additional Info:</strong><br>{order.account_notes}</div>' if order.account_notes else ''}
                    </div>
                    
                    <div class="warning">
                        <strong>⚠️ Important Security Notice:</strong><br>
                        • Please change your password immediately after logging in<br>
                        • These credentials will only be sent once via email<br>
                        • Do not share your account details with anyone<br>
                        • Keep this email for your records
                    </div>
                    
                    <div style="text-align: center;">
                        <a href="https://melodystore.onrender.com/my-orders" class="button">
                            View My Orders
                        </a>
                    </div>
                </div>
                <div class="footer">
                    <p>Need help? Contact our support team at support@melodystore.com</p>
                    <p>&copy; 2024 Melody Store. All rights reserved.</p>
                    <p style="margin-top: 10px;">
                        <small>This is an automated message, please do not reply directly to this email.</small>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create plain text version
        text_content = f"""
MELODY STORE - ORDER CONFIRMATION
==================================
Order #{order.order_number}

Hello {order.customer_name},

Thank you for your purchase! Your account details are below.

ORDER SUMMARY:
- Product: {order.product_name}
- Amount: ₦{float(order.amount):,.2f}
- Payment Ref: {order.payment_ref}

ACCOUNT CREDENTIALS:
- Email: {order.account_email}
- Password: {order.account_password}
{f'- 2FA Code: {order.account_2fa}' if order.account_2fa else ''}
{f'- Additional Info: {order.account_notes}' if order.account_notes else ''}

IMPORTANT:
- Change your password immediately after login
- Keep these credentials secure
- Contact support if you have any issues

View your orders: https://melodystore.onrender.com/my-orders

Melody Store Team
        """
        
        msg = Message(
            subject=f"Order Confirmation - #{order.order_number}",
            recipients=[order.customer_email]
        )
        msg.body = text_content
        msg.html = html_content
        
        mail.send(msg)
        logger.info(f"Order confirmation email sent to {order.customer_email} for order {order.order_number}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email for order {order.order_number}: {str(e)}")
        return False

# ========== CONTEXT PROCESSORS ==========

@app.context_processor
def utility_processor():
    """Make helper functions available in templates"""
    def get_category_image(category):
        if isinstance(category, Category):
            return url_for('static', filename=f'logos/{category.slug}-logo.png')
        return url_for('static', filename='logos/default-logo.png')

    def get_product_image(product):
        if product.image and product.image != 'default.jpg':
            return url_for('static', filename=f'uploads/{product.image}')
        if product.category_ref:
            return url_for('static', filename=f'logos/{product.category_ref.slug}-logo.png')
        return url_for('static', filename='logos/default-logo.png')

    def format_currency(amount):
        return f"₦{amount:,.2f}"

    return dict(
        get_category_image=get_category_image,
        get_product_image=get_product_image,
        format_currency=format_currency,
        now=datetime.utcnow
    )

# ========== INITIALIZATION ==========

with app.app_context():
    db.create_all()

    # Create default admin
    admin_username = os.getenv('ADMIN_USERNAME', 'admin')
    if not Admin.query.filter_by(username=admin_username).first():
        admin = Admin(
            username=admin_username,
            email=os.getenv('ADMIN_EMAIL', 'admin@melodystore.com')
        )
        admin.set_password(os.getenv('ADMIN_PASSWORD', 'admin123'))
        db.session.add(admin)

    # Create default categories
    categories = [
        {'name': 'Facebook', 'slug': 'facebook', 'icon': '📘', 
         'description': 'Aged Facebook accounts with marketplace', 'display_order': 1},
        {'name': 'Instagram', 'slug': 'instagram', 'icon': '📷', 
         'description': 'Instagram accounts with followers', 'display_order': 2},
        {'name': 'TikTok', 'slug': 'tiktok', 'icon': '🎵', 
         'description': 'TikTok accounts with posts', 'display_order': 3},
        {'name': 'Twitter', 'slug': 'twitter', 'icon': '🐦', 
         'description': 'Twitter/X accounts aged', 'display_order': 4},
        {'name': 'VPN', 'slug': 'vpn', 'icon': '🔒', 
         'description': 'Premium VPN subscriptions', 'display_order': 5},
        {'name': 'Texting Apps', 'slug': 'texting', 'icon': '💬', 
         'description': 'Texting apps for verification', 'display_order': 6},
        {'name': 'Update/Format', 'slug': 'format', 'icon': '⚙️', 
         'description': 'PC format tools', 'display_order': 7},
        {'name': 'Dating Logs', 'slug': 'dating', 'icon': '💕', 
         'description': 'Dating site accounts', 'display_order': 8},
        {'name': 'Other Logs', 'slug': 'other', 'icon': '📦', 
         'description': 'Other digital accounts', 'display_order': 9}
    ]

    for cat_data in categories:
        if not Category.query.filter_by(slug=cat_data['slug']).first():
            category = Category(**cat_data)
            db.session.add(category)

    db.session.commit()

# ========== ERROR HANDLERS ==========

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    db.session.rollback()
    return render_template('500.html'), 500

# ========== CUSTOMER ROUTES ==========

@app.route('/')
def index():
    """Homepage with all categories"""
    categories = Category.query.order_by(Category.display_order).all()
    
    # Get featured products with stock counts
    featured_products = Product.query.filter_by(is_active=True)\
        .order_by(Product.created_at.desc())\
        .limit(8)\
        .all()
    
    # Efficient stock counting
    product_ids = [p.id for p in featured_products]
    stock_counts = dict(
        db.session.query(
            Account.product_id, 
            func.count(Account.id)
        ).filter(
            Account.product_id.in_(product_ids),
            Account.sold == False
        ).group_by(Account.product_id).all()
    )
    
    for product in featured_products:
        product.available = stock_counts.get(product.id, 0)

    return render_template('index.html',
                         categories=categories,
                         featured_products=featured_products)

# ========== STATIC PAGES ==========

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/refund')
def refund():
    return render_template('refund.html')

@app.route('/sitemap')
def sitemap():
    return render_template('sitemap.html')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    """Handle newsletter subscription"""
    email = request.form.get('email')
    if email:
        flash('Thank you for subscribing!', 'success')
    else:
        flash('Please enter a valid email', 'error')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        
        if password != confirm:
            flash('Passwords do not match', 'error')
            return redirect(url_for('register'))
        
        from models import User
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return redirect(url_for('register'))
        
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        from models import User
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_email', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    """User dashboard - shows orders and AI assistant"""
    if not session.get('user_id'):
        flash('Please login to access your dashboard', 'error')
        return redirect(url_for('login'))
    
    from models import User
    user = User.query.get(session['user_id'])
    orders = Order.query.filter_by(customer_email=user.email).order_by(Order.created_at.desc()).all()
    
    return render_template('dashboard.html', user=user, orders=orders)

@app.route('/category/<slug>')
def category_view(slug):
    """View products in a category"""
    category = Category.query.filter_by(slug=slug).first_or_404()

    products = Product.query.filter_by(
        category_id=category.id,
        is_active=True
    ).all()

    # Bulk stock check
    product_ids = [p.id for p in products]
    stock_counts = dict(
        db.session.query(
            Account.product_id,
            func.count(Account.id)
        ).filter(
            Account.product_id.in_(product_ids),
            Account.sold == False
        ).group_by(Account.product_id).all()
    )

    for product in products:
        product.available = stock_counts.get(product.id, 0)

    # Add pagination variables to fix the template error
    total_pages = 1
    current_page = 1

    return render_template('category.html', 
                         category=category, 
                         products=products,
                         total_pages=total_pages,
                         current_page=current_page,
                         category_slug=slug)

# ========== CART ROUTES ==========

@app.route('/cart')
def cart():
    """View shopping cart"""
    return render_template('cart.html')

@app.route('/api/cart/add', methods=['POST'])
def add_to_cart():
    """API endpoint to add item to cart"""
    try:
        data = request.json
        product_id = data.get('product_id')
        
        # Get product details
        product = Product.query.get(product_id)
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404
        
        # Check stock
        available = Account.query.filter_by(product_id=product_id, sold=False).count()
        
        return jsonify({
            'success': True,
            'message': 'Added to cart',
            'product': {
                'id': product.id,
                'name': product.name,
                'price': float(product.price),
                'available': available
            }
        })
    except Exception as e:
        logger.error(f"Cart error: {str(e)}")
        return jsonify({'success': False, 'message': 'Error adding to cart'}), 500

@app.route('/product/<int:product_id>')
def product_view(product_id):
    """View single product details"""
    product = Product.query.get_or_404(product_id)
    available_count = Account.query.filter_by(product_id=product_id, sold=False).count()

    # FIXED: Use category_id for similar products
    similar = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id,
        Product.is_active == True
    ).limit(4).all()

    return render_template('product.html',
                         product=product,
                         available_count=available_count,
                         similar=similar)

@app.route('/checkout/<int:product_id>')
def checkout(product_id):
    """Checkout page"""
    product = Product.query.get_or_404(product_id)

    # Quick availability check
    available = Account.query.filter_by(product_id=product_id, sold=False).first()
    available_count = Account.query.filter_by(product_id=product_id, sold=False).count()

    if not available:
        flash('Sorry, this product is currently out of stock!', 'error')
        return redirect(url_for('product_view', product_id=product_id))

    return render_template('checkout.html',
                         product=product,
                         available_count=available_count,
                         paystack_key=PAYSTACK_PUBLIC_KEY)

@app.route('/initialize-payment', methods=['POST'])
def initialize_payment():
    """Initialize Paystack transaction"""
    try:
        data = request.json
        product_id = data.get('product_id')
        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email')

        if not all([product_id, customer_name, customer_email]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        # Validate email format
        if '@' not in customer_email or '.' not in customer_email:
            return jsonify({'success': False, 'message': 'Invalid email format'}), 400

        product = Product.query.get(product_id)
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'}), 404

        # Double-check availability before initializing
        account = Account.query.filter_by(product_id=product_id, sold=False).first()
        if not account:
            return jsonify({'success': False, 'message': 'No account available'}), 400

        # Generate unique reference
        reference = f"MEL-{datetime.utcnow().strftime('%y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"

        # Initialize with Paystack
        url = 'https://api.paystack.co/transaction/initialize'
        headers = {
            'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json'
        }

        payload = {
            'email': customer_email,
            'amount': int(product.price * 100),  # Convert to kobo
            'reference': reference,
            'metadata': {
                'product_id': product_id,
                'product_name': product.name,
                'customer_name': customer_name
            },
            'callback_url': url_for('payment_callback', _external=True)
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()

        if result['status']:
            # Store minimal info in session
            session['pending_reference'] = reference
            return jsonify({
                'success': True,
                'authorization_url': result['data']['authorization_url'],
                'reference': reference
            })
        else:
            logger.error(f"Paystack init failed: {result.get('message')}")
            return jsonify({'success': False, 'message': result.get('message', 'Payment initialization failed')}), 400

    except requests.exceptions.RequestException as e:
        logger.error(f"Paystack connection error: {str(e)}")
        return jsonify({'success': False, 'message': 'Payment service unavailable'}), 503
    except Exception as e:
        logger.error(f"Payment initialization error: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred'}), 500


@app.route('/payment-callback')
def payment_callback():
    """Handle Paystack callback with email delivery"""
    reference = request.args.get('reference')

    if not reference:
        flash('Payment verification failed', 'error')
        return redirect(url_for('index'))

    # Verify with Paystack
    url = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()

        if result['status'] and result['data']['status'] == 'success':
            # Check if already processed
            existing_order = Order.query.filter_by(payment_ref=reference).first()
            if existing_order:
                session['last_order'] = {
                    'order_number': existing_order.order_number,
                    'account': {
                        'email': existing_order.account_email,
                        'password': existing_order.account_password,
                        'two_fa': existing_order.account_2fa,
                        'notes': existing_order.account_notes
                    }
                }
                return redirect(url_for('payment_success'))

            # Process using shared logic
            metadata = result['data'].get('metadata', {})
            customer_email = result['data']['customer']['email']
            amount = result['data']['amount'] // 100

            order, error = process_successful_payment(
                reference,
                metadata,
                amount,
                customer_email
            )

            if error:
                flash(error, 'error')
                return redirect(url_for('index'))

            if order:
                # Send email confirmation after successful payment
                send_order_confirmation(order)
                
                session['last_order'] = {
                    'order_number': order.order_number,
                    'account': {
                        'email': order.account_email,
                        'password': order.account_password,
                        'two_fa': order.account_2fa,
                        'notes': order.account_notes
                    }
                }
                return redirect(url_for('payment_success'))

        flash('Payment verification failed', 'error')
        return redirect(url_for('index'))

    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        flash('An error occurred during verification', 'error')
        return redirect(url_for('index'))

@app.route('/payment-success')
def payment_success():
    """Show successful payment with account details"""
    last_order = session.get('last_order')
    if not last_order:
        return redirect(url_for('index'))

    # Get the actual order from database using the order number
    order_number = last_order.get('order_number')
    order = None
    
    if order_number:
        order = Order.query.filter_by(order_number=order_number).first()
    
    # Clear from session after displaying
    session.pop('last_order', None)
    session.pop('pending_reference', None)
    
    # If we found the order in DB, use it, otherwise fallback to session data
    if order:
        return render_template('success.html', order=order, now=datetime.utcnow())
    else:
        return render_template('success.html', order=last_order, now=datetime.utcnow())

@app.route('/my-orders', methods=['GET', 'POST'])
def my_orders():
    """View orders by email with verification"""
    if request.method == 'POST':
        email = request.form.get('email')
        if email:
            # Store email in session
            session['order_email'] = email
            
            # Send verification email with magic link
            token = secrets.token_urlsafe(32)
            session['verify_token'] = token
            
            # Create verification link
            verify_url = url_for('verify_orders', token=token, _external=True)
            
            try:
                # Send email with link
                msg = Message(
                    subject="View Your Orders - Melody Store",
                    recipients=[email],
                    body=f"""
Hello,

Click the link below to view your orders:

{verify_url}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.

Melody Store Team
                    """,
                    html=f"""
                    <h2>View Your Orders</h2>
                    <p>Click the button below to view your orders:</p>
                    <a href="{verify_url}" style="display: inline-block; padding: 10px 20px; background: #2563eb; color: white; text-decoration: none; border-radius: 5px;">View Orders</a>
                    <p>This link will expire in 1 hour.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                    """
                )
                mail.send(msg)
                flash('📧 Check your email for a verification link to view your orders!', 'success')
            except Exception as e:
                logger.error(f"Failed to send verification email: {str(e)}")
                flash('⚠️ Could not send email. Please try again.', 'error')
            
            return redirect(url_for('my_orders'))
    
    return render_template('my-orders.html')


@app.route('/verify-orders/<token>')
def verify_orders(token):
    """Verify email and show orders"""
    if session.get('verify_token') != token:
        flash('Invalid or expired verification link', 'error')
        return redirect(url_for('my_orders'))
    
    email = session.get('order_email')
    if not email:
        flash('Session expired. Please try again.', 'error')
        return redirect(url_for('my_orders'))
    
    orders = Order.query.filter_by(customer_email=email)\
        .order_by(Order.created_at.desc())\
        .all()
    
    total_pages = 1
    current_page = 1
    
    # Clear verification data
    session.pop('verify_token', None)
    session.pop('order_email', None)
    
    return render_template('orders-list.html', 
                         orders=orders, 
                         email=email,
                         total_pages=total_pages,
                         current_page=current_page)

# ========== PAYSTACK WEBHOOK ==========

@app.route('/paystack-webhook', methods=['POST'])
def paystack_webhook():
    """Handle Paystack webhook - with email delivery"""
    # Verify webhook signature
    signature = request.headers.get('x-paystack-signature')
    payload = request.get_data()

    if not verify_paystack_webhook(signature, payload):
        logger.warning("Invalid webhook signature")
        return jsonify({'error': 'Invalid signature'}), 400

    try:
        event = request.json

        if event['event'] == 'charge.success':
            data = event['data']
            reference = data['reference']

            # Prevent duplicate processing
            existing_order = Order.query.filter_by(payment_ref=reference).first()
            if existing_order:
                logger.info(f"Webhook: Order {reference} already processed")
                return jsonify({'status': 'already_processed'}), 200

            # Process the payment
            order, error = process_successful_payment(
                reference,
                data.get('metadata', {}),
                data['amount'] // 100,
                data['customer']['email']
            )

            if error:
                logger.error(f"Webhook processing error: {error}")
                return jsonify({'error': error}), 500

            # Send email confirmation after successful order creation
            if order:
                email_sent = send_order_confirmation(order)
                if email_sent:
                    logger.info(f"Webhook: Order confirmation email sent for {order.order_number}")
                else:
                    logger.warning(f"Webhook: Failed to send email for {order.order_number}")

        return jsonify({'status': 'success'}), 200

    except KeyError as e:
        logger.error(f"Webhook missing key: {str(e)}")
        return jsonify({'error': 'Invalid webhook data'}), 400
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# ========== ADMIN ROUTES ==========

@app.route('/admin')
def admin_index():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('admin_login'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        admin = Admin.query.filter_by(username=username).first()

        if admin and admin.check_password(password):
            session['admin_logged_in'] = True
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard with stats"""
    # Get stats efficiently with single queries
    total_orders = Order.query.count()
    total_revenue = db.session.query(func.sum(Order.amount)).scalar() or 0
    total_products = Product.query.count()

    # Account stats - fixed case syntax
    account_stats = db.session.query(
        func.count(Account.id).label('total'),
        func.sum(case((Account.sold == True, 1), else_=0)).label('sold')
    ).first()

    total_accounts = account_stats.total or 0
    sold_accounts = account_stats.sold or 0
    unsold_accounts = total_accounts - sold_accounts

    # Recent orders
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()

    # Low stock products
    low_stock = []
    products = Product.query.filter_by(is_active=True).all()
    for product in products:
        available = Account.query.filter_by(product_id=product.id, sold=False).count()
        if available < 5:
            low_stock.append({
                'product': product,
                'available': available
            })

    # Sales by category
    category_sales = db.session.query(
        Category.name,
        func.count(Order.id).label('order_count'),
        func.sum(Order.amount).label('revenue')
    ).join(Product, Product.id == Order.product_id)\
     .join(Category, Category.id == Product.category_id)\
     .group_by(Category.id, Category.name)\
     .all()

    stats = {
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'total_products': total_products,
        'total_accounts': total_accounts,
        'sold_accounts': sold_accounts,
        'unsold_accounts': unsold_accounts
    }

    return render_template('admin/dashboard.html',
                         stats=stats,
                         recent_orders=recent_orders,
                         low_stock=low_stock,
                         category_sales=category_sales)

# ========== ADMIN PRODUCT MANAGEMENT ==========

@app.route('/admin/products')
@admin_required
def admin_products():
    """Manage products"""
    products = Product.query.all()
    
    # Bulk stock check
    product_ids = [p.id for p in products]
    stock_counts = dict(
        db.session.query(
            Account.product_id, 
            func.count(Account.id)
        ).filter(
            Account.product_id.in_(product_ids),
            Account.sold == False
        ).group_by(Account.product_id).all()
    )
    
    for product in products:
        product.available = stock_counts.get(product.id, 0)
        
    return render_template('admin/products.html', products=products)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@admin_required
def admin_product_add():
    """Add new product"""
    if request.method == 'POST':
        try:
            # FIXED: Use category_id
            product = Product(
                name=request.form.get('name'),
                category_id=int(request.form.get('category_id')),
                price=int(request.form.get('price')),
                description=request.form.get('description'),
                rules=request.form.get('rules'),
                country=request.form.get('country'),
                features=request.form.get('features'),
                is_active=True
            )
            db.session.add(product)
            db.session.commit()
            flash('Product added successfully!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding product: {str(e)}', 'error')

    categories = Category.query.order_by(Category.display_order).all()
    return render_template('admin/product-add.html', categories=categories)

@app.route('/admin/product/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(product_id):
    """Edit product"""
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        try:
            product.name = request.form.get('name')
            # FIXED: Use category_id
            product.category_id = int(request.form.get('category_id'))
            product.price = int(request.form.get('price'))
            product.description = request.form.get('description')
            product.rules = request.form.get('rules')
            product.country = request.form.get('country')
            product.features = request.form.get('features')
            product.is_active = 'is_active' in request.form

            db.session.commit()
            flash('Product updated successfully!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating product: {str(e)}', 'error')

    categories = Category.query.order_by(Category.display_order).all()
    return render_template('admin/product-edit.html', product=product, categories=categories)

@app.route('/admin/product/delete/<int:product_id>', methods=['POST'])
@admin_required
def admin_product_delete(product_id):
    """Delete product"""
    product = Product.query.get_or_404(product_id)

    accounts_count = Account.query.filter_by(product_id=product_id).count()
    if accounts_count > 0:
        flash(f'Cannot delete: {accounts_count} accounts exist for this product', 'error')
        return redirect(url_for('admin_products'))

    try:
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting product: {str(e)}', 'error')
        
    return redirect(url_for('admin_products'))

# ========== ADMIN ACCOUNT MANAGEMENT ==========

@app.route('/admin/accounts')
@admin_required
def admin_accounts():
    """View all accounts"""
    product_id = request.args.get('product_id', type=int)
    show = request.args.get('show', 'all')

    query = Account.query
    if product_id:
        query = query.filter_by(product_id=product_id)
    if show == 'sold':
        query = query.filter_by(sold=True)
    elif show == 'unsold':
        query = query.filter_by(sold=False)

    accounts = query.order_by(Account.created_at.desc()).all()
    products = Product.query.all()

    return render_template('admin/accounts.html',
                         accounts=accounts,
                         products=products,
                         current_product=product_id,
                         current_show=show)

@app.route('/admin/accounts/upload', methods=['GET', 'POST'])
@admin_required
def admin_accounts_upload():
    """Bulk upload accounts"""
    if request.method == 'POST':
        product_id = request.form.get('product_id')
        accounts_text = request.form.get('accounts')

        if not product_id or not accounts_text:
            flash('Please select product and paste accounts', 'error')
            return redirect(url_for('admin_accounts_upload'))

        lines = accounts_text.strip().split('\n')
        count = 0
        errors = []

        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            parts = line.split('|')
            if len(parts) < 2:
                errors.append(f"Line {i}: Missing required fields (need at least email|password)")
                continue

            account = Account(
                product_id=product_id,
                account_email=parts[0].strip(),
                account_password=parts[1].strip(),
                account_2fa=parts[2].strip() if len(parts) > 2 else '',
                additional_info=parts[3].strip() if len(parts) > 3 else ''
            )
            db.session.add(account)
            count += 1

        if count > 0:
            try:
                db.session.commit()
                flash(f'Successfully added {count} accounts!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error saving accounts: {str(e)}', 'error')
        else:
            flash('No valid accounts found', 'error')

        if errors:
            for error in errors[:5]:  # Show first 5 errors only
                flash(error, 'error')

        return redirect(url_for('admin_accounts'))

    products = Product.query.all()
    return render_template('admin/account-upload.html', products=products)

@app.route('/admin/account/add', methods=['GET', 'POST'])
@admin_required
def admin_account_add():
    """Add single account"""
    if request.method == 'POST':
        try:
            account = Account(
                product_id=request.form.get('product_id'),
                account_email=request.form.get('account_email'),
                account_password=request.form.get('account_password'),
                account_2fa=request.form.get('account_2fa'),
                account_phone=request.form.get('account_phone'),
                account_cookies=request.form.get('account_cookies'),
                additional_info=request.form.get('additional_info'),
                notes=request.form.get('notes')
            )
            db.session.add(account)
            db.session.commit()
            flash('Account added successfully!', 'success')
            return redirect(url_for('admin_accounts'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding account: {str(e)}', 'error')

    products = Product.query.all()
    return render_template('admin/account-add.html', products=products)

@app.route('/admin/account/edit/<int:account_id>', methods=['GET', 'POST'])
@admin_required
def admin_account_edit(account_id):
    """Edit account"""
    account = Account.query.get_or_404(account_id)

    if account.sold:
        flash('Cannot edit sold account', 'error')
        return redirect(url_for('admin_accounts'))

    if request.method == 'POST':
        try:
            account.account_email = request.form.get('account_email')
            account.account_password = request.form.get('account_password')
            account.account_2fa = request.form.get('account_2fa')
            account.account_phone = request.form.get('account_phone')
            account.account_cookies = request.form.get('account_cookies')
            account.additional_info = request.form.get('additional_info')
            account.notes = request.form.get('notes')

            db.session.commit()
            flash('Account updated successfully!', 'success')
            return redirect(url_for('admin_accounts'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating account: {str(e)}', 'error')

    products = Product.query.all()
    return render_template('admin/account-edit.html', account=account, products=products)

@app.route('/admin/account/delete/<int:account_id>', methods=['POST'])
@admin_required
def admin_account_delete(account_id):
    """Delete account"""
    account = Account.query.get_or_404(account_id)

    if account.sold:
        flash('Cannot delete sold account', 'error')
        return redirect(url_for('admin_accounts'))

    try:
        db.session.delete(account)
        db.session.commit()
        flash('Account deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting account: {str(e)}', 'error')
        
    return redirect(url_for('admin_accounts'))

# ========== ADMIN ORDER MANAGEMENT ==========

@app.route('/admin/orders')
@admin_required
def admin_orders():
    """View all orders"""
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)

@app.route('/admin/order/<int:order_id>')
@admin_required
def admin_order_detail(order_id):
    """View single order"""
    order = Order.query.get_or_404(order_id)
    return render_template('admin/order-detail.html', order=order)

# ========== API ROUTES ==========

@app.route('/api/check-stock/<int:product_id>')
def api_check_stock(product_id):
    """Check available stock for product"""
    count = Account.query.filter_by(product_id=product_id, sold=False).count()
    return jsonify({'available': count, 'product_id': product_id})

@app.route('/api/categories')
def api_categories():
    """Get all categories"""
    categories = Category.query.order_by(Category.display_order).all()
    return jsonify([c.to_dict() for c in categories])

@app.route('/api/products/<int:product_id>')
def api_product(product_id):
    """Get product details"""
    product = Product.query.get_or_404(product_id)
    return jsonify(product.to_dict())

@app.route('/api/search')
def api_search():
    """Search products"""
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])

    products = Product.query.filter(
        or_(
            Product.name.ilike(f'%{query}%'),
            Product.description.ilike(f'%{query}%')
        ),
        Product.is_active == True
    ).limit(10).all()

    return jsonify([{
        'id': p.id,
        'name': p.name,
        'price': p.price,
        'category': p.category_ref.name if p.category_ref else None,
        'image': url_for('static', filename=f'logos/{p.category_ref.slug if p.category_ref else "default"}-logo.png')
    } for p in products])

@app.route('/api/chat', methods=['POST'])
def chat():
    """AI Chatbot with multiple model fallbacks"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    message = data.get('message', '')
    user = User.query.get(session['user_id'])
    
    system_prompt = f"""You are Melody AI, the official assistant for Melody Store.

📌 YOUR IDENTITY:
- Name: Melody AI
- Store: Melody Store (premium digital accounts marketplace)
- Customer: {user.name} ({user.email})

📌 WHAT YOU DO:
- Help customers find products (TikTok, Instagram, Twitter, Facebook, VPN, Texting Apps)
- Explain instant delivery process
- Answer pricing questions
- Provide account usage tips
- Be friendly and engaging

📌 WHAT YOU DON'T DO:
- Ask for passwords, card details, or sensitive info
- Process refunds (direct to support@melodystore.com)
- Guarantee accounts beyond 7-day warranty
- Share other customers' info
- Be rude or unprofessional

📌 TONE:
- Professional but warm
- Use emojis occasionally 😊
- Be helpful first, funny second
- Keep responses under 200 words

Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
User location: Nigeria (inferred)

Respond naturally to: {message}"""

    # Try each model until one works
    last_error = None
    
    for model in GROQ_MODELS:
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 500,
                    "top_p": 0.9
                },
                timeout=15
            )
            
            if response.status_code == 200:
                result = response.json()
                reply = result['choices'][0]['message']['content']
                logger.info(f"Chat success with model: {model}")
                return jsonify({
                    'reply': reply,
                    'model': model,
                    'success': True
                })
            else:
                logger.warning(f"Model {model} failed with status {response.status_code}")
                last_error = f"Model {model} failed"
                continue
                
        except requests.exceptions.Timeout:
            logger.warning(f"Model {model} timed out")
            last_error = "Request timed out"
            continue
        except requests.exceptions.RequestException as e:
            logger.warning(f"Model {model} error: {str(e)}")
            last_error = str(e)
            continue
        except Exception as e:
            logger.warning(f"Unexpected error with {model}: {str(e)}")
            last_error = str(e)
            continue
    
    # All models failed
    logger.error(f"All models failed. Last error: {last_error}")
    return jsonify({
        'reply': "I'm having trouble connecting right now. Please try again in a moment or contact support@melody-store.onrender.com 🙏",
        'success': False,
        'error': last_error
    }), 200  # Still return 200 so frontend doesn't crash

@app.route('/admin/test-email', methods=['GET', 'POST'])
@admin_required
def admin_test_email():
    """Test email configuration with form"""
    
    if request.method == 'POST':
        # Get form data
        recipient_name = request.form.get('name', 'Test User')
        recipient_email = request.form.get('email')
        
        if not recipient_email:
            flash('Please enter an email address', 'error')
            return redirect(url_for('admin_test_email'))
        
        try:
            # Create HTML email with personalized content
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Test Email - Melody Store</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        line-height: 1.6;
                        color: #333;
                        background-color: #f9fafb;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 0 auto;
                        background: white;
                        border-radius: 12px;
                        overflow: hidden;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        background: linear-gradient(135deg, #2563eb, #1d4ed8);
                        color: white;
                        padding: 30px 20px;
                        text-align: center;
                    }}
                    .header h1 {{
                        margin: 0;
                        font-size: 28px;
                    }}
                    .content {{
                        padding: 30px;
                    }}
                    .success-icon {{
                        font-size: 60px;
                        text-align: center;
                        margin-bottom: 20px;
                    }}
                    .info-box {{
                        background: #f3f4f6;
                        border-radius: 8px;
                        padding: 20px;
                        margin: 20px 0;
                    }}
                    .footer {{
                        background: #f3f4f6;
                        padding: 20px;
                        text-align: center;
                        font-size: 12px;
                        color: #6b7280;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>📧 Test Email</h1>
                    </div>
                    <div class="content">
                        <div class="success-icon">✅</div>
                        <h2 style="text-align: center;">Hello {recipient_name}!</h2>
                        <p>This is a test email from <strong>Melody Store</strong>.</p>
                        <p>Your email configuration is working correctly!</p>
                        
                        <div class="info-box">
                            <h3>📋 Configuration Details</h3>
                            <p><strong>SMTP Server:</strong> {app.config['MAIL_SERVER']}</p>
                            <p><strong>Port:</strong> {app.config['MAIL_PORT']}</p>
                            <p><strong>TLS:</strong> {app.config['MAIL_USE_TLS']}</p>
                            <p><strong>From:</strong> {app.config['MAIL_USERNAME']}</p>
                            <p><strong>To:</strong> {recipient_email}</p>
                            <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                        </div>
                        
                        <p>If you received this email, your email system is ready to send order confirmations to customers!</p>
                        
                        <p style="margin-top: 20px;">
                            Best regards,<br>
                            <strong>Melody Store Team</strong>
                        </p>
                    </div>
                    <div class="footer">
                        <p>This is a test email from Melody Store Admin Panel</p>
                        <p>&copy; 2024 Melody Store. All rights reserved.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
Test Email - Melody Store
========================

Hello {recipient_name}!

This is a test email from Melody Store. Your email configuration is working correctly!

Configuration Details:
- SMTP Server: {app.config['MAIL_SERVER']}
- Port: {app.config['MAIL_PORT']}
- TLS: {app.config['MAIL_USE_TLS']}
- From: {app.config['MAIL_USERNAME']}
- To: {recipient_email}
- Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

If you received this email, your email system is ready to send order confirmations to customers!

Best regards,
Melody Store Team
            """
            
            msg = Message(
                subject=f"Test Email - Melody Store (from {recipient_name})",
                recipients=[recipient_email],
                body=text_content,
                html=html_content
            )
            
            mail.send(msg)
            flash(f'✅ Test email sent successfully to {recipient_email}!', 'success')
            logger.info(f"Test email sent to {recipient_email}")
            
        except Exception as e:
            flash(f'❌ Failed to send test email: {str(e)}', 'error')
            logger.error(f"Test email failed: {str(e)}")
        
        return redirect(url_for('admin_dashboard'))
    
    # GET request - show the form with email config
    email_config = {
        'server': app.config.get('MAIL_SERVER', 'Not configured'),
        'port': app.config.get('MAIL_PORT', 'Not configured'),
        'tls': app.config.get('MAIL_USE_TLS', False),
        'username': app.config.get('MAIL_USERNAME', 'Not configured')
    }
    
    return render_template('admin/test-email.html', email_config=email_config)

if __name__ == '__main__':
    app.run(debug=False)
