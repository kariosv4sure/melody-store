from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import json
import uuid

db = SQLAlchemy()

# ========== ADMIN MODEL ==========

class Admin(db.Model):
    __tablename__ = 'admins'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ========== CATEGORY MODEL ==========

class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False, index=True)
    icon = db.Column(db.String(50))
    description = db.Column(db.String(200))
    display_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    products = db.relationship('Product', backref='category_ref', lazy=True)

# ========== PRODUCT MODEL ==========

class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)

    # Proper relationship
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), index=True)

    price = db.Column(db.Numeric(10, 2), nullable=False)

    description = db.Column(db.Text)
    rules = db.Column(db.Text)

    image = db.Column(db.String(200), default='default.jpg')

    # Use JSON instead of string
    features = db.Column(db.JSON)

    country = db.Column(db.String(100))

    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    accounts = db.relationship(
        'Account',
        backref='product_ref',
        lazy=True,
        cascade='all, delete-orphan'
    )

    def get_available_count(self):
        return Account.query.filter_by(product_id=self.id, sold=False).count()

    def get_image_url(self):
        if self.image and self.image != 'default.jpg':
            return f'uploads/{self.image}'

        if self.category_ref:
            return f'logos/{self.category_ref.slug}-logo.png'

        return 'logos/default.png'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category_ref.name if self.category_ref else None,
            'price': float(self.price),
            'description': self.description,
            'rules': self.rules,
            'image': self.get_image_url(),
            'features': self.features or [],
            'country': self.country,
            'available': self.get_available_count(),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ========== ACCOUNT MODEL ==========

class Account(db.Model):
    __tablename__ = 'accounts'

    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id'),
        nullable=False,
        index=True
    )

    # Account details (⚠️ sensitive)
    account_email = db.Column(db.String(200))
    account_password = db.Column(db.String(200))
    account_2fa = db.Column(db.String(500))
    account_phone = db.Column(db.String(50))
    account_cookies = db.Column(db.JSON)
    additional_info = db.Column(db.Text)

    # Status
    sold = db.Column(db.Boolean, default=False)
    sold_to_email = db.Column(db.String(200))
    sold_to_name = db.Column(db.String(200))
    order_id = db.Column(db.String(50))
    sold_at = db.Column(db.DateTime)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    def mark_as_sold(self, customer_name, customer_email, order_ref):
        self.sold = True
        self.sold_to_name = customer_name
        self.sold_to_email = customer_email
        self.order_id = order_ref
        self.sold_at = datetime.utcnow()

    def to_delivery_dict(self):
        return {
            'email': self.account_email,
            'password': self.account_password,
            'two_fa': self.account_2fa,
            'phone': self.account_phone,
            'additional_info': self.additional_info
        }


# ========== ORDER MODEL ==========

class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)

    order_number = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # Customer
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(200), nullable=False)

    # Product relation
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    product = db.relationship('Product')

    product_name = db.Column(db.String(200))
    product_category = db.Column(db.String(50))

    amount = db.Column(db.Numeric(10, 2))

    # Account relation (important)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'))
    account = db.relationship('Account')

    # Snapshot (for history)
    account_email = db.Column(db.String(200))
    account_password = db.Column(db.String(200))
    account_2fa = db.Column(db.String(500))
    account_notes = db.Column(db.Text)

    # Payment
    payment_ref = db.Column(db.String(100))
    payment_status = db.Column(db.String(20), default='paid')

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)

    def generate_order_number(self):
        self.order_number = f"MS-{uuid.uuid4().hex[:8].upper()}"
        return self.order_number

    def to_dict(self):
        return {
            'order_number': self.order_number,
            'customer_name': self.customer_name,
            'customer_email': self.customer_email,
            'product_name': self.product_name,
            'amount': float(self.amount) if self.amount else 0,
            'payment_ref': self.payment_ref,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'delivered_at': self.delivered_at.isoformat() if self.delivered_at else None
        }

# ============= USER MODEL ============

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
