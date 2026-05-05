import os
import io
import zipfile
import uuid
import secrets 
import random 
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, jsonify, json, send_file, flash, redirect, url_for, session, send_from_directory
import qrcode
from qrcode.image.styledpil import StyledPilImage 
from qrcode.image.styles.colormasks import SolidFillColorMask 
from slugify import slugify
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageOps
from pdf2image import convert_from_bytes
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from email_validator import validate_email
import stripe
from dotenv import load_dotenv
from flask_mail import Mail, Message # <-- নতুন সংযোজন
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature # <-- নতুন সংযোজন
from rembg import remove
import time
import shutil
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv() 

app = Flask(__name__)
app.secret_key = "a-very-secret-key-for-my-gizmo-business" 

# --- ফোল্ডার কনফিগারেশন ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
UPLOAD_FOLDER = os.path.join(STATIC_FOLDER, 'uploads_studio')
PROCESSED_FOLDER = os.path.join(STATIC_FOLDER, 'processed_studio')
USER_FILES_FOLDER = os.path.join(STATIC_FOLDER, 'user_files')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER
app.config['USER_FILES_FOLDER'] = USER_FILES_FOLDER 
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- ডাটাবেস কনফিগারেশন ---
# DATABASE_URL এনভায়রনমেন্ট ভেরিয়েবল থাকলে সেটি ব্যবহার করবে, না থাকলে ডিফল্টভাবে SQLite ব্যবহার করবে
db_url = os.getenv('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'site.db'))

# লাইভ সার্ভারে (যেমন Heroku/Render) অনেক সময় 'postgres://' থাকে, যা SQLAlchemy 1.4+ এ সাপোর্ট করে না। 
# তাই এটিকে 'postgresql://' এ কনভার্ট করে নিতে হয়।
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)

# --- Stripe কী কনফিগারেশন ---
app.config['STRIPE_PUBLISHABLE_KEY'] = os.getenv('STRIPE_PUBLISHABLE_KEY')
app.config['STRIPE_SECRET_KEY'] = os.getenv('STRIPE_SECRET_KEY')
app.config['STRIPE_PRICE_ID'] = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = app.config['STRIPE_SECRET_KEY']

# --- নতুন সংযোজন: Flask-Mail কনফিগারেশন ---
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() in ['true', 'on', '1']
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = ('MyGizmo', os.getenv('MAIL_USERNAME'))
mail = Mail(app)
# টোকেনের জন্য সিরিয়ালাইজার (30 মিনিট = 1800 সেকেন্ড)
s = URLSafeTimedSerializer(app.secret_key)

# --- Rate Limiter কনফিগারেশন ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)
# --- কনফিগারেশন শেষ ---

# --- লগইন ম্যানেজার ---
login_manager = LoginManager(app)
login_manager.login_view = 'login' 
login_manager.login_message_category = 'info' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Auto File Cleanup System ---
def cleanup_old_files():
    """২৪ ঘণ্টার পুরনো ফাইলগুলো UPLOAD এবং PROCESSED ফোল্ডার থেকে ডিলিট করবে"""
    folders_to_clean = [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER']]
    current_time = time.time()
    
    for folder in folders_to_clean:
        if not os.path.exists(folder):
            continue
            
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path):
                    # যদি ফাইলটি ২৪ ঘণ্টার (86400 সেকেন্ড) বেশি পুরনো হয়
                    if os.stat(file_path).st_mtime < current_time - 86400:
                        os.remove(file_path)
                        print(f"Deleted old file: {filename}")
            except Exception as e:
                print(f"Error deleting file {filename}: {e}")

# ব্যাকগ্রাউন্ড শিডিউলার চালু করা (প্রতি ৬ ঘণ্টা পর পর ক্লিনআপ চলবে)
scheduler = BackgroundScheduler()
scheduler.add_job(func=cleanup_old_files, trigger="interval", hours=6)
scheduler.start()

# অ্যাপ বন্ধ হলে শিডিউলারও বন্ধ হবে
import atexit
atexit.register(lambda: scheduler.shutdown())

# --- ডাটাবেস মডেল (User Model) ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    stripe_customer_id = db.Column(db.String(120), unique=True)
    subscription_status = db.Column(db.String(50), default='inactive')
    is_admin = db.Column(db.Boolean, default=False) # এই লাইনটি আলাদা থাকবে
    is_banned = db.Column(db.Boolean, default=False)
    
    files = db.relationship('UserFile', backref='user', lazy=True, cascade="all, delete-orphan")
    posts = db.relationship('Post', backref='author', lazy=True)
    
    def __repr__(self):
        return f"User('{self.username}', '{self.email}', '{self.subscription_status}')"
    @property
    def password(self): raise AttributeError('password is not a readable attribute')
    @password.setter
    def password(self, password): self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    def verify_password(self, password): return bcrypt.check_password_hash(self.password_hash, password)

# --- UserFile ডাটাবেস মডেল ---
class UserFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(300), nullable=False)
    saved_filename = db.Column(db.String(300), unique=True, nullable=False)
    file_type = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"UserFile('{self.original_filename}', '{self.file_type}')"

# --- API Key ডাটাবেস মডেল ---
class ApiKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, default='My Key')
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('api_keys', lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"ApiKey('{self.name}', User: {self.user_id})"


# --- Post ডাটাবেস মডেল ---
class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)
    excerpt = db.Column(db.String(300), nullable=True)
    date_posted = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    def __repr__(self):
        return f"Post('{self.title}', '{self.date_posted}')"


# --- ফর্ম ক্লাস ---
class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=30)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')
    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user: raise ValidationError('That username is taken.')
    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user: raise ValidationError('That email is already in use.')

class LoginForm(FlaskForm):

    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class UpdateAccountForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=30)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Update Profile')

    def validate_username(self, username):
        if username.data != current_user.username:
            user = User.query.filter_by(username=username.data).first()
            if user: raise ValidationError('That username is taken. Please choose a different one.')

    def validate_email(self, email):
        if email.data != current_user.email:
            user = User.query.filter_by(email=email.data).first()
            if user: raise ValidationError('That email is taken. Please choose a different one.')

class PostForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(min=5, max=200)])
    excerpt = StringField('Excerpt', validators=[DataRequired(), Length(max=300)])
    content = TextAreaField('Content', validators=[DataRequired()])
    submit = SubmitField('Post')

