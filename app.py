import os
import sys
import subprocess
import threading
import time
import json
import hashlib
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, render_template_string
from flask_socketio import SocketIO

# রেন্ডার বা ক্লাউড হোস্টিংয়ের জন্য বাফারিং বন্ধ করা
os.environ['PYTHONUNBUFFERED'] = '1'

app = Flask(__name__)
# সেশন সিকিউরিটির জন্য সিক্রেট কি
app.secret_key = "bot_secret_access_key_2026_99" 
# রেন্ডারে রিয়েল-টাইম লগের জন্য async_mode threading বা eventlet ব্যবহার করা হয়
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ইউজার সেশন ডাটা স্টোর করার জন্য ডিকশনারি
user_sessions = {}
ADMIN_CONFIG = "admin_config.txt"
ACCOUNTS_FILE = "accounts.json"

# Persistent web-panel accounts (separate from bot UID/password).
def _hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def load_accounts():
    accounts = {}
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                accounts = json.load(f)
        except (json.JSONDecodeError, OSError):
            accounts = {}

    # Role hierarchy: master > admin > user.
    # Keep the original admin login as a normal Admin account.
    changed = False
    if "admin" not in accounts:
        accounts["admin"] = {"password": _hash_password("changeme123"), "role": "admin", "email": ""}
        changed = True
    elif accounts["admin"].get("role") == "master":
        # Older builds treated admin as the master; preserve it as Admin now.
        accounts["admin"]["role"] = "admin"
        changed = True

    # Dedicated Master Admin account. It is a normal login account:
    # username: tanmay, password: 2015. No separate passkey is required.
    if "masteradmin" in accounts and "tanmay" not in accounts:
        accounts["tanmay"] = accounts.pop("masteradmin")
        accounts["tanmay"]["role"] = "master"
        changed = True
    if "tanmay" not in accounts:
        accounts["tanmay"] = {"password": _hash_password("2015"), "role": "master", "email": ""}
        changed = True
    elif accounts["tanmay"].get("role") != "master":
        accounts["tanmay"]["role"] = "master"
        changed = True
    # Ensure the dedicated Master Admin always uses the requested password.
    # This is a normal account login; there is no second Master passkey.
    if accounts.get("tanmay", {}).get("password") != _hash_password("2015"):
        accounts["tanmay"]["password"] = _hash_password("2015")
        changed = True

    if changed:
        save_accounts(accounts)
    return accounts

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)

load_accounts()

