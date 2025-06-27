from flask import Flask, render_template_string, request, redirect, url_for, jsonify, flash, session, send_from_directory
import psycopg2
import psycopg2.extras
import os
import hashlib
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'emmanate-task-manager-secret-key-2024')

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://neondb_owner:npg_nRjsMcQUWH76@ep-lucky-waterfall-a4p2nvsz-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require")

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Database Helper Functions ---
def get_db_connection():
  try:
      conn = psycopg2.connect(DATABASE_URL)
      return conn
  except Exception as e:
      print(f"❌ Database connection error: {e}")
      return None

def init_database():
  print("🚀 Initializing database...")
  conn = get_db_connection()
  if not conn:
      print("❌ Failed to connect to database for initialization.")
      return
  try:
      with conn.cursor() as cur:
          cur.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")
          cur.execute("""
              CREATE TABLE IF NOT EXISTS users (
                  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                  full_name VARCHAR(255) NOT NULL,
                  email VARCHAR(255) UNIQUE NOT NULL,
                  password VARCHAR(255) NOT NULL,
                  bio TEXT,
                  location VARCHAR(255),
                  website VARCHAR(255),
                  profile_picture VARCHAR(255),
                  theme VARCHAR(20) DEFAULT 'dark',
                  notifications_enabled BOOLEAN DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              );
          """)
          cur.execute("""
              CREATE TABLE IF NOT EXISTS categories (
                  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                  name VARCHAR(100) NOT NULL,
                  color VARCHAR(7) NOT NULL,
                  icon VARCHAR(10) NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              );
          """)
          cur.execute("""
              CREATE TABLE IF NOT EXISTS tasks (
                  id SERIAL PRIMARY KEY,
                  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                  title VARCHAR(255) NOT NULL,
                  description TEXT,
                  category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
                  priority VARCHAR(20) DEFAULT 'medium',
                  due_date TIMESTAMP,
                  completed BOOLEAN DEFAULT FALSE,
                  completed_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              );
          """)
      conn.commit()
      print("✅ Database tables initialized successfully.")
  except Exception as e:
      print(f"❌ Database initialization error: {e}")
      conn.rollback()
  finally:
      if conn:
          conn.close()

# --- User & Task Helper Functions ---
def allowed_file(filename):
  return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def hash_password(password):
  return hashlib.sha256(password.encode()).hexdigest()

def create_default_categories(cur, user_id):
  default_categories = [
      ('Work', '#dc2626', '💼'), ('Personal', '#d97706', '🏠'),
      ('Health', '#059669', '💪'), ('Learning', '#7c3aed', '📚')
  ]
  for name, color, icon in default_categories:
      cur.execute("INSERT INTO categories (user_id, name, color, icon) VALUES (%s, %s, %s, %s)", (user_id, name, color, icon))

def get_user_by_id(user_id):
  conn = get_db_connection()
  if not conn: return None
  try:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
          user = cur.fetchone()
          return dict(user) if user else None
  finally:
      if conn:
          conn.close()

def get_user_by_email(email):
  conn = get_db_connection()
  if not conn: return None
  try:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT * FROM users WHERE email = %s", (email,))
          user = cur.fetchone()
          return dict(user) if user else None
  finally:
      if conn:
          conn.close()

def get_user_tasks(user_id, filter_param=None, category_param=None):
  conn = get_db_connection()
  if not conn: return []
  try:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          query = """
              SELECT t.*, c.name as category_name, c.color as category_color, c.icon as category_icon
              FROM tasks t
              LEFT JOIN categories c ON t.category_id = c.id
              WHERE t.user_id = %s
          """
          params = [user_id]
          if filter_param == 'pending':
              query += " AND t.completed = FALSE"
          elif filter_param == 'completed':
              query += " AND t.completed = TRUE"
          elif filter_param == 'overdue':
              query += " AND t.completed = FALSE AND t.due_date < CURRENT_TIMESTAMP"
          
          if category_param:
              query += " AND c.id = %s"
              params.append(category_param)

          query += " ORDER BY t.created_at DESC"
          cur.execute(query, tuple(params))
          tasks = cur.fetchall()
          return [dict(task) for task in tasks]
  finally:
      if conn:
          conn.close()

def get_user_categories(user_id):
  conn = get_db_connection()
  if not conn: return []
  try:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT * FROM categories WHERE user_id = %s ORDER BY created_at ASC", (user_id,))
          categories = cur.fetchall()
          return [dict(category) for category in categories]
  finally:
      if conn:
          conn.close()

def calculate_stats(user_id):
  tasks = get_user_tasks(user_id)
  total = len(tasks)
  completed = len([t for t in tasks if t['completed']])
  pending = total - completed
  overdue = len([t for t in tasks if not t['completed'] and t.get('due_date') and t['due_date'] < datetime.now()])
  return {
      'total': total, 'completed': completed, 'pending': pending, 'overdue': overdue,
      'completion_rate': round((completed / total * 100) if total > 0 else 0, 1)
  }

def get_due_notifications(user_id):
  tasks = get_user_tasks(user_id, filter_param='pending')
  notifications = []
  now = datetime.now()
  for task in tasks:
      if task.get('due_date'):
          due_date = task['due_date']
          time_diff = due_date - now
          if 0 < time_diff.total_seconds() <= 86400: # Due within 24 hours
              if time_diff.total_seconds() < 3600:
                  urgency, message = 'urgent', f"Due in {int(time_diff.total_seconds() // 60)} minutes"
              else:
                  urgency, message = 'today', f"Due today at {due_date.strftime('%H:%M')}"
              notifications.append({'task': task, 'urgency': urgency, 'message': message, 'due_date': due_date})
  return sorted(notifications, key=lambda x: x['due_date'])

def login_required(f):
  def decorated_function(*args, **kwargs):
      if 'user_id' not in session:
          flash('Please log in to access this page.', 'error')
          return redirect(url_for('login'))
      return f(*args, **kwargs)
  decorated_function.__name__ = f.__name__
  return decorated_function

# --- HTML Templates (as string variables) ---
LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }
  
  :root[data-theme="light"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.3s ease, color 0.3s ease;
  }

  .auth-container {
      width: 100%;
      max-width: 480px; /* Increased from 400px */
      padding: 2rem;
  }

  .auth-card {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 2.5rem;
      box-shadow: var(--shadow-lg);
      border: 1px solid var(--border);
  }

  .auth-header {
      text-align: center;
      margin-bottom: 2rem;
  }

  .logo-icon {
      width: 4rem;
      height: 4rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
      margin: 0 auto 1rem;
      box-shadow: var(--shadow-lg);
  }

  .auth-title {
      font-size: 1.75rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
  }

  .auth-subtitle {
      color: var(--text-secondary);
      font-size: 0.875rem;
  }

  .form-group {
      margin-bottom: 1.5rem;
  }

  .form-label {
      display: block;
      font-weight: 600;
      color: var(--text-primary);
      font-size: 0.875rem;
      margin-bottom: 0.5rem;
  }

  .form-input {
      width: 100%;
      padding: 0.75rem 1rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      transition: all 0.2s ease;
  }

  .form-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .form-input::placeholder {
      color: var(--text-muted);
  }

  .btn {
      width: 100%;
      padding: 0.75rem 1.5rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
  }

  .btn-primary {
      background: var(--primary);
      color: white;
      margin-bottom: 1rem;
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  .auth-link {
      text-align: center;
      color: var(--text-secondary);
      font-size: 0.875rem;
  }

  .auth-link a {
      color: var(--primary);
      text-decoration: none;
      font-weight: 600;
  }

  .auth-link a:hover {
      text-decoration: underline;
  }

  .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1rem;
      font-size: 0.875rem;
  }

  .alert-error {
      background: rgba(220, 38, 38, 0.1);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  .alert-success {
      background: rgba(5, 150, 105, 0.1);
      color: #86efac;
      border: 1px solid rgba(5, 150, 105, 0.3);
  }
  
  .theme-toggle-container {
      position: absolute;
      top: 1.5rem;
      right: 1.5rem;
      z-index: 10;
  }
  #theme-toggle {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      width: 2.5rem;
      height: 2.5rem;
      border-radius: 50%;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1rem;
      transition: all 0.2s ease;
  }
  #theme-toggle:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }
  #theme-toggle .fa-sun { display: none; }
  [data-theme="light"] #theme-toggle .fa-sun { display: block; }
  [data-theme="light"] #theme-toggle .fa-moon { display: none; }
</style>
</head>
<body>
<div class="theme-toggle-container">
  <button id="theme-toggle" title="Toggle theme">
      <i class="fas fa-moon"></i>
      <i class="fas fa-sun"></i>
  </button>