# --- নতুন সংযোজন: পাসওয়ার্ড রিসেট ফর্ম ---
class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')
    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user is None:
            raise ValidationError('There is no account with that email. You must register first.')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')
# --- নতুন ফর্ম শেষ ---


# --- ফোল্ডার তৈরি ---
if not os.path.exists(STATIC_FOLDER): os.makedirs(STATIC_FOLDER)
if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(PROCESSED_FOLDER): os.makedirs(PROCESSED_FOLDER)
if not os.path.exists(USER_FILES_FOLDER): os.makedirs(USER_FILES_FOLDER)

# --- টুলসের হেল্পার ফাংশন ---

def save_user_file(user, file_buffer_or_path, original_name, file_type):
    if not user or not user.is_authenticated:
        return
    try:
        unique_filename = f"{uuid.uuid4().hex}_{original_name}"
        save_path = os.path.join(app.config['USER_FILES_FOLDER'], unique_filename)
        
        if isinstance(file_buffer_or_path, (str, Path)):
            import shutil
            shutil.copy(file_buffer_or_path, save_path)
        elif hasattr(file_buffer_or_path, 'read'):
            file_buffer_or_path.seek(0)
            with open(save_path, 'wb') as f:
                f.write(file_buffer_or_path.read())
            file_buffer_or_path.seek(0)
        
        new_file = UserFile(
            original_filename=original_name,
            saved_filename=unique_filename,
            file_type=file_type,
            user_id=user.id
        )
        db.session.add(new_file)
        db.session.commit()
        print(f"File saved for user {user.id}: {unique_filename}")

    except Exception as e:
        print(f"Error saving user file: {e}")
        db.session.rollback()

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp", "gif"}
FORMAT_MAP = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "PDF": "pdf"}
def convert_jpg_to_pdf(image_files):
    pil_images = []
    for image_file in image_files:
        try:
            image = Image.open(image_file)
            if image.mode == 'RGBA' or image.mode == 'P': image = image.convert('RGB')
            pil_images.append(image)
        except Exception as e: print(f"Skipping non-image file: {e}")
    if not pil_images: return None
    pdf_buffer = io.BytesIO()
    pil_images[0].save(pdf_buffer, format='PDF', save_all=True, append_images=pil_images[1:])
    pdf_buffer.seek(0)
    return pdf_buffer
def convert_pdf_to_jpgs(pdf_file):
    pdf_bytes = pdf_file.read()
    try: images = convert_from_bytes(pdf_bytes)
    except Exception as e: print(f"PDF to JPG Error: {e} (Poppler installed?)"); return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zf:
        for i, image in enumerate(images):
            img_buffer = io.BytesIO(); image.save(img_buffer, format='JPEG'); img_buffer.seek(0)
            zf.writestr(f'page_{i+1}.jpg', img_buffer.read())
    zip_buffer.seek(0); return zip_buffer
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT
def _safe_font(size=24):
    try: return ImageFont.truetype("arial.ttf", size)
    except IOError:
        try: return ImageFont.truetype("DejaVuSans.ttf", size)
        except IOError: return ImageFont.load_default()
