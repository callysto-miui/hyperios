import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
import requests
import uuid
import time
import hashlib
from functools import wraps

app = Flask(__name__)
# Critical: Set secret key for sessions
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-strong-secret-key-here-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hyperos_bot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'

db = SQLAlchemy(app)

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Admin credentials
ADMIN_USERNAME = 'JEPFX'
ADMIN_PASSWORD = 'JEPFXADMIN'

# Models
class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(500), unique=True, nullable=False)
    label = db.Column(db.String(100))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_apply = db.Column(db.DateTime, nullable=True)
    success_count = db.Column(db.Integer, default=0)

class ApplyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    success = db.Column(db.Boolean)
    message = db.Column(db.Text)
    response_code = db.Column(db.Integer)

# Create tables
with app.app_context():
    db.create_all()

# Admin login decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please login first', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # If already logged in, redirect to dashboard
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        print(f"Login attempt - Username: {username}")  # Debug log
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session.permanent = True  # Make session persistent
            flash('Login successful!', 'success')
            print("Login successful - redirecting to dashboard")  # Debug log
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'error')
            print("Login failed - invalid credentials")  # Debug log
    
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    accounts = Account.query.all()
    logs = ApplyLog.query.order_by(ApplyLog.timestamp.desc()).limit(100).all()
    return render_template('admin_dashboard.html', accounts=accounts, logs=logs, now=datetime.now())

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('admin_login'))