</div>
<div class="auth-container">
  <div class="auth-card">
      <div class="auth-header">
          <div class="logo-icon"></div>
          <h1 class="auth-title">Welcome Back</h1>
          <p class="auth-subtitle">Sign in to your Emmanate Task Manager account</p>
      </div>

      {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
              {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
              {% endfor %}
          {% endif %}
      {% endwith %}

      <form method="POST">
          <div class="form-group">
              <label class="form-label" for="email">Email Address</label>
              <input type="email" id="email" name="email" class="form-input" 
                     placeholder="Enter your email" required>
          </div>

          <div class="form-group">
              <label class="form-label" for="password">Password</label>
              <input type="password" id="password" name="password" class="form-input" 
                     placeholder="Enter your password" required>
          </div>

          <button type="submit" class="btn btn-primary">
              <i class="fas fa-sign-in-alt"></i>
              Sign In
          </button>
      </form>

      <div class="auth-link">
          Don't have an account? <a href="{{ url_for('signup') }}">Sign up here</a>
      </div>
  </div>
</div>
<script>
  const themeToggle = document.getElementById('theme-toggle');
  const htmlEl = document.documentElement;

  themeToggle.addEventListener('click', () => {
      const currentTheme = htmlEl.getAttribute('data-theme');
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
      htmlEl.setAttribute('data-theme', newTheme);
      document.cookie = `theme=${newTheme};path=/;max-age=31536000;samesite=lax`;
  });
</script>
</body>
</html>
"""
SIGNUP_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sign Up - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }
  
  :root[data-theme="light"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem 1rem;
      transition: background 0.3s ease, color 0.3s ease;
  }

  .auth-container {
      width: 100%;
      max-width: 480px; /* Increased from 400px */
  }

  .auth-card {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 2.5rem;
      box-shadow: var(--shadow-lg);
      border: 1px solid var(--border);
  }

  .auth-header {
      text-align: center;
      margin-bottom: 2rem;
  }

  .logo-icon {
      width: 4rem;
      height: 4rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
      margin: 0 auto 1rem;
      box-shadow: var(--shadow-lg);
  }

  .auth-title {
      font-size: 1.75rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
  }

  .auth-subtitle {
      color: var(--text-secondary);
      font-size: 0.875rem;
  }

  .form-group {
      margin-bottom: 1.5rem;
  }

  .form-label {
      display: block;
      font-weight: 600;
      color: var(--text-primary);
      font-size: 0.875rem;
      margin-bottom: 0.5rem;
  }

  .form-input {
      width: 100%;
      padding: 0.75rem 1rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      transition: all 0.2s ease;
  }

  .form-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .form-input::placeholder {
      color: var(--text-muted);
  }

  .btn {
      width: 100%;
      padding: 0.75rem 1.5rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
  }

  .btn-primary {
      background: var(--primary);
      color: white;
      margin-bottom: 1rem;
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  .auth-link {
      text-align: center;
      color: var(--text-secondary);
      font-size: 0.875rem;
  }

  .auth-link a {
      color: var(--primary);
      text-decoration: none;
      font-weight: 600;
  }

  .auth-link a:hover {
      text-decoration: underline;
  }

  .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1rem;
      font-size: 0.875rem;
  }

  .alert-error {
      background: rgba(220, 38, 38, 0.1);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  .alert-success {
      background: rgba(5, 150, 105, 0.1);
      color: #86efac;
      border: 1px solid rgba(5, 150, 105, 0.3);
  }
  
  .theme-toggle-container {
      position: absolute;
      top: 1.5rem;
      right: 1.5rem;
      z-index: 10;
  }
  #theme-toggle {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      color: var(--text-secondary);
      width: 2.5rem;
      height: 2.5rem;
      border-radius: 50%;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1rem;
      transition: all 0.2s ease;
  }
  #theme-toggle:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }
  #theme-toggle .fa-sun { display: none; }
  [data-theme="light"] #theme-toggle .fa-sun { display: block; }
  [data-theme="light"] #theme-toggle .fa-moon { display: none; }
</style>
</head>
<body>
<div class="theme-toggle-container">
  <button id="theme-toggle" title="Toggle theme">
      <i class="fas fa-moon"></i>
      <i class="fas fa-sun"></i>
  </button>
</div>
<div class="auth-container">
  <div class="auth-card">
      <div class="auth-header">
          <div class="logo-icon"></div>
          <h1 class="auth-title">Create Account</h1>
          <p class="auth-subtitle">Join Emmanate Task Manager today</p>
      </div>

      {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
              {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
              {% endfor %}
          {% endif %}
      {% endwith %}

      <form method="POST">
          <div class="form-group">
              <label class="form-label" for="full_name">Full Name</label>
              <input type="text" id="full_name" name="full_name" class="form-input" 
                     placeholder="Enter your full name" required>
          </div>

          <div class="form-group">
              <label class="form-label" for="email">Email Address</label>
              <input type="email" id="email" name="email" class="form-input" 
                     placeholder="Enter your email" required>
          </div>

          <div class="form-group">
              <label class="form-label" for="password">Password</label>
              <input type="password" id="password" name="password" class="form-input" 
                     placeholder="Create a password" required minlength="6">
          </div>

          <div class="form-group">
              <label class="form-label" for="confirm_password">Confirm Password</label>
              <input type="password" id="confirm_password" name="confirm_password" class="form-input" 
                     placeholder="Confirm your password" required minlength="6">
          </div>

          <button type="submit" class="btn btn-primary">
              <i class="fas fa-user-plus"></i>
              Create Account
          </button>
      </form>

      <div class="auth-link">
          Already have an account? <a href="{{ url_for('login') }}">Sign in here</a>
      </div>
  </div>
</div>
<script>
  const themeToggle = document.getElementById('theme-toggle');
  const htmlEl = document.documentElement;

  themeToggle.addEventListener('click', () => {
      const currentTheme = htmlEl.getAttribute('data-theme');
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
      htmlEl.setAttribute('data-theme', newTheme);
      document.cookie = `theme=${newTheme};path=/;max-age=31536000;samesite=lax`;
  });
</script>
</body>
</html>
"""
MAIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ user.get('theme', 'dark') }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Emmanate Task Manager - Professional Task Management</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      /* Professional Dark Theme */
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --secondary: #d97706;
      --accent: #f59e0b;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;
      
      /* Dark Theme Colors */
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      
      /* Text Colors */
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --text-inverse: #111827;
      
      /* Gold Accents */
      --gold: #fbbf24;
      --gold-light: #fde68a;
      --gold-dark: #d97706;
      
      /* Borders and Shadows */
      --border: #374151;
      --border-light: #4b5563;
      --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.3);
      --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.4), 0 1px 2px -1px rgb(0 0 0 / 0.4);
      --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.4), 0 2px 4px -2px rgb(0 0 0 / 0.4);
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.5), 0 8px 10px -6px rgb(0 0 0 / 0.5);
  }

  :root[data-theme="light"] {
      /* Professional Light Theme */
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --secondary: #d97706;
      --accent: #f59e0b;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;
      
      /* Light Theme Colors */
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-hover: #e2e8f0;
      
      /* Text Colors */
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --text-inverse: #ffffff;
      
      /* Gold Accents */
      --gold: #f59e0b;
      --gold-light: #fbbf24;
      --gold-dark: #d97706;
      
      /* Borders and Shadows */
      --border: #e2e8f0;
      --border-light: #cbd5e1;
      --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
      --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
      --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
  }

  :root {
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
      --radius-xl: 1rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
      font-feature-settings: 'cv02', 'cv03', 'cv04', 'cv11';
      transition: all 0.3s ease;
  }

  .app-container {
      display: flex;
      min-height: 100vh;
  }

  /* Sidebar */
  .sidebar {
      width: 280px;
      background: var(--bg-secondary);
      border-right: 1px solid var(--border);
      padding: 1.5rem;
      position: fixed;
      height: 100vh;
      overflow-y: auto;
      z-index: 100;
      transition: width 0.3s ease;
  }

  .sidebar.collapsed {
      width: 80px;
  }

  .sidebar.collapsed .logo-text,
  .sidebar.collapsed .nav-title,
  .sidebar.collapsed .nav-text,
  .sidebar.collapsed .nav-badge,
  .sidebar.collapsed .category-text {
      display: none;
  }

  .sidebar.collapsed .nav-item,
  .sidebar.collapsed .category-item {
      justify-content: center;
      padding: 0.75rem;
  }

  .logo {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
      position: relative;
  }

  .logo-icon {
      width: 2.5rem;
      height: 2.5rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-md);
  }

  .logo-text {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text-primary);
  }

  .nav-section {
      margin-bottom: 2rem;
  }

  .nav-title {
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.75rem;
  }

  .nav-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      color: var(--text-secondary);
      text-decoration: none;
      transition: all 0.2s ease;
      margin-bottom: 0.25rem;
      font-weight: 500;
      cursor: pointer;
  }

  .nav-item:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .nav-item.active {
      background: var(--primary);
      color: var(--text-inverse);
  }

  .nav-item i {
      width: 1.25rem;
      text-align: center;
      flex-shrink: 0;
  }

  .nav-text {
      flex: 1;
  }

  .nav-badge {
      background: var(--bg-tertiary);
      color: var(--text-muted);
      padding: 0.125rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      flex-shrink: 0;
  }

  .nav-item.active .nav-badge {
      background: rgba(255, 255, 255, 0.2);
      color: var(--text-inverse);
  }

  /* Categories Dropdown */
  .categories-dropdown {
      position: relative;
  }

  .categories-toggle {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      color: var(--text-secondary);
      background: none;
      border: none;
      width: 100%;
      text-align: left;
      cursor: pointer;
      transition: all 0.2s ease;
      font-weight: 500;
      font-size: 0.875rem;
  }

  .categories-toggle:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .categories-toggle i {
      width: 1.25rem;
      text-align: center;
  }

  .categories-toggle .dropdown-arrow {
      margin-left: auto;
      transition: transform 0.2s ease;
  }

  .categories-dropdown.open .dropdown-arrow {
      transform: rotate(180deg);
  }

  .sidebar.collapsed .categories-toggle .dropdown-arrow,
  .sidebar.collapsed .categories-toggle .nav-text {
      display: none;
  }

  .categories-menu {
      max-height: 0;
      overflow: hidden;
      transition: max-height 0.3s ease;
      margin-left: 1rem;
      margin-top: 0.5rem;
  }

  .categories-dropdown.open .categories-menu {
      max-height: 300px;
  }

  .category-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.5rem 1rem;
      border-radius: var(--radius);
      color: var(--text-secondary);
      text-decoration: none;
      transition: all 0.2s ease;
      margin-bottom: 0.25rem;
      cursor: pointer;
  }

  .category-item:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .category-item.active {
      background: var(--primary);
      color: var(--text-inverse);
  }

  .category-color {
      width: 0.75rem;
      height: 0.75rem;
      border-radius: 50%;
      flex-shrink: 0;
  }

  .category-text {
      flex: 1;
  }

  .sidebar-footer {
      margin-top: auto;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
  }

  .sidebar-toggle {
      width: 100%;
      padding: 0.75rem;
      background: var(--primary);
      border: none;
      border-radius: var(--radius);
      color: var(--text-inverse);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      font-size: 0.875rem;
      font-weight: 500;
      transition: all 0.2s ease;
  }

  .sidebar-toggle:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  /* Main Content */
  .main-content {
      flex: 1;
      margin-left: 280px;
      padding: 2rem;
      background: var(--bg-primary);
      transition: margin-left 0.3s ease;
  }

  .sidebar.collapsed + .main-content {
      margin-left: 80px;
  }

  .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
      flex-wrap: wrap;
      gap: 1rem;
  }

  .header-left h1 {
      font-size: 2rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
  }

  .header-left p {
      color: var(--text-secondary);
  }

  .header-actions {
      display: flex;
      gap: 1rem;
      align-items: center;
  }

  .search-box {
      position: relative;
  }

  .search-input {
      width: 300px;
      padding: 0.75rem 1rem 0.75rem 2.5rem;
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      background: var(--bg-secondary);
      color: var(--text-primary);
      font-size: 0.875rem;
      transition: all 0.2s ease;
  }

  .search-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .search-input::placeholder {
      color: var(--text-muted);
  }

  .search-icon {
      position: absolute;
      left: 0.75rem;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
  }

  /* Profile and Notifications Container */
  .user-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
  }

  /* Notifications */
  .notifications-container {
      position: relative;
  }

  .notifications-trigger {
      position: relative;
      padding: 0.75rem;
      border-radius: var(--radius);
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      cursor: pointer;
      transition: all 0.2s ease;
      color: var(--text-secondary);
  }

  .notifications-trigger:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .notification-badge {
      position: absolute;
      top: 0.25rem;
      right: 0.25rem;
      width: 1rem;
      height: 1rem;
      background: var(--danger);
      color: var(--text-inverse);
      border-radius: 50%;
      font-size: 0.625rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      justify-content: center;
  }

  .notifications-dropdown {
      position: absolute;
      top: 100%;
      right: 0;
      margin-top: 0.5rem;
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-lg);
      min-width: 320px;
      max-height: 400px;
      overflow-y: auto;
      z-index: 1000;
      opacity: 0;
      visibility: hidden;
      transform: translateY(-10px);
      transition: all 0.2s ease;
  }

  .notifications-dropdown.show {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
  }

  .notifications-header {
      padding: 1rem;
      border-bottom: 1px solid var(--border);
      background: var(--bg-tertiary);
  }

  .notifications-header h3 {
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-primary);
  }

  .notification-item {
      padding: 1rem;
      border-bottom: 1px solid var(--border);
      transition: all 0.2s ease;
  }

  .notification-item:last-child {
      border-bottom: none;
  }

  .notification-item:hover {
      background: var(--bg-hover);
  }

  .notification-title {
      font-weight: 600;
      font-size: 0.875rem;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
  }

  .notification-message {
      font-size: 0.75rem;
      color: var(--text-secondary);
      margin-bottom: 0.5rem;
  }

  .notification-urgency {
      display: inline-block;
      padding: 0.125rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.625rem;
      font-weight: 600;
      text-transform: uppercase;
  }

  .notification-urgency.urgent {
      background: rgba(220, 38, 38, 0.2);
      color: #fca5a5;
  }

  .notification-urgency.today {
      background: rgba(217, 119, 6, 0.2);
      color: #fed7aa;
  }

  .notification-urgency.tomorrow {
      background: rgba(5, 150, 105, 0.2);
      color: #86efac;
  }

  /* Profile Menu */
  .profile-container {
      position: relative;
  }

  .profile-trigger {
      width: 2.5rem;
      height: 2.5rem;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-inverse);
      font-weight: 600;
      font-size: 0.875rem;
      overflow: hidden;
      cursor: pointer;
      transition: all 0.2s ease;
      border: 2px solid var(--border);
  }

  .profile-trigger:hover {
      transform: scale(1.05);
      box-shadow: var(--shadow-md);
  }

  .profile-trigger img {
      width: 100%;
      height: 100%;
      object-fit: cover;
  }

  .profile-dropdown {
      position: absolute;
      top: 100%;
      right: 0;
      margin-top: 0.5rem;
      background: var(--bg-secondary);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-lg);
      min-width: 200px;
      z-index: 1000;
      opacity: 0;
      visibility: hidden;
      transform: translateY(-10px);
      transition: all 0.2s ease;
  }

  .profile-dropdown.show {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
  }

  .profile-dropdown-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      color: var(--text-secondary);
      text-decoration: none;
      transition: all 0.2s ease;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
  }

  .profile-dropdown-item:last-child {
      border-bottom: none;
  }

  .profile-dropdown-item:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .profile-dropdown-item i {
      width: 1rem;
      text-align: center;
  }

  .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 1.25rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s ease;
      white-space: nowrap;
  }

  .btn-primary {
      background: var(--primary);
      color: var(--text-inverse);
      box-shadow: var(--shadow-sm);
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      box-shadow: var(--shadow);
      transform: translateY(-1px);
  }

  .btn-secondary {
      background: var(--bg-secondary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
  }

  .btn-secondary:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .btn-sm {
      padding: 0.5rem 0.75rem;
      font-size: 0.75rem;
  }

  /* Stats Cards */
  .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 1.5rem;
      margin-bottom: 2rem;
  }

  .stat-card {
      background: var(--bg-secondary);
      padding: 1.5rem;
      border-radius: var(--radius-lg);
      border: 1px solid var(--border);
      box-shadow: var(--shadow-sm);
      transition: all 0.2s ease;
  }

  .stat-card:hover {
      box-shadow: var(--shadow-md);
      transform: translateY(-2px);
  }

  .stat-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
  }

  .stat-title {
      font-size: 0.875rem;
      color: var(--text-secondary);
      font-weight: 500;
  }

  .stat-icon {
      width: 2.5rem;
      height: 2.5rem;
      border-radius: var(--radius);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1rem;
  }

  .stat-value {
      font-size: 2.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
  }

  .stat-change {
      font-size: 0.75rem;
      color: var(--text-muted);
  }

  /* Filters */
  .filters {
      display: flex;
      gap: 1rem;
      margin-bottom: 2rem;
      flex-wrap: wrap;
      align-items: center;
      background: var(--bg-secondary);
      padding: 1.5rem;
      border-radius: var(--radius-lg);
      border: 1px solid var(--border);
  }

  .filter-group {
      display: flex;
      gap: 0.5rem;
      align-items: center;
  }

  .filter-label {
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-secondary);
  }

  .filter-select {
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--bg-tertiary);
      color: var(--text-primary);
      font-size: 0.875rem;
  }

  .filter-buttons {
      display: flex;
      gap: 0.5rem;
      background: var(--bg-tertiary);
      padding: 0.25rem;
      border-radius: var(--radius-lg);
  }

  .filter-btn {
      padding: 0.5rem 1rem;
      border: none;
      background: transparent;
      color: var(--text-secondary);
      font-size: 0.875rem;
      font-weight: 500;
      border-radius: var(--radius);
      cursor: pointer;
      transition: all 0.2s ease;
  }

  .filter-btn:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .filter-btn.active {
      background: var(--primary);
      color: var(--text-inverse);
      box-shadow: var(--shadow-sm);
  }

  /* Tasks */
  .tasks-container {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      border: 1px solid var(--border);
      overflow: hidden;
      box-shadow: var(--shadow-sm);
  }

  .tasks-header {
      padding: 1.5rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--bg-tertiary);
  }

  .tasks-title {
      font-size: 1.125rem;
      font-weight: 600;
      color: var(--text-primary);
  }

  .task-item {
      padding: 1.5rem;
      border-bottom: 1px solid var(--border);
      transition: all 0.2s ease;
      position: relative;
      background: var(--bg-secondary);
  }

  .task-item:hover {
      background: var(--bg-hover);
  }

  .task-item:last-child {
      border-bottom: none;
  }

  .task-main {
      display: flex;
      gap: 1rem;
      align-items: flex-start;
  }

  .task-checkbox {
      width: 1.25rem;
      height: 1.25rem;
      border: 2px solid var(--border-light);
      border-radius: 0.25rem;
      cursor: pointer;
      transition: all 0.2s ease;
      flex-shrink: 0;
      margin-top: 0.125rem;
      position: relative;
      background: var(--bg-tertiary);
  }

  .task-checkbox:hover {
      border-color: var(--primary);
  }

  .task-checkbox.completed {
      background: var(--success);
      border-color: var(--success);
  }

  .task-checkbox.completed::after {
      content: '✓';
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      color: var(--text-inverse);
      font-size: 0.75rem;
      font-weight: 600;
  }

  .task-content {
      flex: 1;
  }

  .task-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 0.5rem;
  }

  .task-title {
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
  }

  .task-title.completed {
      text-decoration: line-through;
      color: var(--text-muted);
  }

  .task-description {
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-bottom: 0.75rem;
      line-height: 1.5;
  }

  .task-meta {
      display: flex;
      gap: 1rem;
      align-items: center;
      flex-wrap: wrap;
  }

  .task-category {
      display: flex;
      align-items: center;
      gap: 0.375rem;
      font-size: 0.75rem;
      font-weight: 500;
      color: var(--text-secondary);
  }

  .task-priority {
      padding: 0.125rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.625rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
  }

  .priority-high {
      background: rgba(220, 38, 38, 0.2);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  .priority-medium {
      background: rgba(217, 119, 6, 0.2);
      color: #fed7aa;
      border: 1px solid rgba(217, 119, 6, 0.3);
  }

  .priority-low {
      background: rgba(5, 150, 105, 0.2);
      color: #86efac;
      border: 1px solid rgba(5, 150, 105, 0.3);
  }

  .task-due {
      font-size: 0.75rem;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 0.25rem;
  }

  .task-due.overdue {
      color: #fca5a5;
      font-weight: 600;
  }

  .task-actions {
      display: flex;
      gap: 0.5rem;
      opacity: 0;
      transition: opacity 0.2s ease;
  }

  .task-item:hover .task-actions {
      opacity: 1;
  }

  .task-action {
      width: 2rem;
      height: 2rem;
      border: none;
      background: var(--bg-tertiary);
      color: var(--text-muted);
      border-radius: var(--radius);
      cursor: pointer;
      transition: all 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
  }

  .task-action:hover {
      background: var(--primary);
      color: var(--text-inverse);
  }

  .empty-state {
      text-align: center;
      padding: 4rem 2rem;
      color: var(--text-secondary);
  }

  .empty-icon {
      width: 4rem;
      height: 4rem;
      margin: 0 auto 1.5rem;
      background: var(--bg-tertiary);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.5rem;
      color: var(--text-muted);
  }

  .empty-title {
      font-size: 1.25rem;
      font-weight: 600;
      font-size: 1.5rem;
      color: var(--text-muted);
  }

  .empty-title {
      font-size: 1.25rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
      color: var(--text-primary);
  }

  .empty-description {
      margin-bottom: 2rem;
      max-width: 28rem;
      margin-left: auto;
      margin-right: auto;
  }

  /* Mobile Responsive */
  @media (max-width: 1024px) {
      .sidebar {
          transform: translateX(-100%);
          transition: transform 0.3s ease;
      }

      .sidebar.open {
          transform: translateX(0);
      }

      .main-content {
          margin-left: 0;
      }

      .search-input {
          width: 200px;
      }
  }

  @media (max-width: 768px) {
      .main-content {
          padding: 1rem;
      }

      .header {
          flex-direction: column;
          align-items: stretch;
      }

      .header-actions {
          justify-content: space-between;
      }

      .search-input {
          width: 100%;
      }

      .stats-grid {
          grid-template-columns: repeat(2, 1fr);
      }

      .filters {
          flex-direction: column;
          align-items: stretch;
      }

      .filter-group {
          justify-content: space-between;
      }
  }

  @media (max-width: 480px) {
      .stats-grid {
          grid-template-columns: 1fr;
      }

      .task-main {
          flex-direction: column;
          gap: 0.75rem;
      }

      .task-meta {
          flex-direction: column;
          align-items: flex-start;
          gap: 0.5rem;
      }
  }
</style>
</head>
<body>
<div class="app-container">
  <!-- Sidebar -->
  <aside class="sidebar" id="sidebar">
      <div class="logo">
          <div class="logo-icon"></div>
          <div class="logo-text">Emmanate Task Manager</div>
      </div>

      <nav class="nav-section">
          <div class="nav-title">Overview</div>
          <a href="{{ url_for('dashboard') }}" class="nav-item {% if not filter_param and not category_param %}active{% endif %}">
              <i class="fas fa-home"></i>
              <span class="nav-text">Dashboard</span>
              <span class="nav-badge">{{ stats.total }}</span>
          </a>
          <a href="{{ url_for('dashboard', filter='pending') }}" class="nav-item {% if filter_param == 'pending' %}active{% endif %}">
              <i class="fas fa-clock"></i>
              <span class="nav-text">Pending</span>
              <span class="nav-badge">{{ stats.pending }}</span>
          </a>
          <a href="{{ url_for('dashboard', filter='completed') }}" class="nav-item {% if filter_param == 'completed' %}active{% endif %}">
              <i class="fas fa-check-circle"></i>
              <span class="nav-text">Completed</span>
              <span class="nav-badge">{{ stats.completed }}</span>
          </a>
          <a href="{{ url_for('dashboard', filter='overdue') }}" class="nav-item {% if filter_param == 'overdue' %}active{% endif %}">
              <i class="fas fa-exclamation-triangle"></i>
              <span class="nav-text">Overdue</span>
              <span class="nav-badge">{{ stats.overdue }}</span>
          </a>
      </nav>

      <nav class="nav-section">
          <div class="nav-title">Categories</div>
          <div class="categories-dropdown" id="categoriesDropdown">
              <button class="categories-toggle" onclick="toggleCategories()">
                  <i class="fas fa-folder"></i>
                  <span class="nav-text">Categories</span>
                  <i class="fas fa-chevron-down dropdown-arrow"></i>
              </button>
              <div class="categories-menu">
                  {% for category in categories %}
                  <a href="{{ url_for('dashboard', category=category.id) }}" class="category-item {% if category_param == category.id|string %}active{% endif %}">
                      <div class="category-color" style="background-color: {{ category.color }}"></div>
                      <span class="category-text">{{ category.icon }} {{ category.name }}</span>
                  </a>
                  {% endfor %}
              </div>
          </div>
      </nav>

      <div class="sidebar-footer">
          <button class="sidebar-toggle" onclick="toggleSidebar()">
              <i class="fas fa-chevron-left"></i>
              <span class="nav-text">Collapse</span>
          </button>
      </div>
  </aside>

  <!-- Main Content -->
  <main class="main-content">
      <header class="header">
          <div class="header-left">
              <h1>Welcome back, {{ user.full_name.split()[0] }}!</h1>
              <p>Manage your tasks with precision and style</p>
          </div>
          <div class="header-actions">
              <div class="search-box">
                  <i class="fas fa-search search-icon"></i>
                  <input type="text" class="search-input" placeholder="Search tasks..." id="searchInput">
              </div>

              <a href="{{ url_for('add_task') }}" class="btn btn-primary">
                  <i class="fas fa-plus"></i>
                  New Task
              </a>

              <div class="user-actions">
                  <!-- Notifications -->
                  <div class="notifications-container">
                      <div class="notifications-trigger" onclick="toggleNotifications(event)">
                          <i class="fas fa-bell"></i>
                          {% if notifications %}
                          <span class="notification-badge">{{ notifications|length }}</span>
                          {% endif %}
                      </div>
                      <div class="notifications-dropdown" id="notificationsDropdown">
                          <div class="notifications-header">
                              <h3><i class="fas fa-bell"></i> Notifications</h3>
                          </div>
                          {% if notifications %}
                              {% for notification in notifications %}
                              <div class="notification-item">
                                  <div class="notification-title">{{ notification.task.title }}</div>
                                  <div class="notification-message">{{ notification.message }}</div>
                                  <span class="notification-urgency {{ notification.urgency }}">{{ notification.urgency }}</span>
                              </div>
                              {% endfor %}
                          {% else %}
                              <div class="notification-item">
                                  <div class="notification-title">No notifications</div>
                                  <div class="notification-message">You're all caught up!</div>
                              </div>
                          {% endif %}
                      </div>
                  </div>

                  <!-- Profile Menu -->
                  <div class="profile-container">
                      <div class="profile-trigger" onclick="toggleProfileMenu(event)">
                          {% if user.profile_picture %}
                          <img src="{{ url_for('uploaded_file', filename=user.profile_picture) }}" alt="Profile">
                          {% else %}
                          {{ user.full_name[0].upper() }}
                          {% endif %}
                      </div>
                      <div class="profile-dropdown" id="profileDropdown">
                          <a href="{{ url_for('profile') }}" class="profile-dropdown-item">
                              <i class="fas fa-user"></i>
                              Profile
                          </a>
                          <a href="{{ url_for('settings') }}" class="profile-dropdown-item">
                              <i class="fas fa-cog"></i>
                              Settings
                          </a>
                          <div class="profile-dropdown-item" onclick="toggleTheme()">
                              <i class="fas fa-moon" id="themeIcon"></i>
                              <span id="themeText">Dark Mode</span>
                          </div>
                          <a href="{{ url_for('logout') }}" class="profile-dropdown-item">
                              <i class="fas fa-sign-out-alt"></i>
                              Logout
                          </a>
                      </div>
                  </div>
              </div>
          </div>
      </header>

      <!-- Stats -->
      <div class="stats-grid">
          <div class="stat-card">
              <div class="stat-header">
                  <div class="stat-title">Total Tasks</div>
                  <div class="stat-icon" style="background: rgba(220, 38, 38, 0.2); color: #fca5a5;">
                      <i class="fas fa-tasks"></i>
                  </div>
              </div>
              <div class="stat-value">{{ stats.total }}</div>
              <div class="stat-change">All time</div>
          </div>

          <div class="stat-card">
              <div class="stat-header">
                  <div class="stat-title">Completed</div>
                  <div class="stat-icon" style="background: rgba(5, 150, 105, 0.2); color: #86efac;">
                      <i class="fas fa-check"></i>
                  </div>
              </div>
              <div class="stat-value">{{ stats.completed }}</div>
              <div class="stat-change">{{ stats.completion_rate }}% completion rate</div>
          </div>

          <div class="stat-card">
              <div class="stat-header">
                  <div class="stat-title">Pending</div>
                  <div class="stat-icon" style="background: rgba(251, 191, 36, 0.2); color: #fde68a;">
                      <i class="fas fa-clock"></i>
                  </div>
              </div>
              <div class="stat-value">{{ stats.pending }}</div>
              <div class="stat-change">Active tasks</div>
          </div>

          <div class="stat-card">
              <div class="stat-header">
                  <div class="stat-title">Overdue</div>
                  <div class="stat-icon" style="background: rgba(220, 38, 38, 0.2); color: #fca5a5;">
                      <i class="fas fa-exclamation-triangle"></i>
                  </div>
              </div>
              <div class="stat-value">{{ stats.overdue }}</div>
              <div class="stat-change">Need attention</div>
          </div>
      </div>

      <!-- Filters -->
      <div class="filters">
          <div class="filter-group">
              <label class="filter-label">Status:</label>
              <div class="filter-buttons">
                  <button class="filter-btn active" onclick="filterTasks('all')">All</button>
                  <button class="filter-btn" onclick="filterTasks('pending')">Pending</button>
                  <button class="filter-btn" onclick="filterTasks('completed')">Completed</button>
                  <button class="filter-btn" onclick="filterTasks('overdue')">Overdue</button>
              </div>
          </div>

          <div class="filter-group">
              <label class="filter-label">Priority:</label>
              <select class="filter-select" id="priorityFilter" onchange="filterByPriority()">
                  <option value="">All Priorities</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
              </select>
          </div>

          <div class="filter-group">
              <label class="filter-label">Category:</label>
              <select class="filter-select" id="categoryFilter" onchange="filterByCategory()">
                  <option value="">All Categories</option>
                  {% for category in categories %}
                  <option value="{{ category.id }}">{{ category.name }}</option>
                  {% endfor %}
              </select>
          </div>

          <div class="filter-group">
              <label class="filter-label">Sort:</label>
              <select class="filter-select" id="sortSelect" onchange="sortTasks()">
                  <option value="created_desc">Newest First</option>
                  <option value="created_asc">Oldest First</option>
                  <option value="due_asc">Due Date</option>
                  <option value="priority_desc">Priority</option>
                  <option value="title_asc">Title A-Z</option>
              </select>
          </div>
      </div>

      <!-- Tasks -->
      <div class="tasks-container">
          <div class="tasks-header">
              <div class="tasks-title">Your Tasks</div>
              <div class="header-actions">
                  <button class="btn btn-secondary btn-sm" onclick="selectAllTasks()">
                      <i class="fas fa-check-square"></i>
                      Select All
                  </button>
              </div>
          </div>

          {% if tasks %}
              {% for task in tasks %}
              <div class="task-item" data-task-id="{{ task.id }}" 
                   data-status="{{ 'completed' if task.completed else 'pending' }}"
                   data-priority="{{ task.priority }}"
                   data-category="{{ task.get('category_id', '') }}"
                   data-due="{{ task.get('due_date', '') }}">
                  <div class="task-main">
                      <div class="task-checkbox {{ 'completed' if task.completed }}" 
                           onclick="toggleTaskStatus({{ task.id }})"></div>
                      <div class="task-content">
                          <div class="task-header">
                              <div>
                                  <div class="task-title {{ 'completed' if task.completed }}">
                                      {{ task.title }}
                                  </div>
                                  {% if task.get('description') %}
                                  <div class="task-description">{{ task.description }}</div>
                                  {% endif %}
                              </div>
                              <div class="task-actions">
                                  <a href="{{ url_for('edit_task', task_id=task.id) }}" class="task-action" title="Edit">
                                      <i class="fas fa-edit"></i>
                                  </a>
                                  <a href="{{ url_for('duplicate_task', task_id=task.id) }}" class="task-action" title="Duplicate">
                                      <i class="fas fa-copy"></i>
                                  </a>
                                  <a href="{{ url_for('delete_task', task_id=task.id) }}" class="task-action" title="Delete" onclick="return confirm('Are you sure you want to delete this task?');">
                                      <i class="fas fa-trash"></i>
                                  </a>
                              </div>
                          </div>
                          <div class="task-meta">
                              {% if task.get('category_name') %}
                              <div class="task-category">
                                  <div class="category-color" style="background-color: {{ task.category_color }}"></div>
                                  {{ task.category_icon }} {{ task.category_name }}
                              </div>
                              {% endif %}
                              <div class="task-priority priority-{{ task.get('priority', 'medium') }}">
                                  {{ task.get('priority', 'medium') }}
                              </div>
                              {% if task.get('due_date') %}
                              <div class="task-due">
                                  <i class="fas fa-calendar"></i>
                                  {{ task.due_date.strftime('%Y-%m-%d %H:%M') if task.due_date else '' }}
                              </div>
                              {% endif %}
                          </div>
                      </div>
                  </div>
              </div>
              {% endfor %}
          {% else %}
              <div class="empty-state">
                  <div class="empty-icon">
                      <i class="fas fa-tasks"></i>
                  </div>
                  <div class="empty-title">No tasks yet</div>
                  <div class="empty-description">
                      Create your first task to get started with Emmanate Task Manager. 
                      Stay organized and boost your productivity!
                  </div>
                  <a href="{{ url_for('add_task') }}" class="btn btn-primary">
                      <i class="fas fa-plus"></i>
                      Create Your First Task
                  </a>
              </div>
          {% endif %}
      </div>
  </main>
</div>

<script>
// Theme management
function toggleTheme() {
  const html = document.documentElement;
  const currentTheme = html.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
  
  html.setAttribute('data-theme', newTheme);
  document.cookie = `theme=${newTheme};path=/;max-age=31536000;samesite=lax`;
  
  // Update theme icon and text
  const themeIcon = document.getElementById('themeIcon');
  const themeText = document.getElementById('themeText');
  
  if (newTheme === 'light') {
      themeIcon.className = 'fas fa-sun';
      themeText.textContent = 'Light Mode';
  } else {
      themeIcon.className = 'fas fa-moon';
      themeText.textContent = 'Dark Mode';
  }
  
  // Save theme preference to DB
  fetch('/api/update-theme', {
      method: 'POST',
      headers: {
          'Content-Type': 'application/json',
      },
      body: JSON.stringify({ theme: newTheme })
  });
}

// Initialize theme
document.addEventListener('DOMContentLoaded', function() {
  const currentTheme = document.documentElement.getAttribute('data-theme');
  const themeIcon = document.getElementById('themeIcon');
  const themeText = document.getElementById('themeText');
  
  if (currentTheme === 'light') {
      if(themeIcon) themeIcon.className = 'fas fa-sun';
      if(themeText) themeText.textContent = 'Light Mode';
  } else {
      if(themeIcon) themeIcon.className = 'fas fa-moon';
      if(themeText) themeText.textContent = 'Dark Mode';
  }
});

function toggleCategories() {
  const categoriesDropdown = document.getElementById('categoriesDropdown');
  categoriesDropdown.classList.toggle('open');
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  sidebar.classList.toggle('collapsed');
}

function toggleNotifications(event) {
  event.stopPropagation(); // Prevent document click from immediately closing it
  const notificationsDropdown = document.getElementById('notificationsDropdown');
  notificationsDropdown.classList.toggle('show');
}

function toggleProfileMenu(event) {
  event.stopPropagation(); // Prevent document click from immediately closing it
  const profileDropdown = document.getElementById('profileDropdown');
  profileDropdown.classList.toggle('show');
}

// Close dropdowns on document click
document.addEventListener('click', function(event) {
  const notificationsDropdown = document.getElementById('notificationsDropdown');
  if (notificationsDropdown && notificationsDropdown.classList.contains('show')) {
      notificationsDropdown.classList.remove('show');
  }

  const profileDropdown = document.getElementById('profileDropdown');
  if (profileDropdown && profileDropdown.classList.contains('show')) {
      profileDropdown.classList.remove('show');
  }
});

function filterTasks(status) {
  const taskItems = document.querySelectorAll('.task-item');
  taskItems.forEach(taskItem => {
      const taskStatus = taskItem.dataset.status;
      if (status === 'all' || taskStatus === status) {
          taskItem.style.display = '';
      } else {
          taskItem.style.display = 'none';
      }
  });

  // Update active filter button
  document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.classList.remove('active');
  });
  document.querySelector(`.filter-btn[onclick="filterTasks('${status}')"]`).classList.add('active');
}