def add_text_watermark(img: Image.Image, text: str, position: str, opacity: float, fontsize: int):
    if not text: return img
    base = img.convert("RGBA"); overlay = Image.new("RGBA", base.size, (255, 255, 255, 0)); draw = ImageDraw.Draw(overlay); font = _safe_font(fontsize)
    try:
        bbox = draw.textbbox((0, 0), text, font=font); textwidth = bbox[2] - bbox[0]; textheight = bbox[3] - bbox[1]
    except AttributeError: textwidth, textheight = draw.textsize(text, font=font)
    margin = max(10, int(min(base.size) * 0.02))
    positions = {"bottom-right": (base.width - textwidth - margin, base.height - textheight - margin),"bottom-left": (margin, base.height - textheight - margin),"top-left": (margin, margin),"top-right": (base.width - textwidth - margin, margin),"center": ((base.width - textwidth) // 2, (base.height - textheight) // 2),}
    x, y = positions.get(position, positions["bottom-right"]); fill = (255, 255, 255, int(255 * float(opacity))); draw.text((x, y), text, font=font, fill=fill)
    combined = Image.alpha_composite(base, overlay); return combined.convert("RGB")
def add_image_watermark(img: Image.Image, wm_path: str, position: str, opacity: float, scale: float):
    if not wm_path or not os.path.exists(wm_path): return img
    try: wm = Image.open(wm_path).convert("RGBA")
    except Exception as e: raise RuntimeError(f"Watermark image load failed: {e}")
    base = img.convert("RGBA"); target_w = max(1, int(base.width * float(scale))); ratio = target_w / wm.width; target_h = int(wm.height * ratio)
    wm_resized = wm.resize((target_w, target_h), Image.LANCZOS)
    if float(opacity) < 1.0:
        alpha = wm_resized.split()[3]; alpha = ImageEnhance.Brightness(alpha).enhance(float(opacity)); wm_resized.putalpha(alpha)
    margin = max(10, int(min(base.size) * 0.02))
    pos_map = {"bottom-right": (base.width - wm_resized.width - margin, base.height - wm_resized.height - margin),"bottom-left": (margin, base.height - wm_resized.height - margin),"top-left": (margin, margin),"top-right": (base.width - wm_resized.width - margin, margin),"center": ((base.width - wm_resized.width) // 2, (base.height - wm_resized.height) // 2),}
    pos = pos_map.get(position, pos_map["bottom-right"])
    layer = Image.new("RGBA", base.size, (255, 255, 255, 0)); layer.paste(wm_resized, pos, wm_resized)
    combined = Image.alpha_composite(base, layer); return combined.convert("RGB")
def make_pdf_from_images(pil_image_paths, out_pdf_path):
    c = canvas.Canvas(str(out_pdf_path), pagesize=A4); page_w, page_h = A4
    for img_path in pil_image_paths:
        try:
            with Image.open(img_path) as im:
                im = im.convert("RGB"); img_w_px, img_h_px = im.size; ratio = min(page_w / img_w_px, page_h / img_h_px)
                draw_w = img_w_px * ratio; draw_h = img_h_px * ratio; x = (page_w - draw_w) / 2; y = (page_h - draw_h) / 2
                b = io.BytesIO(); im.save(b, format="PNG"); b.seek(0); ir = ImageReader(b)
                c.drawImage(ir, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, anchor='c'); c.showPage()
        except Exception as e: print("make_pdf_from_images: failed to add", img_path, e)
    c.save()

def hex_to_rgb(hex_color):
    """হেক্স কালার স্ট্রিংকে (#FFFFFF) একটি RGB টুপলে (255, 255, 255) রূপান্তর করে।"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    elif len(hex_color) == 3:
        return tuple(int(hex_color[i]*2, 16) for i in (0, 1, 2))
    return (0, 0, 0) # ভুল ফরম্যাট হলে কালো রিটার্ন করুন

def _generate_custom_qr(url, fill_color, back_color, logo_file_storage):
    """
    একটি কাস্টমাইজড QR কোড তৈরি করে (রং এবং লোগো সহ)।
    রিটার্ন করে একটি BytesIO অবজেক্ট (PNG ইমেজ)।
    """
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    fill_rgb = hex_to_rgb(fill_color)
    back_rgb = hex_to_rgb(back_color)

    img = qr.make_image(
        image_factory=StyledPilImage,
        color_mask=SolidFillColorMask(front_color=fill_rgb, back_color=back_rgb)
    ).convert('RGBA')

    if logo_file_storage and logo_file_storage.filename != '':
        try:
            logo = Image.open(logo_file_storage.stream).convert("RGBA")
            
            qr_size = img.size[0]
            logo_max_size = int(qr_size / 4)
            logo.thumbnail((logo_max_size, logo_max_size), Image.LANCZOS)

            pos = ((qr_size - logo.size[0]) // 2, (qr_size - logo.size[1]) // 2)
            
            img.paste(logo, pos, logo)
            
        except Exception as e:
            print(f"Error adding logo to QR code: {e}")
            pass

    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    
    return img_buffer


# --- প্রধান রুট (Main Routes) ---
@app.route('/')
def home():
    return render_template('home.html', stripe_key=app.config['STRIPE_PUBLISHABLE_KEY'])
@app.route('/tools')
def tools():
    return render_template('tools.html')

# --- স্ট্যাটিক পেইজ রুট ---
@app.route('/features')
def features(): return render_template('features.html')
@app.route('/about')
def about_us(): return render_template('about.html')
@app.route('/contact')
def contact(): return render_template('contact.html')
@app.route('/privacy')
def privacy_policy(): return render_template('privacy.html')
@app.route('/terms')
def terms_of_service(): return render_template('terms.html')

# --- Blog রুট ---
@app.route('/blog')
def blog():
    posts = Post.query.order_by(Post.date_posted.desc()).all()
    return render_template('blog.html', title='Blog', posts=posts)

@app.route('/blog/new', methods=['GET', 'POST'])
@login_required 
def create_post():
    form = PostForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            # ব্যান চেক করা হচ্ছে
            if getattr(user, 'is_banned', False):
                flash('Your account has been banned. Please contact support.', 'danger')
                return redirect(url_for('login'))
                
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('create_post.html', title='Create Post', form=form)

@app.route('/blog/post/<string:slug>')
def post(slug):
    post = Post.query.filter_by(slug=slug).first_or_404()
    return render_template('post.html', title=post.title, post=post)

# --- Auth Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('tools'))
    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            customer = stripe.Customer.create(email=form.email.data, name=form.username.data)
            user = User(username=form.username.data, email=form.email.data, password=form.password.data, stripe_customer_id=customer.id)
            db.session.add(user); db.session.commit()
            flash('Your account has been created! You are now able to log in', 'success')
            return redirect(url_for('login'))
        except stripe.error.StripeError as e: flash(f'Error creating Stripe customer: {e}', 'danger'); return render_template('register.html', title='Register', form=form)
        except Exception as e: flash(f'An error occurred: {e}', 'danger'); return render_template('register.html', title='Register', form=form)
    return render_template('register.html', title='Register', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('tools'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.verify_password(form.password.data):
            login_user(user); flash('Login Successful!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('tools'))
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout')
def logout():
    logout_user(); flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

# --- নতুন সংযোজন: পাসওয়ার্ড রিসেট রুট ---

def send_reset_email(user):
    """পাসওয়ার্ড রিসেট ইমেইল পাঠায়"""
    token = s.dumps(user.email, salt='password-reset-salt')
    reset_url = url_for('reset_token', token=token, _external=True)
    msg = Message(
        'Password Reset Request - MyGizmo',
        recipients=[user.email]
    )
    msg.body = f'''To reset your password, visit the following link:
{reset_url}

If you did not make this request then simply ignore this email and no changes will be made.
'''
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending email: {e}")
        flash('Could not send email. Please check server configuration.', 'danger')

@app.route("/reset_password", methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        send_reset_email(user)
        flash('An email has been sent with instructions to reset your password.', 'info')
        return redirect(url_for('login'))
    return render_template('reset_request.html', title='Reset Password', form=form)

@app.route("/reset_token/<token>", methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=1800) # 30 মিনিট মেয়াদ
    except (SignatureExpired, BadTimeSignature):
        flash('That is an invalid or expired token', 'danger')
        return redirect(url_for('reset_request'))
    
    user = User.query.filter_by(email=email).first()
    if user is None:
        flash('Invalid token or user not found.', 'danger')
        return redirect(url_for('reset_request'))
    
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.password = form.password.data # পাসওয়ার্ড হ্যাশ করার জন্য @password.setter ব্যবহার করবে
        db.session.commit()
        flash('Your password has been updated! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('reset_token.html', title='Reset Password', form=form)
# --- নতুন রুট শেষ ---


# --- ড্যাশবোর্ড রুট (আপডেট করা) ---
@app.route('/dashboard')
@login_required 
def dashboard():
    files = UserFile.query.filter_by(user_id=current_user.id).order_by(UserFile.created_at.desc()).limit(10).all()
    api_keys = ApiKey.query.filter_by(user_id=current_user.id).order_by(ApiKey.created_at.desc()).all()
    return render_template('dashboard.html', title='Dashboard', files=files, api_keys=api_keys)

# --- ফাইল ডাউনলোড রুট ---
@app.route('/download_file/<filename>')
@login_required
def download_file(filename):
    file_record = UserFile.query.filter_by(saved_filename=filename, user_id=current_user.id).first_or_404()
    return send_from_directory(
        app.config['USER_FILES_FOLDER'],
        filename,
        as_attachment=True,
        download_name=file_record.original_filename
    )

@app.route("/profile", methods=['GET', 'POST'])
@login_required
def profile():
    form = UpdateAccountForm()
    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.email = form.email.data
        db.session.commit()
        flash('Your account has been updated!', 'success')
        return redirect(url_for('profile'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
    return render_template('profile.html', title='Profile', form=form)

# --- Admin Panel Routes ---
def get_folder_size(folder):
    total_size = 0
    if os.path.exists(folder):
        for dirpath, dirnames, filenames in os.walk(folder):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    return total_size

@app.route("/admin")
@login_required
def admin_dashboard():
    if not getattr(current_user, 'is_admin', False):
        flash('Access Denied! You do not have permission to view this page.', 'danger')
        return redirect(url_for('home'))
    
    users = User.query.order_by(User.id.desc()).all()
    total_users = len(users)
    pro_users = sum(1 for u in users if u.subscription_status == 'active')
    
    # Calculate folder sizes
    upload_size = get_folder_size(app.config.get('UPLOAD_FOLDER', 'uploads'))
    processed_size = get_folder_size(app.config.get('PROCESSED_FOLDER', 'processed'))
    total_size_mb = round((upload_size + processed_size) / (1024 * 1024), 2)
    
    return render_template('admin_dashboard.html', title='Admin Dashboard', 
                           users=users, total_users=total_users, 
                           pro_users=pro_users, total_size_mb=total_size_mb)

@app.route("/admin/user/<int:user_id>/toggle_pro", methods=['POST'])
@login_required
def toggle_pro(user_id):
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Unauthorized'}), 403
    user = User.query.get_or_404(user_id)
    user.subscription_status = 'active' if user.subscription_status != 'active' else 'inactive'
    db.session.commit()
    flash(f"User {user.username}'s Pro status updated.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/user/<int:user_id>/toggle_ban", methods=['POST'])
@login_required
def toggle_ban(user_id):
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Unauthorized'}), 403
    user = User.query.get_or_404(user_id)
    user.is_banned = not getattr(user, 'is_banned', False)
    db.session.commit()
    status = "banned" if user.is_banned else "unbanned"
    flash(f"User {user.username} has been {status}.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/force_cleanup", methods=['POST'])
@login_required
def force_cleanup():
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Unauthorized'}), 403
    
    folders_to_clean = [app.config.get('UPLOAD_FOLDER', 'uploads'), app.config.get('PROCESSED_FOLDER', 'processed')]
    deleted_count = 0
    for folder in folders_to_clean:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"Error deleting {filename}: {e}")
    
    flash(f"Force cleanup successful! Deleted {deleted_count} files.", 'success')
    return redirect(url_for('admin_dashboard'))

# --- Stripe পেমেন্ট রুট ---
@app.route('/create-checkout-session', methods=['POST'])
@login_required 
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id, payment_method_types=['card'],
            line_items=[{'price': app.config['STRIPE_PRICE_ID'], 'quantity': 1,}],
            mode='subscription', allow_promotion_codes=True,
            success_url=url_for('success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('cancel', _external=True),
        )
        return jsonify({'id': checkout_session.id})
    except Exception as e: return jsonify(error=str(e)), 403

@app.route('/success')
def success():
    flash('Your subscription was successful!', 'success')
    return render_template('success.html')

@app.route('/cancel')
def cancel():
    flash('Your subscription process was cancelled.', 'info')
    return render_template('cancel.html')

@app.route('/create-portal-session', methods=['POST'])
@login_required 
def create_portal_session():
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id, return_url=url_for('dashboard', _external=True),
        )
        return jsonify({'url': portal_session.url})
    except Exception as e: return jsonify(error=str(e)), 403

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True); sig_header = request.headers.get('Stripe-Signature')
    try: event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except ValueError as e: return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e: return 'Invalid signature', 400
    event_type = event['type']; data = event['data']['object']
    if event_type == 'checkout.session.completed':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = 'active'; db.session.commit(); print(f"User {user.email} is now ACTIVE.")
    elif event_type == 'customer.subscription.deleted':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = 'inactive'; db.session.commit(); print(f"User {user.email} is now INACTIVE.")
    elif event_type == 'customer.subscription.updated':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = data.get('status'); db.session.commit(); print(f"User {user.email} status updated to {data.get('status')}.")
    else: print(f"Unhandled Stripe event type: {event_type}")
    return 'OK', 200

# --- API কী ম্যানেজমেন্ট রুট ---
@app.route('/api/create-key', methods=['POST'])
@login_required
def create_api_key():
    # if current_user.subscription_status != 'active':
    #     flash('API access is a Pro feature. Please upgrade your plan.', 'danger')
    #     return redirect(url_for('dashboard'))

    key_name = request.form.get('key_name', 'My New Key')
    if not key_name:
        key_name = 'My New Key'

    new_key_str = secrets.token_hex(32) 
    
    new_key = ApiKey(
        key=new_key_str,
        name=key_name,
        user_id=current_user.id
    )
    try:
        db.session.add(new_key)
        db.session.commit()
        flash(f'New API key "{key_name}" has been generated!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating API key: {e}', 'danger')
        
    return redirect(url_for('dashboard'))

@app.route('/api/delete-key/<int:key_id>', methods=['POST'])
@login_required
def delete_api_key(key_id):
    key_to_delete = ApiKey.query.get_or_404(key_id)
    
    if key_to_delete.user_id != current_user.id:
        flash('You do not have permission to delete this key.', 'danger')
        return redirect(url_for('dashboard'))
        
    try:
        db.session.delete(key_to_delete)
        db.session.commit()
        flash(f'API key "{key_to_delete.name}" has been deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting API key: {e}', 'danger')

    return redirect(url_for('dashboard'))

# --- টুলসের রুট ---
@app.route('/qr-generator', methods=['GET', 'POST'])
def qr_generator():
    qr_image_path = None
    if request.method == 'POST':
        url = request.form.get('url')
        fill_color = request.form.get('fill_color', '#000000')
        back_color = request.form.get('back_color', '#FFFFFF')
        logo_file = request.files.get('logo_image')

        if not url:
            flash("URL or text is required.", "danger")
            return redirect(url_for('qr_generator'))
        
        try:
            img_buffer = _generate_custom_qr(url, fill_color, back_color, logo_file)
            
            qr_filename = 'qr_code_generated.png'
            qr_image_path_full = os.path.join(STATIC_FOLDER, qr_filename)
            
            with open(qr_image_path_full, 'wb') as f:
                f.write(img_buffer.read())
                
            qr_image_path = qr_filename
            
            img_buffer.seek(0)
            save_user_file(current_user, img_buffer, "custom_qr.png", "QR Code")

        except Exception as e:
            flash(f"Error generating QR code: {e}", "danger")
            print(f"QR Error: {e}") 

    return render_template('qr_generator.html', qr_image_path=qr_image_path)
    
@app.route('/calculator')
def calculator(): return render_template('calculator.html')

@app.route('/slug-generator')
def slug_generator(): return render_template('slug_generator.html')

@app.route('/generate-slug', methods=['POST'])
def generate_slug():
    data = request.json; text = data.get('text', ''); separator = data.get('separator', '-')
    remove_numbers = data.get('remove_numbers', False); lowercase = data.get('lowercase', True) 
    final_slug = slugify(text, separator=separator, lowercase=lowercase)
    if remove_numbers:
        final_slug = "".join(c for c in final_slug if not c.isdigit()); final_slug = final_slug.replace(separator * 2, separator)
    return jsonify({'slug': final_slug})

@app.route('/list-randomizer')
def list_randomizer(): return render_template('list_randomizer.html')

@app.route('/tools/word-counter')
def word_counter():
    return render_template('word_counter.html')

@app.route('/tools/case-converter')
def case_converter():
    return render_template('case_converter.html')

@app.route('/tools/lorem-ipsum-generator')
def lorem_ipsum_generator():
    return render_template('lorem_ipsum.html')

@app.route('/tools/base64-encoder-decoder')
def base64_tool():
    return render_template('base64_tool.html')

@app.route('/tools/url-encoder-decoder')
def url_tool():
    return render_template('url_tool.html')

@app.route('/tools/password-generator')
def password_generator():
    return render_template('password_generator.html')

@app.route('/tools/hash-generator')
def hash_generator():
    return render_template('hash_generator.html')

@app.route('/tools/json-formatter')
def json_formatter():
    return render_template('json_formatter.html')

@app.route('/file-converter')
def file_converter(): return render_template('file_converter.html')

@app.route('/convert', methods=['POST'])
def handle_conversion():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.accept_mimetypes
    files = request.files.getlist('file')
    if not files or files[0].filename == '':
        if is_ajax: return jsonify({'error': "No selected file"}), 400
        flash("No selected file", "danger"); return redirect(url_for('file_converter'))
    conversion_type = request.form.get('conversion_type')
    
    output_buffer = None
    download_name = "download"
    
    if conversion_type == 'jpg_to_pdf':
        try:
            output_buffer = convert_jpg_to_pdf(files)
            download_name = "converted.pdf"
            if not output_buffer:
                if is_ajax: return jsonify({'error': "No valid JPG images found"}), 400
                flash("No valid JPG images found", "danger"); return redirect(url_for('file_converter'))
        except Exception as e:
            if is_ajax: return jsonify({'error': f"Error during JPG to PDF conversion: {e}"}), 500
            flash(f"Error during JPG to PDF conversion: {e}", "danger"); return redirect(url_for('file_converter'))
    elif conversion_type == 'pdf_to_jpg':
        try:
            if len(files) > 1:
                if is_ajax: return jsonify({'error': "Please upload only one PDF for PDF-to-JPG conversion."}), 400
                flash("Please upload only one PDF for PDF-to-JPG conversion.", "danger"); return redirect(url_for('file_converter'))
            output_buffer = convert_pdf_to_jpgs(files[0])
            download_name = "converted_images.zip"
            if not output_buffer:
                if is_ajax: return jsonify({'error': "Error during PDF to JPG conversion. Did you install Poppler?"}), 500
                flash("Error during PDF to JPG conversion. Did you install Poppler?", "danger"); return redirect(url_for('file_converter'))
        except Exception as e:
            if is_ajax: return jsonify({'error': f"Error during PDF to JPG conversion: {e}. (Did you install Poppler?)"}), 500
            flash(f"Error during PDF to JPG conversion: {e}. (Did you install Poppler?)", "danger"); return redirect(url_for('file_converter'))
    else:
        if is_ajax: return jsonify({'error': "Invalid conversion type"}), 400
        flash("Invalid conversion type", "danger"); return redirect(url_for('file_converter'))

    save_user_file(current_user, output_buffer, download_name, "File Converter")

    response = send_file(
        output_buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype='application/octet-stream'
    )
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return response

@app.route("/image-studio")
def image_studio(): return render_template("image_studio.html")

@app.route("/process", methods=["POST"])
@limiter.limit("5 per minute") # মিনিটে সর্বোচ্চ ৫ বার
def process_images():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.accept_mimetypes
    files = request.files.getlist("images")
    if not files or not files[0].filename:
        if is_ajax: return jsonify({'error': "Please select at least one image."}), 400
        flash("Please select at least one image.", "error"); return redirect(url_for("image_studio"))
    
    try:
        # পুরনো ফর্ম ডেটা
        resize_w = int(request.form.get("width") or 0); resize_h = int(request.form.get("height") or 0)
        keep_aspect = request.form.get("keep_aspect") == "on" or request.form.get("keep_aspect") == "true"
        watermark_text = (request.form.get("watermark_text") or "").strip()
        wm_position = request.form.get("wm_position") or "bottom-right"
        text_opacity = float(request.form.get("text_opacity") or 0.5); img_opacity = float(request.form.get("img_opacity") or 0.5)
        text_size = int(request.form.get("text_size") or 24); image_scale = float(request.form.get("image_scale") or 0.2)
        output_format = (request.form.get("output_format") or "JPEG").upper(); quality = int(request.form.get("quality") or 90)
        
        # নতুন ফর্ম ডেটা
        rotate = request.form.get("rotate")
        flip = request.form.get("flip")
        img_filter = request.form.get("img_filter")

    except Exception as e:
        if is_ajax: return jsonify({'error': f"Invalid form data: {e}"}), 400
        flash(f"Invalid form data: {e}", "error"); return redirect(url_for("image_studio"))

    if output_format not in FORMAT_MAP: output_format = "JPEG"
    quality = max(1, min(100, quality)); wm_path = None
    wm_file = request.files.get("watermark_image")
    if wm_file and wm_file.filename and allowed_file(wm_file.filename):
        wm_name = secure_filename(wm_file.filename)
        wm_path = os.path.join(app.config['UPLOAD_FOLDER'], f"wm_{uuid.uuid4().hex}_{wm_name}")
        try: wm_file.save(wm_path)
        except Exception as e: print("Watermark save failed:", e); wm_path = None
    
    processed_paths = []; errors = []
    for f in files:
        if not f or not f.filename or not allowed_file(f.filename):
            errors.append(f"{f.filename or 'Unknown file'}: unsupported type"); continue
        safe = secure_filename(f.filename); in_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{safe}")
        try:
            f.save(in_path)
            with Image.open(in_path) as im:
                im = im.convert("RGBA")

                # --- নতুন লজিক প্রয়োগ করুন ---
                
                # ১. ফিল্টার (প্রথমে প্রয়োগ করুন)
                if img_filter == "grayscale":
                    im = ImageOps.grayscale(im).convert("RGBA")
                elif img_filter == "sepia":
                    im = im.convert("RGB") 
                    sepia_matrix = [
                        0.393, 0.769, 0.189, 0,
                        0.349, 0.686, 0.168, 0,
                        0.272, 0.534, 0.131, 0
                    ]
                    im = im.convert("RGB", sepia_matrix).convert("RGBA")
                elif img_filter == "blur":
                    im = im.filter(ImageFilter.GaussianBlur(radius=2))
                elif img_filter == "sharpen":
                    im = im.filter(ImageFilter.SHARPEN)

                # ২. ঘোরানো (Rotate)
                if rotate == "90":
                    im = im.transpose(Image.ROTATE_90)
                elif rotate == "180":
                    im = im.transpose(Image.ROTATE_180)
                elif rotate == "270":
                    im = im.transpose(Image.ROTATE_270)
                
                # ৩. উল্টানো (Flip)
                if flip == "horizontal":
                    im = im.transpose(Image.FLIP_LEFT_RIGHT)
                elif flip == "vertical":
                    im = im.transpose(Image.FLIP_TOP_BOTTOM)

                # --- নতুন লজিক শেষ ---

                # ৪. রিসাইজ (আগের মতোই)
                if resize_w or resize_h:
                    if keep_aspect: target = (resize_w or im.width, resize_h or im.height); im.thumbnail(target, Image.LANCZOS)
                    else: new_w = resize_w or im.width; new_h = resize_h or im.height; im = im.resize((new_w, new_h), Image.LANCZOS)
                
                # ৫. ওয়াটারমার্ক (আগের মতোই)
                if wm_path: im = add_image_watermark(im, str(wm_path), wm_position, img_opacity, image_scale)
                if watermark_text: im = add_text_watermark(im, watermark_text, wm_position, text_opacity, text_size)
                
                # সেভ করুন
                ext = FORMAT_MAP.get(output_format, "jpg"); out_file_name = f"{uuid.uuid4().hex}_out.{ext}"
                out_path = os.path.join(app.config['PROCESSED_FOLDER'], out_file_name)
                
                if output_format == "PDF":
                    out_temp = os.path.join(app.config['PROCESSED_FOLDER'], f"{uuid.uuid4().hex}_pdfimg.png")
                    im.convert("RGB").save(out_temp, "PNG"); processed_paths.append(out_temp)
                elif output_format == "JPEG": im.convert("RGB").save(out_path, "JPEG", quality=quality); processed_paths.append(out_path)
                else: im.save(out_path, output_format); processed_paths.append(out_path)
        except Exception as e: 
            errors.append(f"{safe}: processing failed ({e})")
            print(f"Image Studio Error: {e}") 
        finally:
            if os.path.exists(in_path):
                try: os.remove(in_path)
                except Exception as e: print(f"Failed to remove temp file {in_path}: {e}")
    
    if not processed_paths:
        if is_ajax: return jsonify({'error': "No images were processed. " + ("; ".join(errors[:5]) if errors else "")}), 400
        flash("No images were processed. " + ("; ".join(errors[:5]) if errors else ""), "error")
        if wm_path and os.path.exists(wm_path): os.remove(wm_path)
        return redirect(url_for("image_studio"))
    
    output_path = None
    download_name = "download"
    mimetype = "application/octet-stream"

    if output_format == "PDF":
        pdf_file = os.path.join(app.config['PROCESSED_FOLDER'], f"batch_{uuid.uuid4().hex}.pdf")
        try:
            make_pdf_from_images(processed_paths, pdf_file)
            download_name = "MyGizmo_Converted.pdf"; mimetype = "application/pdf"; output_path = pdf_file
        except Exception as e:
            if is_ajax: return jsonify({'error': f"PDF generation failed: {e}"}), 500
            flash(f"PDF generation failed: {e}", "error"); return redirect(url_for("image_studio"))
        finally:
            for p in processed_paths:
                if os.path.exists(p): os.remove(p)
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in processed_paths: zf.write(p, arcname=os.path.basename(p))
        zip_buffer.seek(0)
        for p in processed_paths:
            if os.path.exists(p): os.remove(p)
        download_name = f"MyGizmo_Images_{uuid.uuid4().hex}.zip"; mimetype = "application/zip"; output_path = zip_buffer
    
    if wm_path and os.path.exists(wm_path): os.remove(wm_path)
    
    save_user_file(current_user, output_path, download_name, "Image Studio")
    
    response = send_file(output_path, as_attachment=True, download_name=download_name, mimetype=mimetype)
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return response


@app.route('/ai-background-remover', methods=['GET', 'POST'])
@limiter.limit("10 per minute") # মিনিটে সর্বোচ্চ ১০ বার
def ai_background_remover():
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.accept_mimetypes
        file = request.files.get('image_file') or request.files.get('image')
        if not file or not file.filename:
            if is_ajax: return jsonify({'error': 'No file selected. Please upload an image.'}), 400
            flash('No file selected. Please upload an image.', 'error')
            return redirect(url_for('ai_background_remover'))
        if not allowed_file(file.filename):
            if is_ajax: return jsonify({'error': 'Invalid file type. Please upload a JPG, PNG, or WEBP image.'}), 400
            flash('Invalid file type. Please upload a JPG, PNG, or WEBP image.', 'error')
            return redirect(url_for('ai_background_remover'))
        try:
            input_bytes = file.read()
            output_bytes = remove(input_bytes)
            output_buffer = io.BytesIO(output_bytes)
            output_buffer.seek(0)
            
            download_name = f'bg_removed_{file.filename}.png'
            
            save_user_file(current_user, output_buffer, download_name, "AI Background Remover")
            
            response = send_file(
                output_buffer,
                as_attachment=True,
                download_name=download_name,
                mimetype='image/png'
            )
            response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
            return response
        except Exception as e:
            if is_ajax: return jsonify({'error': f'Error during background removal: {e}'}), 500
            flash(f'Error during background removal: {e}', 'danger')
            return redirect(url_for('ai_background_remover'))
    return render_template('ai_background_remover.html')

# --- ================================== ---
# --- পাবলিক API রুট ---
# --- ================================== ---

def get_user_from_api_key(api_key):
    if not api_key:
        return None
    key_obj = ApiKey.query.filter_by(key=api_key).first()
    if key_obj:
        return key_obj.user
    return None

def _authenticate_api_request(req):
    api_key = req.headers.get('X-API-Key')
    if not api_key:
        return None, (jsonify({'error': 'API key is missing. Use "X-API-Key" header.'}), 401)
    
    user = get_user_from_api_key(api_key)
    
    if not user:
        return None, (jsonify({'error': 'Invalid API key.'}), 401)
        
    # if user.subscription_status != 'active':
    #     return None, (jsonify({'error': 'API access requires an active "Pro" subscription.'}), 403)
    
    return user, None 

@app.route('/api/v1/tools/slugify', methods=['POST'])
@limiter.limit("30 per minute") # API এর জন্য লিমিট
def api_slugify():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    data = request.json
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing "text" parameter in JSON body.'}), 400

    text = data.get('text', '')
    separator = data.get('separator', '-')
    remove_numbers = data.get('remove_numbers', False)
    lowercase = data.get('lowercase', True)
    
    final_slug = slugify(text, separator=separator, lowercase=lowercase)
    if remove_numbers:
        final_slug = "".join(c for c in final_slug if not c.isdigit())
        final_slug = final_slug.replace(separator * 2, separator)

    return jsonify({
        'success': True,
        'input_text': text,
        'slug': final_slug
    }), 200

@app.route('/api/v1/tools/qr-generator', methods=['POST'])
def api_qr_generator():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    if 'text' not in request.form:
        return jsonify({'error': 'Missing "text" parameter in form-data.'}), 400
    
    url = request.form.get('text')
    if not url:
        return jsonify({'error': 'Text parameter cannot be empty.'}), 400

    fill_color = request.form.get('fill_color', '#000000')
    back_color = request.form.get('back_color', '#FFFFFF')
    logo_file = request.files.get('logo_image')

    try:
        img_buffer = _generate_custom_qr(url, fill_color, back_color, logo_file)
        
        return send_file(
            img_buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name='generated_qr.png'
        )
    except Exception as e:
        return jsonify({'error': f'Could not generate QR code: {e}'}), 500

@app.route('/api/v1/tools/list-randomizer', methods=['POST'])
def api_list_randomizer():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    data = request.json
    if not data or 'items' not in data or not isinstance(data['items'], list):
        return jsonify({'error': 'Missing "items" parameter (must be a list) in JSON body.'}), 400
    
    items = data['items']
    if not items:
        return jsonify({'error': 'The "items" list cannot be empty.'}), 400
    
    try:
        num_selections = int(data.get('num_selections', 1))
    except ValueError:
        return jsonify({'error': '"num_selections" must be an integer.'}), 400
        
    if num_selections < 0 or num_selections > len(items):
        return jsonify({'error': f'Number of selections must be between 0 and {len(items)}.'}), 400

    if num_selections == 0:
        num_selections = len(items)

    random.shuffle(items)
    selected_items = items[:num_selections]
    
    return jsonify({
        'success': True,
        'selected_items_count': len(selected_items),
        'randomized_items': selected_items
    }), 200

@app.route('/api/v1/tools/bg-remover', methods=['POST'])
def api_bg_remover():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    if 'image' not in request.files:
        return jsonify({'error': 'Missing "image" file in form-data.'}), 400
        
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No selected file.'}), 400
        
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Please upload a JPG, PNG, or WEBP.'}), 400

    try:
        input_bytes = file.read()
        output_bytes = remove(input_bytes)
        output_buffer = io.BytesIO(output_bytes)
        output_buffer.seek(0)
        
        download_name = f'bg_removed_{file.filename}.png'

        return send_file(
            output_buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name=download_name
        )
    except Exception as e:
        return jsonify({'error': f'Error during background removal: {e}'}), 500

@app.route('/api/v1/tools/jpg-to-pdf', methods=['POST'])
def api_jpg_to_pdf():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    files = request.files.getlist('images')
    if not files or files[0].filename == '':
        return jsonify({'error': 'Missing "images" files in form-data.'}), 400
    
    try:
        output_buffer = convert_jpg_to_pdf(files)
        if not output_buffer:
            return jsonify({'error': 'No valid JPG images found in the upload.'}), 400
            
        return send_file(
            output_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name='converted.pdf'
        )
    except Exception as e:
        return jsonify({'error': f'Error during JPG to PDF conversion: {e}'}), 500

@app.route('/api/v1/tools/pdf-to-jpg', methods=['POST'])
def api_pdf_to_jpg():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    if 'file' not in request.files:
        return jsonify({'error': 'Missing "file" in form-data.'}), 400
        
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'No selected file or file is not a PDF.'}), 400
        
    try:
        output_buffer = convert_pdf_to_jpgs(file)
        if not output_buffer:
            return jsonify({'error': 'Could not convert PDF. Is Poppler installed?'}), 500
            
        return send_file(
            output_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name='converted_images.zip'
        )
    except Exception as e:
        return jsonify({'error': f'Error during PDF to JPG conversion: {e}'}), 500

@app.route('/api/v1/tools/image-studio', methods=['POST'])
def api_image_studio():
    user, error = _authenticate_api_request(request)
    if error:
        return error

    files = request.files.getlist("images")
    if not files or not files[0].filename:
        return jsonify({'error': 'Missing "images" files in form-data.'}), 400
    
    try:
        # পুরনো ফর্ম ডেটা
        resize_w = int(request.form.get("width") or 0); resize_h = int(request.form.get("height") or 0)
        keep_aspect = request.form.get("keep_aspect") == "on"; watermark_text = (request.form.get("watermark_text") or "").strip()
        wm_position = request.form.get("wm_position") or "bottom-right"
        text_opacity = float(request.form.get("text_opacity") or 0.5); img_opacity = float(request.form.get("img_opacity") or 0.5)
        text_size = int(request.form.get("text_size") or 24); image_scale = float(request.form.get("image_scale") or 0.2)
        output_format = (request.form.get("output_format") or "JPEG").upper(); quality = int(request.form.get("quality") or 90)
        
        # নতুন ফর্ম ডেটা
        rotate = request.form.get("rotate")
        flip = request.form.get("flip")
        img_filter = request.form.get("img_filter")

    except Exception as e:
        return jsonify({'error': f'Invalid form data: {e}'}), 400

    if output_format not in FORMAT_MAP: output_format = "JPEG"
    quality = max(1, min(100, quality)); wm_path = None
    wm_file = request.files.get("watermark_image")
    if wm_file and wm_file.filename and allowed_file(wm_file.filename):
        wm_name = secure_filename(wm_file.filename)
        wm_path = os.path.join(app.config['UPLOAD_FOLDER'], f"wm_{uuid.uuid4().hex}_{wm_name}")
        try: wm_file.save(wm_path)
        except Exception as e: print("Watermark save failed:", e); wm_path = None
    
    processed_paths = []; errors = []
    for f in files:
        if not f or not f.filename or not allowed_file(f.filename):
            errors.append(f"{f.filename or 'Unknown file'}: unsupported type"); continue
        safe = secure_filename(f.filename); in_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{safe}")
        try:
            f.save(in_path)
            with Image.open(in_path) as im:
                im = im.convert("RGBA")

                # --- নতুন লজিক প্রয়োগ করুন ---
                if img_filter == "grayscale":
                    im = ImageOps.grayscale(im).convert("RGBA")
                elif img_filter == "sepia":
                    im = im.convert("RGB") 
                    sepia_matrix = [
                        0.393, 0.769, 0.189, 0,
                        0.349, 0.686, 0.168, 0,
                        0.272, 0.534, 0.131, 0
                    ]
                    im = im.convert("RGB", sepia_matrix).convert("RGBA")
                elif img_filter == "blur":
                    im = im.filter(ImageFilter.GaussianBlur(radius=2))
                elif img_filter == "sharpen":
                    im = im.filter(ImageFilter.SHARPEN)

                if rotate == "90":
                    im = im.transpose(Image.ROTATE_90)
                elif rotate == "180":
                    im = im.transpose(Image.ROTATE_180)
                elif rotate == "270":
                    im = im.transpose(Image.ROTATE_270)
                
                if flip == "horizontal":
                    im = im.transpose(Image.FLIP_LEFT_RIGHT)
                elif flip == "vertical":
                    im = im.transpose(Image.FLIP_TOP_BOTTOM)
                # --- নতুন লজিক শেষ ---

                if resize_w or resize_h:
                    if keep_aspect: target = (resize_w or im.width, resize_h or im.height); im.thumbnail(target, Image.LANCZOS)
                    else: new_w = resize_w or im.width; new_h = resize_h or im.height; im = im.resize((new_w, new_h), Image.LANCZOS)
                
                if wm_path: im = add_image_watermark(im, str(wm_path), wm_position, img_opacity, image_scale)
                if watermark_text: im = add_text_watermark(im, watermark_text, wm_position, text_opacity, text_size)
                
                ext = FORMAT_MAP.get(output_format, "jpg"); out_file_name = f"{uuid.uuid4().hex}_out.{ext}"
                out_path = os.path.join(app.config['PROCESSED_FOLDER'], out_file_name)
                
                if output_format == "PDF":
                    out_temp = os.path.join(app.config['PROCESSED_FOLDER'], f"{uuid.uuid4().hex}_pdfimg.png")
                    im.convert("RGB").save(out_temp, "PNG"); processed_paths.append(out_temp)
                elif output_format == "JPEG": im.convert("RGB").save(out_path, "JPEG", quality=quality); processed_paths.append(out_path)
                else: im.save(out_path, output_format); processed_paths.append(out_path)
        except Exception as e: 
            errors.append(f"{safe}: processing failed ({e})")
            print(f"Image Studio Error: {e}")
        finally:
            if os.path.exists(in_path):
                try: os.remove(in_path)
                except Exception as e: print(f"Failed to remove temp file {in_path}: {e}")
    
    if not processed_paths:
        return jsonify({'error': "No images were processed.", 'details': errors}), 400
    
    output_path = None
    download_name = "download"
    mimetype = "application/octet-stream"

    if output_format == "PDF":
        pdf_file = os.path.join(app.config['PROCESSED_FOLDER'], f"batch_{uuid.uuid4().hex}.pdf")
        try:
            make_pdf_from_images(processed_paths, pdf_file)
            download_name = "MyGizmo_Converted.pdf"; mimetype = "application/pdf"; output_path = pdf_file
        except Exception as e:
            return jsonify({'error': f'PDF generation failed: {e}'}), 500
        finally:
            for p in processed_paths:
                if os.path.exists(p): os.remove(p)
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in processed_paths: zf.write(p, arcname=os.path.basename(p))
        zip_buffer.seek(0)
        for p in processed_paths:
            if os.path.exists(p): os.remove(p)
        download_name = f"MyGizmo_Images_{uuid.uuid4().hex}.zip"; mimetype = "application/zip"; output_path = zip_buffer
    
    if wm_path and os.path.exists(wm_path): os.remove(wm_path)
    
    return send_file(output_path, as_attachment=True, download_name=download_name, mimetype=mimetype)


# --- অ্যাপ রান করুন ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all() 
    app.run(debug=True)