# --- লগইন পেইজের ডিজাইন ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Login</title>
    <style>
        body { background-color: #0d0d0d; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #fff; }
        .login-card { background-color: #1a1a1a; padding: 35px; border-radius: 20px; width: 340px; border: 1px solid #2a2a2a; box-shadow: 0 15px 35px rgba(0,0,0,0.6); }
        .logo-section { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 30px; }
        .logo-icon { background-color: #ff6b35; padding: 6px 10px; border-radius: 8px; font-weight: bold; font-size: 18px; }
        .logo-text { letter-spacing: 3px; font-weight: bold; font-size: 22px; }
        .input-group { margin-bottom: 20px; }
        .label { font-size: 12px; font-weight: bold; text-transform: uppercase; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; color: #999; }
        input { width: 100%; padding: 14px; background-color: #0a0a0a; border: 1px solid #333; border-radius: 12px; color: #fff; box-sizing: border-box; transition: 0.3s; }
        input:focus { border-color: #ff6b35; outline: none; box-shadow: 0 0 8px rgba(255, 107, 53, 0.3); }
        .login-btn { background-color: #ff6b35; color: white; border: none; width: 100%; padding: 14px; border-radius: 12px; font-weight: bold; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 10px; text-transform: uppercase; font-size: 15px; margin-top: 10px; }
        .login-btn:hover { background-color: #e55a2b; }
        .info-footer { margin-top: 30px; background-color: #0a0a0a; padding: 15px; border-radius: 12px; font-size: 11px; text-align: center; color: #777; border: 1px solid #222; }
        .info-footer span { color: #ff6b35; display: block; margin-bottom: 4px; font-weight: bold; }
        #msg { color: #ff4444; font-size: 13px; text-align: center; margin-bottom: 15px; display: none; }
        .signup-toggle { margin-top: 14px; width: 100%; padding: 12px; background: transparent; color: #ff6b35; border: 1px solid #3a3a3a; border-radius: 12px; font-weight: bold; cursor: pointer; }
        .signup-panel { display: none; margin-top: 18px; padding-top: 18px; border-top: 1px solid #2a2a2a; }
        .signup-panel.show { display: block; }
        .back-btn { background: transparent; border: 0; color: #999; cursor: pointer; margin-bottom: 12px; }
        #signup-msg { font-size: 13px; text-align: center; margin-bottom: 12px; display: none; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-section">
            <div class="logo-icon">🎮</div>
            <div class="logo-text">LOGIN</div>
        </div>
        <div id="msg">Invalid credentials!</div>
        <div class="input-group">
            <div class="label">👤 Username</div>
            <input type="text" id="u" placeholder="Enter username">
        </div>
        <div class="input-group">
            <div class="label">🔒 Password</div>
            <input type="password" id="p" placeholder="Enter password">
        </div>
        <button class="login-btn" onclick="doLogin()">➜ LOGIN</button>
        <button class="signup-toggle" onclick="showSignup()">CREATE USER ACCOUNT</button>

        <div id="signup-panel" class="signup-panel">
            <button class="back-btn" onclick="showLogin()">← Back to Login</button>
            <div id="signup-msg"></div>
            <div class="input-group">
                <div class="label">👤 Username</div>
                <input type="text" id="su" placeholder="Create username">
            </div>
            <div class="input-group">
                <div class="label">🔒 Password</div>
                <input type="password" id="sp" placeholder="Create password">
            </div>
            <div class="input-group">
                <div class="label">🔒 Confirm Password</div>
                <input type="password" id="scp" placeholder="Confirm password">
            </div>
            <div class="input-group">
                <div class="label">📧 Email</div>
                <input type="email" id="se" placeholder="Enter email">
            </div>
            <button class="login-btn" onclick="registerUser()">✓ CREATE ACCOUNT</button>
        </div>

        <div class="info-footer">
            <span>ⓘHi Welcome to Tanmay TCP Bot control </span>
            Developer @tanmayjain2015
        </div>
    </div>
    <script>
        function showSignup() {
            document.getElementById('msg').style.display = 'none';
            document.getElementById('signup-panel').classList.add('show');
        }

        function showLogin() {
            document.getElementById('signup-panel').classList.remove('show');
            document.getElementById('signup-msg').style.display = 'none';
        }

        async function registerUser() {
            const username = document.getElementById('su').value.trim();
            const password = document.getElementById('sp').value;
            const confirm_password = document.getElementById('scp').value;
            const email = document.getElementById('se').value.trim();
            const box = document.getElementById('signup-msg');
            box.style.display = 'block';
            box.style.color = '#ff4444';
            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, password, confirm_password, email})
                });
                const d = await res.json();
                box.innerText = d.message || 'Registration failed.';
                if (d.status === 'success') {
                    box.style.color = '#4ade80';
                    document.getElementById('su').value = '';
                    document.getElementById('sp').value = '';
                    document.getElementById('scp').value = '';
                    document.getElementById('se').value = '';
                    setTimeout(showLogin, 1200);
                }
            } catch (e) {
                box.innerText = 'Server error. Please try again.';
            }
        }

        async function doLogin() {
            const u = document.getElementById('u').value;
            const p = document.getElementById('p').value;
            const msg = document.getElementById('msg');
            const resp = await fetch('/api/login_auth', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: u, password: p})
            });
            const data = await resp.json();
            if(data.status === 'success') {
                window.location.href = '/';
            } else {
                msg.style.display = 'block';
                setTimeout(() => { msg.style.display = 'none'; }, 3000);
            }
        }
    </script>
</body>
</html>
"""

# অ্যাডমিন কনফিগারেশন লোড করা
def get_config():
    conf = {"pass": "admin123", "duration": 120}
    if os.path.exists(ADMIN_CONFIG):
        with open(ADMIN_CONFIG, 'r') as f:
            for line in f:
                if '=' in line:
                    parts = line.strip().split('=')
                    if len(parts) == 2:
                        key, val = parts
                        if key == 'admin_password': conf['pass'] = val
                        if key == 'global_duration': conf['duration'] = int(val)
    return conf

# অ্যাডমিন কনফিগারেশন সেভ করা
def save_config(password, duration):
    with open(ADMIN_CONFIG, 'w') as f:
        f.write(f"admin_password={password}\nglobal_duration={duration}\n")

# লগইন চেক করার ডেকোরেটর
def admin_required(f):
    def wrap(*args, **kwargs):
        if session.get('logged_in') and session.get('role') in ('admin', 'master'):
            return f(*args, **kwargs)
        return jsonify({'status': 'error', 'message': 'Admin account required!'}), 403
    wrap.__name__ = f.__name__
    return wrap

def master_required(f):
    def wrap(*args, **kwargs):
        if session.get('logged_in') and session.get('role') == 'master':
            return f(*args, **kwargs)
        return jsonify({'status': 'error', 'message': 'Master Admin account required!'}), 403
    wrap.__name__ = f.__name__
    return wrap

def login_required(f):
    def wrap(*args, **kwargs):
        if 'logged_in' in session:
            return f(*args, **kwargs)
        return redirect(url_for('login'))
    wrap.__name__ = f.__name__
    return wrap

# বটের এক্সপায়ারি চেক করার মনিটর
def expiry_monitor():
    while True:
        now = datetime.now()
        for name, data in list(user_sessions.items()):
            if data['running'] and data['end_time'] != "unlimited":
                if now > data['end_time']:
                    if data['proc']:
                        data['proc'].terminate()
                    user_sessions[name]['running'] = False
                    socketio.emit('status_update', {'running': False, 'user': name})
        time.sleep(2)

threading.Thread(target=expiry_monitor, daemon=True).start()

def stream_logs(proc, name):
    try:
        # রিয়েল-টাইম লগের জন্য iter এবং readline ব্যবহার
        for line in iter(proc.stdout.readline, ''):
            if line:
                socketio.emit('new_log', {'data': line.strip(), 'user': name})
        proc.stdout.close()
    except Exception as e:
        print(f"Logging error for {name}: {e}")

# --- রুটস (Routes) ---

@app.route('/login')
def login():
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template_string(LOGIN_HTML)

@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')
    email = data.get('email', '').strip().lower()

    import re
    if not username or not password or not confirm_password or not email:
        return jsonify({"status": "error", "message": "All fields are required."}), 400
    if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username):
        return jsonify({"status": "error", "message": "Username must be 3-32 characters and use only letters, numbers, or underscore."}), 400
    if len(password) < 6:
        return jsonify({"status": "error", "message": "Password must be at least 6 characters."}), 400
    if password != confirm_password:
        return jsonify({"status": "error", "message": "Passwords do not match."}), 400
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return jsonify({"status": "error", "message": "Enter a valid email address."}), 400

    accounts = load_accounts()
    if username in accounts:
        return jsonify({"status": "error", "message": "Username already exists."}), 409
    if any((info.get('email') or '').lower() == email for info in accounts.values() if info.get('email')):
        return jsonify({"status": "error", "message": "Email already registered."}), 409

    accounts[username] = {
        "password": _hash_password(password),
        "role": "user",
        "email": email
    }
    save_accounts(accounts)
    return jsonify({"status": "success", "message": "User account created successfully. You can now login."})

@app.route('/api/login_auth', methods=['POST'])
def login_auth():
    data = request.json
    u = data.get('username')
    p = data.get('password')
    if not isinstance(u, str) or not isinstance(p, str):
        return jsonify({"status": "error", "message": "Invalid credentials!"})
    accounts = load_accounts()
    account = accounts.get(u)
    if account and account.get("password") == _hash_password(p):
        session["logged_in"] = True
        session["username"] = u
        session["role"] = account.get("role", "user")
        return jsonify({"status": "success", "role": session["role"]})
    return jsonify({"status": "error", "message": "Invalid credentials!"})

@app.route('/')
@login_required
def index():
    response = render_template('index.html', current_role=session.get('role', 'user'), current_username=session.get('username', ''))
    from flask import make_response
    response = make_response(response)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    response = redirect(url_for('login'))
    # Prevent the browser from showing a cached authenticated page after logout.
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/check_status', methods=['POST'])
@login_required
def check_status():
    data = request.json
    name = data.get('name')
    if name in user_sessions and user_sessions[name]['running']:
        info = user_sessions[name]
        rem_sec = -1 if info['end_time'] == "unlimited" else int((info['end_time'] - datetime.now()).total_seconds())
        return jsonify({"running": True, "rem_sec": max(0, rem_sec)})
    return jsonify({"running": False})

@app.route('/api/control', methods=['POST'])
@login_required
def bot_control():
    data = request.json
    action, name, uid, pw = data.get('action'), data.get('name'), data.get('uid'), data.get('password')
    conf = get_config()

    if action == 'start':
        if not uid or not pw:
            return jsonify({"status": "error", "message": "UID/PW required!"})
        if name in user_sessions and user_sessions[name]['running']:
            return jsonify({"status": "error", "message": "ALREADY RUNNING!"})
        try:
            with open("bot.txt", "w") as f: f.write(f"uid={uid}\npassword={pw}")
            
            # সংশোধনী: sys.executable এর সাথে '-u' ফ্লাগ যুক্ত করা হয়েছে যেন লগ সাথে সাথে আসে
            proc = subprocess.Popen(
                [sys.executable, '-u', 'main.py'], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                text=True, 
                bufsize=1, 
                universal_newlines=True
            )
            
            end_time = "unlimited" if conf['duration'] == -1 else datetime.now() + timedelta(minutes=conf['duration'])
            user_sessions[name] = {'proc': proc, 'end_time': end_time, 'running': True}
            
            threading.Thread(target=stream_logs, args=(proc, name), daemon=True).start()
            
            rem_sec = (conf['duration'] * 60 if conf['duration'] != -1 else -1)
            return jsonify({"status": "success", "running": True, "rem_sec": rem_sec})
        except Exception as e: 
            return jsonify({"status": "error", "message": str(e)})

    elif action == 'stop':
        if name in user_sessions and user_sessions[name]['running']:
            if user_sessions[name]['proc']: 
                user_sessions[name]['proc'].terminate()
            user_sessions[name]['running'] = False
            return jsonify({"status": "success", "running": False})
    return jsonify({"status": "error", "message": "FAILED"})

@app.route('/api/admin', methods=['POST'])
@master_required
def admin_api():
    data = request.json
    conf = get_config()
    action = data.get('action')
    if action == 'login':
        active_users = []
        for n, i in user_sessions.items():
            if i['running']:
                rem_m = -1 if i['end_time'] == "unlimited" else max(0, int((i['end_time'] - datetime.now()).total_seconds() / 60))
                active_users.append({"name": n, "rem_min": rem_m})
        return jsonify({"status": "success", "duration": conf['duration'], "users": active_users})
    elif action == 'save_global':
        save_config(conf['pass'], int(data.get('duration', 120)))
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/accounts', methods=['POST'])
@admin_required
def accounts_api():
    data = request.json or {}
    current_role = session.get('role', 'user')
    master_ok = current_role == 'master'
    accounts = load_accounts()
    action = data.get('action')

    if action == 'list':
        return jsonify({
            "status": "success",
            "accounts": [
                {"username": name, "role": info.get("role", "user")}
                for name, info in sorted(accounts.items())
            ],
            "master": master_ok,
            "role": current_role
        })

    username = str(data.get('username', '')).strip()

    if action == 'create':
        password = str(data.get('password', ''))
        new_role = str(data.get('role', 'user')).lower()
        if not username or not password:
            return jsonify({"status": "error", "message": "Username and password are required!"})
        if new_role not in ('user', 'admin'):
            return jsonify({"status": "error", "message": "Invalid account type!"})
        if new_role == 'admin' and current_role != 'master':
            return jsonify({"status": "error", "message": "Master Admin account required to create an Admin account!"}), 403
        if len(username) < 3 or len(password) < 4:
            return jsonify({"status": "error", "message": "Username must be 3+ chars and password 4+ chars!"})
        if username in accounts:
            return jsonify({"status": "error", "message": "Username already exists!"})
        accounts[username] = {"password": _hash_password(password), "role": new_role, "email": (data.get("email") or "").strip().lower()}
        save_accounts(accounts)
        return jsonify({"status": "success", "message": f"{new_role.title()} account created!"})

    if action == 'update_password':
        new_password = str(data.get('password', ''))
        if username not in accounts:
            return jsonify({"status": "error", "message": "Account not found!"})
        target_role = accounts[username].get('role', 'user')
        if target_role == 'master':
            return jsonify({"status": "error", "message": "Master Admin account is protected!"}), 403
        if target_role == 'admin' and current_role != 'master':
            return jsonify({"status": "error", "message": "Master Admin account required to change an Admin password!"}), 403
        if len(new_password) < 4:
            return jsonify({"status": "error", "message": "Password must be 4+ characters!"})
        accounts[username]['password'] = _hash_password(new_password)
        save_accounts(accounts)
        return jsonify({"status": "success", "message": "Password updated!"})

    if action == 'delete':
        if username not in accounts:
            return jsonify({"status": "error", "message": "Account not found!"})
        if username == session.get('username'):
            return jsonify({"status": "error", "message": "You cannot delete your own account!"}), 403
        target_role = accounts[username].get('role', 'user')
        if target_role == 'master':
            return jsonify({"status": "error", "message": "Master Admin account cannot be deleted!"}), 403
        if target_role == 'admin' and current_role != 'master':
            return jsonify({"status": "error", "message": "Master Admin account required to delete an Admin account!"}), 403
        del accounts[username]
        save_accounts(accounts)
        return jsonify({"status": "success", "message": "Account deleted!"})

    return jsonify({"status": "error", "message": "Invalid action!"}), 400

@app.route('/api/proxy_guild')
@login_required
def proxy_guild():
    t, gid, reg, uid, pw = request.args.get('type'), request.args.get('guild_id'), request.args.get('region'), request.args.get('uid'), request.args.get('password')
    base_url = "https://guild-info-danger.vercel.app"
    urls = {
        'info': f"{base_url}/guild?guild_id={gid}&region={reg}",
        'join': f"{base_url}/join?guild_id={gid}&uid={uid}&password={pw}",
        'members': f"{base_url}/members?guild_id={gid}&uid={uid}&password={pw}",
        'leave': f"{base_url}/leave?guild_id={gid}&uid={uid}&password={pw}"
    }
    try:
        resp = requests.get(urls.get(t), timeout=15)
        return jsonify(resp.json())
    except: return jsonify({"error": "API Error"})

if __name__ == '__main__':
    # Render-এ পোর্ট এনভায়রনমেন্ট ভেরিয়েবল থেকে নিতে হয়
    port = int(os.environ.get("PORT", 10000))
    # Render-এ রিয়েল-টাইম লগের জন্য host '0.0.0.0' হওয়া বাধ্যতামূলক
    socketio.run(app, host='0.0.0.0', port=port)