function filterByPriority() {
  const priority = document.getElementById('priorityFilter').value;
  const taskItems = document.querySelectorAll('.task-item');

  taskItems.forEach(taskItem => {
      const taskPriority = taskItem.dataset.priority;
      if (!priority || taskPriority === priority) {
          taskItem.style.display = '';
      } else {
          taskItem.style.display = 'none';
      }
  });
}

function filterByCategory() {
  const category = document.getElementById('categoryFilter').value;
  const taskItems = document.querySelectorAll('.task-item');

  taskItems.forEach(taskItem => {
      const taskCategory = taskItem.dataset.category;
      if (!category || taskCategory === category) {
          taskItem.style.display = '';
      } else {
          taskItem.style.display = 'none';
      }
  });
}

function sortTasks() {
  const sortValue = document.getElementById('sortSelect').value;
  const tasksContainer = document.querySelector('.tasks-container');
  const taskItems = Array.from(document.querySelectorAll('.task-item'));

  taskItems.sort((a, b) => {
      let valueA, valueB;

      switch (sortValue) {
          case 'created_asc':
              valueA = new Date(a.dataset.created);
              valueB = new Date(b.dataset.created);
              break;
          case 'created_desc':
              valueA = new Date(b.dataset.created);
              valueB = new Date(a.dataset.created);
              break;
          case 'due_asc':
              valueA = new Date(a.dataset.due);
              valueB = new Date(b.dataset.due);
              break;
          case 'priority_desc':
              const priorityOrder = { 'high': 1, 'medium': 2, 'low': 3 };
              valueA = priorityOrder[a.dataset.priority] || 4;
              valueB = priorityOrder[b.dataset.priority] || 4;
              break;
          case 'title_asc':
              valueA = a.querySelector('.task-title').textContent.toLowerCase();
              valueB = b.querySelector('.task-title').textContent.toLowerCase();
              break;
          default:
              return 0;
      }

      if (valueA < valueB) return -1;
      if (valueA > valueB) return 1;
      return 0;
  });

  // Re-append sorted items to the container
  taskItems.forEach(item => tasksContainer.appendChild(item));
}