# API Routes
@app.route('/api/account/add', methods=['POST'])
def add_account():
    try:
        data = request.json
        token = data.get('token')
        label = data.get('label', f'Account_{uuid.uuid4().hex[:8]}')
        
        if not token:
            return jsonify({'error': 'Token required'}), 400
        
        # Check if account exists
        existing = Account.query.filter_by(token=token).first()
        if existing:
            return jsonify({'error': 'Account already exists'}), 400
        
        account = Account(token=token, label=label)
        db.session.add(account)
        db.session.commit()
        
        # Schedule daily job
        schedule_account_job(account)
        
        return jsonify({'message': 'Account added successfully', 'id': account.id})
    except Exception as e:
        print(f"Error adding account: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/account/remove', methods=['POST'])
@admin_required
def remove_account():
    try:
        data = request.json
        token = data.get('token')
        
        account = Account.query.filter_by(token=token).first()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        # Remove scheduled job
        job_id = f'unlock_{account.id}'
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        
        db.session.delete(account)
        db.session.commit()
        return jsonify({'message': 'Account removed successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/account/toggle', methods=['POST'])
@admin_required
def toggle_account():
    try:
        data = request.json
        token = data.get('token')
        
        account = Account.query.filter_by(token=token).first()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        account.active = not account.active
        db.session.commit()
        
        if account.active:
            schedule_account_job(account)
        else:
            job_id = f'unlock_{account.id}'
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
        
        return jsonify({'message': f'Account {"activated" if account.active else "paused"}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/accounts', methods=['GET'])
@admin_required
def list_accounts():
    accounts = Account.query.all()
    return jsonify([{
        'id': a.id,
        'label': a.label,
        'active': a.active,
        'created_at': a.created_at.isoformat(),
        'last_apply': a.last_apply.isoformat() if a.last_apply else None,
        'success_count': a.success_count
    } for a in accounts])

@app.route('/api/apply_now', methods=['POST'])
@admin_required
def apply_now():
    try:
        data = request.json
        token = data.get('token')
        
        account = Account.query.filter_by(token=token).first()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        if not account.active:
            return jsonify({'error': 'Account is paused'}), 400
        
        result = apply_unlock(account)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/<token>', methods=['GET'])
@admin_required
def get_logs(token):
    logs = ApplyLog.query.filter_by(token=token).order_by(ApplyLog.timestamp.desc()).limit(50).all()
    return jsonify([{
        'timestamp': l.timestamp.isoformat(),
        'success': l.success,
        'message': l.message,
        'response_code': l.response_code
    } for l in logs])

@app.route('/api/logs/all', methods=['GET'])
@admin_required
def get_all_logs():
    logs = ApplyLog.query.order_by(ApplyLog.timestamp.desc()).limit(200).all()
    return jsonify([{
        'timestamp': l.timestamp.isoformat(),
        'token': l.token[:20] + '...',
        'success': l.success,
        'message': l.message
    } for l in logs])

@app.route('/api/time', methods=['GET'])
def get_time():
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing_tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    countdown = (midnight - now).total_seconds()
    
    return jsonify({
        'beijing_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'countdown': countdown,
        'midnight': midnight.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/api/scheduler/status', methods=['GET'])
@admin_required
def scheduler_status():
    jobs = scheduler.get_jobs()
    return jsonify({
        'running': scheduler.running,
        'jobs': [{'id': j.id, 'next_run': str(j.next_run_time)} for j in jobs]
    })

def schedule_account_job(account):
    """Schedule daily unlock attempt at 23:59:59 Beijing time"""
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing_tz)
    
    # Schedule for today 23:59:59 or tomorrow if past that time
    target_time = now.replace(hour=23, minute=59, second=59, microsecond=0)
    if now >= target_time:
        target_time += timedelta(days=1)
    
    job_id = f'unlock_{account.id}'
    
    # Remove existing job if any
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    # Add main job
    scheduler.add_job(
        id=job_id,
        func=apply_unlock_with_logging,
        args=[account.id],
        trigger='date',
        run_date=target_time,
        replace_existing=True
    )
    
    # Add retry jobs (5 attempts with 2-second intervals)
    for i in range(1, 6):
        retry_time = target_time + timedelta(seconds=i*2)
        retry_job_id = f'{job_id}_retry_{i}'
        if scheduler.get_job(retry_job_id):
            scheduler.remove_job(retry_job_id)
        scheduler.add_job(
            id=retry_job_id,
            func=apply_unlock_with_logging,
            args=[account.id],
            trigger='date',
            run_date=retry_time,
            replace_existing=True
        )

def apply_unlock_with_logging(account_id):
    """Wrapper to log unlock attempts"""
    with app.app_context():
        account = Account.query.get(account_id)
        if account and account.active:
            result = apply_unlock(account)
            return result
    return None

def apply_unlock(account):
    """Make unlock request to Xiaomi API"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cookie': f'new_bbs_serviceToken={account.token}',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Referer': 'https://c.mi.com/',
        'Origin': 'https://c.mi.com'
    }
    
    # Xiaomi unlock API endpoint
    url = 'https://c.mi.com/unlock/apply'
    
    results = []
    for attempt in range(5):
        try:
            print(f"Attempt {attempt + 1} for account {account.label}")
            response = requests.post(url, headers=headers, timeout=15, allow_redirects=True)
            
            # Check if successful
            success = response.status_code == 200 and ('success' in response.text.lower() or '成功' in response.text)
            
            log = ApplyLog(
                token=account.token,
                success=success,
                message=f'Attempt {attempt + 1}: HTTP {response.status_code} - {response.text[:200]}',
                response_code=response.status_code
            )
            db.session.add(log)
            db.session.commit()
            
            if success:
                account.success_count += 1
                account.last_apply = datetime.utcnow()
                db.session.commit()
                results.append({'attempt': attempt + 1, 'success': True, 'message': 'Unlock successful!'})
                break
            else:
                results.append({'attempt': attempt + 1, 'success': False, 'message': f'HTTP {response.status_code}'})
            
            if attempt < 4:  # Don't sleep after last attempt
                time.sleep(2)
                
        except Exception as e:
            print(f"Error in attempt {attempt + 1}: {e}")
            log = ApplyLog(
                token=account.token,
                success=False,
                message=f'Attempt {attempt + 1} error: {str(e)}',
                response_code=0
            )
            db.session.add(log)
            db.session.commit()
            results.append({'attempt': attempt + 1, 'success': False, 'error': str(e)})
    
    return {'token': account.label, 'results': results}

# Shutdown scheduler
import atexit
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