function selectAllTasks() {
  // Implement select all tasks functionality here
  alert('Select All Tasks functionality is not yet implemented.');
}

function toggleTaskStatus(taskId) {
  window.location.href = `/toggle/${taskId}`;
}

// Search functionality
document.getElementById('searchInput').addEventListener('input', function() {
  const searchTerm = this.value.toLowerCase();
  const taskItems = document.querySelectorAll('.task-item');

  taskItems.forEach(taskItem => {
      const taskTitle = taskItem.querySelector('.task-title').textContent.toLowerCase();
      const taskDescription = taskItem.querySelector('.task-description')?.textContent.toLowerCase() || '';

      if (taskTitle.includes(searchTerm) || taskDescription.includes(searchTerm)) {
          taskItem.style.display = '';
      } else {
          taskItem.style.display = 'none';
      }
  });
});
</script>
</body>
</html>
"""
EDIT_TASK_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ user.get('theme', 'dark') }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Edit Task - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      /* Professional Dark Theme */
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --secondary: #d97706;
      --accent: #f59e0b;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;

      /* Dark Theme Colors */
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;

      /* Text Colors */
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  :root[data-theme="light"] {
      /* Professional Light Theme */
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --secondary: #d97706;
      --accent: #f59e0b;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;

      /* Light Theme Colors */
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-hover: #e2e8f0;

      /* Text Colors */
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  :root {
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
      --radius-xl: 1rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
      font-feature-settings: 'cv02', 'cv03', 'cv04', 'cv11';
      transition: all 0.3s ease;
      padding: 2rem;
  }

  .container {
      max-width: 800px;
      margin: 0 auto;
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      border: 1px solid var(--border);
      box-shadow: var(--shadow-sm);
      padding: 2rem;
  }

  h1 {
      font-size: 2rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 1rem;
  }

  .form-group {
      margin-bottom: 1.5rem;
  }

  .form-label {
      display: block;
      font-weight: 600;
      color: var(--text-primary);
      font-size: 0.875rem;
      margin-bottom: 0.5rem;
  }

  .form-input,
  .form-textarea,
  .form-select {
      width: 100%;
      padding: 0.75rem 1rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      transition: all 0.2s ease;
  }

  .form-input:focus,
  .form-textarea:focus,
  .form-select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .form-input::placeholder,
  .form-textarea::placeholder {
      color: var(--text-muted);
  }

  .form-textarea {
      resize: vertical;
      min-height: 120px;
  }

  .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 1.25rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s ease;
      white-space: nowrap;
  }

  .btn-primary {
      background: var(--primary);
      color: var(--text-inverse);
      box-shadow: var(--shadow-sm);
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      box-shadow: var(--shadow);
      transform: translateY(-1px);
  }

  .btn-secondary {
      background: var(--bg-secondary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
  }

  .btn-secondary:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .alert {
      padding: 1rem;
      border-radius: var(--radius);
      margin-bottom: 1.5rem;
      font-size: 0.875rem;
  }

  .alert-error {
      background: rgba(220, 38, 38, 0.1);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  .back-link {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      margin-bottom: 2rem;
      transition: color 0.2s ease;
  }

  .back-link:hover {
      color: var(--text-primary);
  }
</style>
</head>
<body>
<div class="container">
  <a href="{{ url_for('dashboard') }}" class="back-link">
      <i class="fas fa-arrow-left"></i>
      Back to Dashboard
  </a>

  <h1>Edit Task</h1>

  {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
          {% for category, message in messages %}
              <div class="alert alert-{{ category }}">{{ message }}</div>
          {% endfor %}
      {% endif %}
  {% endwith %}

  <form method="POST">
      <div class="form-group">
          <label class="form-label" for="title">Title</label>
          <input type="text" id="title" name="title" class="form-input" value="{{ task.title }}" required>
      </div>

      <div class="form-group">
          <label class="form-label" for="description">Description</label>
          <textarea id="description" name="description" class="form-textarea">{{ task.description or '' }}</textarea>
      </div>

      <div class="form-group">
          <label class="form-label" for="category">Category</label>
          <select id="category" name="category" class="form-select">
              <option value="">No Category</option>
              {% for category in categories %}
                  <option value="{{ category.id }}" {% if task.category_id == category.id %}selected{% endif %}>
                      {{ category.name }}
                  </option>
              {% endfor %}
          </select>
      </div>

      <div class="form-group">
          <label class="form-label" for="priority">Priority</label>
          <select id="priority" name="priority" class="form-select">
              <option value="low" {% if task.priority == 'low' %}selected{% endif %}>Low</option>
              <option value="medium" {% if task.priority == 'medium' %}selected{% endif %}>Medium</option>
              <option value="high" {% if task.priority == 'high' %}selected{% endif %}>High</option>
          </select>
      </div>

      <div class="form-group">
          <label class="form-label" for="due_date">Due Date</label>
          <input type="datetime-local" id="due_date" name="due_date" class="form-input" value="{{ task.due_date.strftime('%Y-%m-%dT%H:%M') if task.due_date else '' }}">
      </div>

      <button type="submit" class="btn btn-primary">
          <i class="fas fa-save"></i>
          Update Task
      </button>
      <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">
          Cancel
      </a>
  </form>
</div>
</body>
</html>
"""
ADD_TASK_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ user.get('theme', 'dark') }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Add Task - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  :root[data-theme="light"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-hover: #e2e8f0;
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
      min-height: 100vh;
      transition: all 0.3s ease;
  }

  .container {
      max-width: 800px;
      margin: 0 auto;
      padding: 2rem 1rem;
  }

  .header {
      text-align: center;
      margin-bottom: 3rem;
  }

  .header-logo {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 1rem;
  }

  .logo-icon {
      width: 3rem;
      height: 3rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
  }

  .header h1 {
      font-size: 2.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.5rem;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
  }

  .back-link {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      font-weight: 500;
      transition: color 0.2s ease;
      margin-top: 1rem;
  }

  .back-link:hover {
      color: var(--primary);
  }

  .form-container {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 2.5rem;
      box-shadow: var(--shadow-lg);
      border: 1px solid var(--border);
  }

  .form-grid {
      display: grid;
      gap: 2rem;
  }

  .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
  }

  .form-label {
      font-weight: 600;
      color: var(--text-primary);
      font-size: 0.875rem;
  }

  .form-input,
  .form-textarea,
  .form-select {
      padding: 0.75rem 1rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      transition: all 0.2s ease;
      background: var(--bg-tertiary);
      color: var(--text-primary);
  }

  .form-input:focus,
  .form-textarea:focus,
  .form-select:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .form-input::placeholder,
  .form-textarea::placeholder {
      color: var(--text-muted);
  }

  .form-textarea {
      resize: vertical;
      min-height: 120px;
  }

  .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2rem;
  }

  .priority-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0.75rem;
  }

  .priority-option {
      display: none;
  }

  .priority-label {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      padding: 1rem;
      border: 2px solid var(--border);
      border-radius: var(--radius-lg);
      cursor: pointer;
      transition: all 0.2s ease;
      font-weight: 500;
      background: var(--bg-tertiary);
      color: var(--text-secondary);
  }

  .priority-option:checked + .priority-label {
      border-color: var(--primary);
      background: rgba(220, 38, 38, 0.1);
      color: var(--primary);
  }

  .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 2rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s ease;
  }

  .btn-primary {
      background: var(--primary);
      color: white;
      box-shadow: var(--shadow-lg);
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  .btn-secondary {
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
  }

  .btn-secondary:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .form-actions {
      display: flex;
      gap: 1rem;
      justify-content: center;
      margin-top: 2rem;
      flex-wrap: wrap;
  }

  .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1rem;
      font-size: 0.875rem;
  }

  .alert-error {
      background: rgba(220, 38, 38, 0.1);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  @media (max-width: 768px) {
      .container {
          padding: 1rem;
      }

      .form-container {
          padding: 1.5rem;
      }

      .form-row {
          grid-template-columns: 1fr;
      }

      .priority-grid {
          grid-template-columns: 1fr;
      }

      .form-actions {
          flex-direction: column;
      }
  }
</style>
</head>
<body>
<div class="container">
  <header class="header">
      <div class="header-logo">
          <div class="logo-icon"></div>
      </div>
      <h1>Add New Task</h1>
      <a href="{{ url_for('dashboard') }}" class="back-link">
          <i class="fas fa-arrow-left"></i>
          Back to Dashboard
      </a>
  </header>

  <div class="form-container">
      {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
              {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
              {% endfor %}
          {% endif %}
      {% endwith %}

      <form method="POST" class="form-grid">
          <div class="form-group">
              <label class="form-label" for="title">Task Title *</label>
              <input type="text" id="title" name="title" class="form-input" 
                     placeholder="Enter task title" required>
          </div>

          <div class="form-group">
              <label class="form-label" for="description">Description</label>
              <textarea id="description" name="description" class="form-textarea" 
                        placeholder="Enter task description (optional)"></textarea>
          </div>

          <div class="form-row">
              <div class="form-group">
                  <label class="form-label" for="category">Category</label>
                  <select id="category" name="category" class="form-select">
                      <option value="">Select a category</option>
                      {% for category in categories %}
                      <option value="{{ category.id }}">{{ category.icon }} {{ category.name }}</option>
                      {% endfor %}
                  </select>
              </div>

              <div class="form-group">
                  <label class="form-label" for="due_date">Due Date</label>
                  <input type="datetime-local" id="due_date" name="due_date" class="form-input">
              </div>
          </div>

          <div class="form-group">
              <label class="form-label">Priority</label>
              <div class="priority-grid">
                  <div>
                      <input type="radio" id="priority_high" name="priority" value="high" class="priority-option">
                      <label for="priority_high" class="priority-label">
                          <i class="fas fa-exclamation-triangle"></i>
                          High
                      </label>
                  </div>
                  <div>
                      <input type="radio" id="priority_medium" name="priority" value="medium" class="priority-option" checked>
                      <label for="priority_medium" class="priority-label">
                          <i class="fas fa-minus-circle"></i>
                          Medium
                      </label>
                  </div>
                  <div>
                      <input type="radio" id="priority_low" name="priority" value="low" class="priority-option">
                      <label for="priority_low" class="priority-label">
                          <i class="fas fa-arrow-down"></i>
                          Low
                      </label>
                  </div>
              </div>
          </div>

          <div class="form-actions">
              <button type="submit" class="btn btn-primary">
                  <i class="fas fa-plus"></i>
                  Create Task
              </button>
              <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">
                  <i class="fas fa-times"></i>
                  Cancel
              </a>
          </div>
      </form>
  </div>
</div>
</body>
</html>
"""
PROFILE_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ user.get('theme', 'dark') }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Profile - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  :root[data-theme="light"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-hover: #e2e8f0;
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
      min-height: 100vh;
      transition: all 0.3s ease;
  }

  .container {
      max-width: 800px;
      margin: 0 auto;
      padding: 2rem 1rem;
  }

  .header {
      text-align: center;
      margin-bottom: 3rem;
  }

  .header-logo {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 1rem;
  }

  .logo-icon {
      width: 3rem;
      height: 3rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
  }

  .header h1 {
      font-size: 2.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.5rem;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
  }

  .back-link {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      font-weight: 500;
      transition: color 0.2s ease;
      margin-top: 1rem;
  }

  .back-link:hover {
      color: var(--primary);
  }

  .profile-container {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 2.5rem;
      box-shadow: var(--shadow-lg);
      border: 1px solid var(--border);
  }

  .profile-avatar-section {
      text-align: center;
      margin-bottom: 2rem;
      padding-bottom: 2rem;
      border-bottom: 1px solid var(--border);
  }

  .profile-avatar-large {
      width: 8rem;
      height: 8rem;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-weight: 700;
      font-size: 2rem;
      margin: 0 auto 1rem;
      overflow: hidden;
      position: relative;
  }

  .profile-avatar-large img {
      width: 100%;
      height: 100%;
      object-fit: cover;
  }

  .form-grid {
      display: grid;
      gap: 2rem;
  }

  .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
  }

  .form-label {
      font-weight: 600;
      color: var(--text-primary);
      font-size: 0.875rem;
  }

  .form-input {
      padding: 0.75rem 1rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      transition: all 0.2s ease;
      background: var(--bg-tertiary);
      color: var(--text-primary);
  }

  .form-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.1);
  }

  .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2rem;
  }

  .form-actions {
      display: flex;
      gap: 1rem;
      justify-content: center;
      margin-top: 2rem;
      flex-wrap: wrap;
  }

  .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 2rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s ease;
  }

  .btn-primary {
      background: var(--primary);
      color: white;
      box-shadow: var(--shadow-lg);
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  .btn-secondary {
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
  }

  .btn-secondary:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1rem;
      font-size: 0.875rem;
  }

  .alert-success {
      background: rgba(5, 150, 105, 0.1);
      color: #86efac;
      border: 1px solid rgba(5, 150, 105, 0.3);
  }

  .alert-error {
      background: rgba(220, 38, 38, 0.1);
      color: #fca5a5;
      border: 1px solid rgba(220, 38, 38, 0.3);
  }

  @media (max-width: 768px) {
      .container {
          padding: 1rem;
      }

      .profile-container {
          padding: 1.5rem;
      }

      .form-row {
          grid-template-columns: 1fr;
      }

      .form-actions {
          flex-direction: column;
      }
  }
</style>
</head>
<body>
<div class="container">
  <header class="header">
      <div class="header-logo">
          <div class="logo-icon"></div>
      </div>
      <h1>Profile Settings</h1>
      <a href="{{ url_for('dashboard') }}" class="back-link">
          <i class="fas fa-arrow-left"></i>
          Back to Dashboard
      </a>
  </header>

  <div class="profile-container">
      {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
              {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
              {% endfor %}
          {% endif %}
      {% endwith %}

      <div class="profile-avatar-section">
          <div class="profile-avatar-large">
              {% if user.profile_picture %}
              <img src="{{ url_for('uploaded_file', filename=user.profile_picture) }}" alt="Profile Picture">
              {% else %}
              {{ user.full_name[0].upper() }}
              {% endif %}
          </div>
          <h2>{{ user.full_name }}</h2>
          <p style="color: var(--text-secondary);">{{ user.email }}</p>
      </div>

      <form method="POST" enctype="multipart/form-data" class="form-grid">
          <div class="form-group">
              <label class="form-label" for="profile_picture">Profile Picture</label>
              <input type="file" id="profile_picture" name="profile_picture" class="form-input" accept="image/*">
          </div>

          <div class="form-row">
              <div class="form-group">
                  <label class="form-label" for="full_name">Full Name</label>
                  <input type="text" id="full_name" name="full_name" class="form-input" 
                         value="{{ user.full_name }}" required>
              </div>

              <div class="form-group">
                  <label class="form-label" for="email">Email Address</label>
                  <input type="email" id="email" name="email" class="form-input" 
                         value="{{ user.email }}" required>
              </div>
          </div>

          <div class="form-row">
              <div class="form-group">
                  <label class="form-label" for="location">Location</label>
                  <input type="text" id="location" name="location" class="form-input" 
                         value="{{ user.get('location', '') }}" placeholder="City, Country">
              </div>

              <div class="form-group">
                  <label class="form-label" for="website">Website</label>
                  <input type="url" id="website" name="website" class="form-input" 
                         value="{{ user.get('website', '') }}" placeholder="https://example.com">
              </div>
          </div>

          <div class="form-group">
              <label class="form-label" for="bio">Bio</label>
              <textarea id="bio" name="bio" class="form-input" rows="4" 
                        placeholder="Tell us about yourself">{{ user.get('bio', '') }}</textarea>
          </div>

          <div class="form-actions">
              <button type="submit" class="btn btn-primary">
                  <i class="fas fa-save"></i>
                  Save Changes
              </button>
              <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">
                  <i class="fas fa-times"></i>
                  Cancel
              </a>
          </div>
      </form>
  </div>
</div>
</body>
</html>
"""
SETTINGS_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="{{ user.get('theme', 'dark') }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Settings - Emmanate Task Manager</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  :root[data-theme="dark"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #0f0f0f;
      --bg-secondary: #1a1a1a;
      --bg-tertiary: #262626;
      --bg-hover: #2d2d2d;
      --text-primary: #ffffff;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --border: #374151;
      --gold: #fbbf24;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  :root[data-theme="light"] {
      --primary: #dc2626;
      --primary-hover: #b91c1c;
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-hover: #e2e8f0;
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --gold: #f59e0b;
      --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
      --radius: 0.5rem;
      --radius-lg: 0.75rem;
  }

  * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
  }

  body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
      min-height: 100vh;
      transition: all 0.3s ease;
  }

  .container {
      max-width: 800px;
      margin: 0 auto;
      padding: 2rem 1rem;
  }

  .header {
      text-align: center;
      margin-bottom: 3rem;
  }

  .header-logo {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 1rem;
  }

  .logo-icon {
      width: 3rem;
      height: 3rem;
      background: url('{{ url_for("static", filename="logo-icon.png") }}') center/contain no-repeat;
      border-radius: var(--radius-lg);
  }

  .header h1 {
      font-size: 2.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.5rem;
      background: linear-gradient(135deg, var(--primary), var(--gold));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
  }

  .back-link {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      color: var(--text-secondary);
      text-decoration: none;
      font-weight: 500;
      transition: color 0.2s ease;
      margin-top: 1rem;
  }

  .back-link:hover {
      color: var(--primary);
  }

  .settings-container {
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      padding: 2.5rem;
      box-shadow: var(--shadow-lg);
      border: 1px solid var(--border);
  }

  .settings-section {
      margin-bottom: 2.5rem;
      padding-bottom: 2rem;
      border-bottom: 1px solid var(--border);
  }

  .settings-section:last-child {
      margin-bottom: 0;
      padding-bottom: 0;
      border-bottom: none;
  }

  .section-title {
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 0.75rem;
  }

  .section-description {
      color: var(--text-secondary);
      font-size: 0.875rem;
      margin-bottom: 1.5rem;
  }

  .setting-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1rem 0;
      border-bottom: 1px solid var(--border);
  }

  .setting-item:last-child {
      border-bottom: none;
  }

  .setting-info {
      flex: 1;
  }

  .setting-label {
      font-weight: 500;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
  }

  .setting-description {
      font-size: 0.875rem;
      color: var(--text-secondary);
  }

  .theme-options {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1rem;
      margin-top: 1rem;
  }

  .theme-option {
      display: none;
  }

  .theme-label {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 1rem;
      border: 2px solid var(--border);
      border-radius: var(--radius-lg);
      cursor: pointer;
      transition: all 0.2s ease;
      background: var(--bg-tertiary);
  }

  .theme-option:checked + .theme-label {
      border-color: var(--primary);
      background: rgba(220, 38, 38, 0.1);
  }

  .theme-preview {
      width: 2rem;
      height: 2rem;
      border-radius: var(--radius);
      display: flex;
      overflow: hidden;
  }

  .theme-preview-dark {
      background: linear-gradient(45deg, #0f0f0f 50%, #1a1a1a 50%);
  }

  .theme-preview-light {
      background: linear-gradient(45deg, #ffffff 50%, #f8fafc 50%);
      border: 1px solid var(--border);
  }

  .toggle-switch {
      position: relative;
      width: 3rem;
      height: 1.5rem;
      background: var(--bg-tertiary);
      border-radius: 9999px;
      cursor: pointer;
      transition: all 0.2s ease;
      border: 1px solid var(--border);
  }

  .toggle-switch.active {
      background: var(--primary);
  }

  .toggle-switch::after {
      content: '';
      position: absolute;
      top: 0.125rem;
      left: 0.125rem;
      width: 1.25rem;
      height: 1.25rem;
      background: var(--text-primary);
      border-radius: 50%;
      transition: all 0.2s ease;
  }

  .toggle-switch.active::after {
      transform: translateX(1.5rem);
      background: white;
  }

  .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 2rem;
      border: none;
      border-radius: var(--radius);
      font-size: 0.875rem;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s ease;
  }

  .btn-primary {
      background: var(--primary);
      color: white;
      box-shadow: var(--shadow-lg);
  }

  .btn-primary:hover {
      background: var(--primary-hover);
      transform: translateY(-1px);
  }

  .btn-secondary {
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      border: 1px solid var(--border);
  }

  .btn-secondary:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
  }

  .form-actions {
      display: flex;
      gap: 1rem;
      justify-content: center;
      margin-top: 2rem;
      flex-wrap: wrap;
  }

  .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1rem;
      font-size: 0.875rem;
  }

  .alert-success {
      background: rgba(5, 150, 105, 0.1);
      color: #86efac;
      border: 1px solid rgba(5, 150, 105, 0.3);
  }

  @media (max-width: 768px) {
      .container {
          padding: 1rem;
      }

      .settings-container {
          padding: 1.5rem;
      }

      .setting-item {
          flex-direction: column;
          align-items: flex-start;
          gap: 1rem;
      }

      .theme-options {
          grid-template-columns: 1fr;
      }

      .form-actions {
          flex-direction: column;
      }
  }
</style>
</head>
<body>
<div class="container">
  <header class="header">
      <div class="header-logo">
          <div class="logo-icon"></div>
      </div>
      <h1>Settings</h1>
      <a href="{{ url_for('dashboard') }}" class="back-link">
          <i class="fas fa-arrow-left"></i>
          Back to Dashboard
      </a>
  </header>

  <div class="settings-container">
      {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
              {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
              {% endfor %}
          {% endif %}
      {% endwith %}

      <form method="POST">
          <!-- Appearance Settings -->
          <div class="settings-section">
              <h2 class="section-title">
                  <i class="fas fa-palette"></i>
                  Appearance
              </h2>
              <p class="section-description">
                  Customize the look and feel of your task manager
              </p>

              <div class="setting-item">
                  <div class="setting-info">
                      <div class="setting-label">Theme</div>
                      <div class="setting-description">Choose between light and dark mode</div>
                  </div>
              </div>

              <div class="theme-options">
                  <div>
                      <input type="radio" id="theme_dark" name="theme" value="dark" class="theme-option" 
                             {{ 'checked' if user.get('theme', 'dark') == 'dark' }}>
                      <label for="theme_dark" class="theme-label">
                          <div class="theme-preview theme-preview-dark"></div>
                          <div>
                              <div class="setting-label">Dark Mode</div>
                              <div class="setting-description">Easy on the eyes</div>
                          </div>
                      </label>
                  </div>
                  <div>
                      <input type="radio" id="theme_light" name="theme" value="light" class="theme-option" 
                             {{ 'checked' if user.get('theme') == 'light' }}>
                      <label for="theme_light" class="theme-label">
                          <div class="theme-preview theme-preview-light"></div>
                          <div>
                              <div class="setting-label">Light Mode</div>
                              <div class="setting-description">Clean and bright</div>
                          </div>
                      </label>
                  </div>
              </div>
          </div>

          <!-- Notification Settings -->
          <div class="settings-section">
              <h2 class="section-title">
                  <i class="fas fa-bell"></i>
                  Notifications
              </h2>
              <p class="section-description">
                  Manage how you receive notifications about your tasks
              </p>

              <div class="setting-item">
                  <div class="setting-info">
                      <div class="setting-label">Task Reminders</div>
                      <div class="setting-description">Get notified about upcoming due dates</div>
                  </div>
                  <div class="toggle-switch {{ 'active' if user.get('notifications_enabled', True) }}" 
                       onclick="toggleNotifications(this)">
                      <input type="hidden" name="notifications_enabled" 
                             value="{{ 'true' if user.get('notifications_enabled', True) else 'false' }}">
                  </div>
              </div>
          </div>

          <!-- Account Settings -->
          <div class="settings-section">
              <h2 class="section-title">
                  <i class="fas fa-user-cog"></i>
                  Account
              </h2>
              <p class="section-description">
                  Manage your account preferences and security
              </p>

              <div class="setting-item">
                  <div class="setting-info">
                      <div class="setting-label">Profile Information</div>
                      <div class="setting-description">Update your name, email, and profile picture</div>
                  </div>
                  <a href="{{ url_for('profile') }}" class="btn btn-secondary">
                      <i class="fas fa-edit"></i>
                      Edit Profile
                  </a>
              </div>
          </div>

          <div class="form-actions">
              <button type="submit" class="btn btn-primary">
                  <i class="fas fa-save"></i>
                  Save Settings
              </button>
              <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">
                  <i class="fas fa-times"></i>
                  Cancel
              </a>
          </div>
      </form>
  </div>
</div>

<script>
  function toggleNotifications(element) {
      element.classList.toggle('active');
      const input = element.querySelector('input[type="hidden"]');
      const isActive = element.classList.contains('active');
      input.value = isActive ? 'true' : 'false';
  }

  // Apply theme immediately when changed
  document.addEventListener('change', function(e) {
      if (e.target.name === 'theme') {
          document.documentElement.setAttribute('data-theme', e.target.value);
      }
  });
</script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
@login_required
def index():
  return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
  if 'user_id' in session:
      return redirect(url_for('dashboard'))
  
  theme = request.cookies.get('theme', 'dark')
      
  if request.method == 'POST':
      email = request.form['email'].lower()
      password = request.form['password']
      
      # Auto-create test user if it doesn't exist
      if email == 'test@example.com' and not get_user_by_email(email):
          conn = get_db_connection()
          try:
              with conn.cursor() as cur:
                  hashed_pw = hash_password('password123')
                  cur.execute("INSERT INTO users (full_name, email, password) VALUES (%s, %s, %s) RETURNING id", 
                              ('Test User', 'test@example.com', hashed_pw))
                  user_id = cur.fetchone()[0]
                  create_default_categories(cur, user_id)
              conn.commit()
          finally:
              if conn:
                  conn.close()

      user = get_user_by_email(email)
      if user and user['password'] == hash_password(password):
          session['user_id'] = str(user['id'])
          return redirect(url_for('dashboard'))
      else:
          flash('Invalid email or password.', 'error')
  return render_template_string(LOGIN_TEMPLATE, theme=theme)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
  if 'user_id' in session:
      return redirect(url_for('dashboard'))

  theme = request.cookies.get('theme', 'dark')

  if request.method == 'POST':
      full_name = request.form['full_name']
      email = request.form['email'].lower()
      password = request.form['password']
      confirm_password = request.form['confirm_password']

      if password != confirm_password:
          flash('Passwords do not match.', 'error')
      elif len(password) < 6:
          flash('Password must be at least 6 characters long.', 'error')
      elif get_user_by_email(email):
          flash('An account with this email already exists.', 'error')
      else:
          conn = get_db_connection()
          try:
              with conn.cursor() as cur:
                  hashed_pw = hash_password(password)
                  cur.execute("INSERT INTO users (full_name, email, password) VALUES (%s, %s, %s) RETURNING id", 
                              (full_name, email, hashed_pw))
                  user_id = cur.fetchone()[0]
                  create_default_categories(cur, user_id)
              conn.commit()
          finally:
              if conn:
                  conn.close()
          flash('Account created successfully! Please log in.', 'success')
          return redirect(url_for('login'))
  return render_template_string(SIGNUP_TEMPLATE, theme=theme)

@app.route('/logout')
def logout():
  session.pop('user_id', None)
  flash('You have been logged out.', 'success')
  return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
  user = get_user_by_id(session['user_id'])
  if not user:
      return redirect(url_for('logout'))
  
  filter_param = request.args.get('filter')
  category_param = request.args.get('category')

  tasks = get_user_tasks(session['user_id'], filter_param, category_param)
  categories = get_user_categories(session['user_id'])
  stats = calculate_stats(session['user_id'])
  notifications = get_due_notifications(session['user_id'])
  
  return render_template_string(MAIN_TEMPLATE, user=user, tasks=tasks, categories=categories, stats=stats, notifications=notifications, filter_param=filter_param, category_param=category_param)

@app.route('/add-task', methods=['GET', 'POST'])
@login_required
def add_task():
  user = get_user_by_id(session['user_id'])
  categories = get_user_categories(session['user_id'])
  if request.method == 'POST':
      title = request.form.get('title', '').strip()
      if not title:
          flash('Task title is required', 'error')
      else:
          conn = get_db_connection()
          with conn.cursor() as cur:
              due_date_str = request.form.get('due_date')
              due_date = datetime.fromisoformat(due_date_str) if due_date_str else None
              category_id = request.form.get('category')
              cur.execute("""
                  INSERT INTO tasks (user_id, title, description, category_id, priority, due_date)
                  VALUES (%s, %s, %s, %s, %s, %s)
              """, (session['user_id'], title, request.form.get('description'), category_id if category_id else None, request.form.get('priority'), due_date))
          conn.commit()
          conn.close()
          flash('Task added successfully!', 'success')
          return redirect(url_for('dashboard'))
  return render_template_string(ADD_TASK_TEMPLATE, user=user, categories=categories)

@app.route('/edit/<int:task_id>', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
  user = get_user_by_id(session['user_id'])
  categories = get_user_categories(session['user_id'])
  conn = get_db_connection()
  with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
      cur.execute("SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, session['user_id']))
      task = cur.fetchone()
  conn.close()
  if not task:
      flash('Task not found.', 'error')
      return redirect(url_for('dashboard'))

  if request.method == 'POST':
      title = request.form.get('title', '').strip()
      if not title:
          flash('Task title is required', 'error')
      else:
          conn = get_db_connection()
          with conn.cursor() as cur:
              due_date_str = request.form.get('due_date')
              due_date = datetime.fromisoformat(due_date_str) if due_date_str else None
              category_id = request.form.get('category')
              cur.execute("""
                  UPDATE tasks SET title=%s, description=%s, category_id=%s, priority=%s, due_date=%s, updated_at=CURRENT_TIMESTAMP
                  WHERE id=%s AND user_id=%s
              """, (title, request.form.get('description'), category_id if category_id else None, request.form.get('priority'), due_date, task_id, session['user_id']))
          conn.commit()
          conn.close()
          flash('Task updated successfully!', 'success')
          return redirect(url_for('dashboard'))
  return render_template_string(EDIT_TASK_TEMPLATE, user=user, categories=categories, task=task)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
  user = get_user_by_id(session['user_id'])
  if request.method == 'POST':
      full_name = request.form.get('full_name', '').strip()
      email = request.form.get('email', '').strip().lower()
      
      if not full_name or not email:
          flash('Name and email are required', 'error')
      else:
          conn = get_db_connection()
          with conn.cursor() as cur:
              profile_picture_filename = user.get('profile_picture')
              if 'profile_picture' in request.files:
                  file = request.files['profile_picture']
                  if file and file.filename != '' and allowed_file(file.filename):
                      filename = secure_filename(f"{session['user_id']}_{uuid.uuid4().hex}.{file.filename.rsplit('.', 1)[1].lower()}")
                      file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                      profile_picture_filename = filename

              cur.execute("""
                  UPDATE users SET full_name=%s, email=%s, bio=%s, location=%s, website=%s, profile_picture=%s, updated_at=CURRENT_TIMESTAMP
                  WHERE id=%s
              """, (full_name, email, request.form.get('bio'), request.form.get('location'), request.form.get('website'), profile_picture_filename, session['user_id']))
          conn.commit()
          conn.close()
          flash('Profile updated successfully!', 'success')
          return redirect(url_for('profile'))
  return render_template_string(PROFILE_TEMPLATE, user=user)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
  user = get_user_by_id(session['user_id'])
  if request.method == 'POST':
      conn = get_db_connection()
      with conn.cursor() as cur:
          cur.execute("""
              UPDATE users SET theme=%s, notifications_enabled=%s, updated_at=CURRENT_TIMESTAMP
              WHERE id=%s
          """, (request.form.get('theme'), request.form.get('notifications_enabled') == 'true', session['user_id']))
      conn.commit()
      conn.close()
      flash('Settings updated successfully!', 'success')
      return redirect(url_for('settings'))
  return render_template_string(SETTINGS_TEMPLATE, user=user)

@app.route('/toggle/<int:task_id>')
@login_required
def toggle_task(task_id):
  conn = get_db_connection()
  with conn.cursor() as cur:
      cur.execute("SELECT completed FROM tasks WHERE id = %s AND user_id = %s", (task_id, session['user_id']))
      task = cur.fetchone()
      if task:
          new_status = not task[0]
          completed_at = datetime.now() if new_status else None
          cur.execute("UPDATE tasks SET completed=%s, completed_at=%s WHERE id=%s", (new_status, completed_at, task_id))
  conn.commit()
  conn.close()
  return redirect(request.referrer or url_for('dashboard'))

@app.route('/delete/<int:task_id>')
@login_required
def delete_task(task_id):
  conn = get_db_connection()
  with conn.cursor() as cur:
      cur.execute("DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, session['user_id']))
  conn.commit()
  conn.close()
  flash('Task deleted.', 'success')
  return redirect(url_for('dashboard'))

@app.route('/duplicate/<int:task_id>')
@login_required
def duplicate_task(task_id):
  conn = get_db_connection()
  with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
      cur.execute("SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, session['user_id']))
      task = cur.fetchone()
      if task:
          cur.execute("""
              INSERT INTO tasks (user_id, title, description, category_id, priority, due_date)
              VALUES (%s, %s, %s, %s, %s, %s)
          """, (session['user_id'], f"Copy of {task['title']}", task['description'], task['category_id'], task['priority'], task['due_date']))
  conn.commit()
  conn.close()
  flash('Task duplicated.', 'success')
  return redirect(url_for('dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
  return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- API Routes ---
@app.route('/api/update-theme', methods=['POST'])
@login_required
def update_theme():
  theme = request.json.get('theme', 'dark')
  conn = get_db_connection()
  with conn.cursor() as cur:
      cur.execute("UPDATE users SET theme=%s WHERE id=%s", (theme, session['user_id']))
  conn.commit()
  conn.close()
  return jsonify({'success': True})

if __name__ == '__main__':
  # The init_database() function is called here to ensure
  # the database and tables are created before the app starts.
  init_database()
  app.run(debug=True)
