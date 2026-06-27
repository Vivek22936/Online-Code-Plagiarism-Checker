import ast
import difflib
import time
import io
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, session
from fpdf import FPDF
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "secret_key_for_plagiarism_checker"

import sqlite3

# --- SQLite Configuration ---
DB_PATH = "users.db"

def get_db_connection():
    """Establishes a connection to the local SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enables dictionary-like access to rows
    return conn

# --- Database Setup (Tables Creation) ---
def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # User accounts table
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      full_name TEXT, 
                      username TEXT UNIQUE, 
                      email TEXT UNIQUE, 
                      password TEXT)''')
        
        # Plagiarism check history table
        c.execute('''CREATE TABLE IF NOT EXISTS scans 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      user_email TEXT, 
                      code1_snippet TEXT, 
                      code2_snippet TEXT,
                      structural_sim REAL, 
                      textual_sim REAL, 
                      total_sim REAL, 
                      verdict TEXT,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        conn.commit()
        conn.close()
        print(f"DATABASE READY: SQLite Database '{DB_PATH}' is connected and ready.")
    except Exception as e:
        print(f"\nDATABASE ERROR: FAILED TO INITIALIZE DATABASE: {e}\n")

init_db()

# --- Plagiarism Detection Logic ---

def calculate_similarity(code1, code2, language='python'):
    start_time = time.time()
    
    # 1. Normalize and Clean Code based on Language
    def clean_code(code, lang):
        if not code: return ""
        # Remove comments
        if lang == 'python':
            code = re.sub(r'#.*', '', code)
        else: # JS, C++, Java
            code = re.sub(r'//.*', '', code)
            code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        
        # Normalize whitespace
        lines = [l.strip() for l in code.splitlines() if l.strip()]
        return "\n".join(lines)

    c1_clean = clean_code(code1, language)
    c2_clean = clean_code(code2, language)
    
    # Textual Similarity (difflib on cleaned text)
    text_sim = round(difflib.SequenceMatcher(None, c1_clean, c2_clean).ratio() * 100, 2)
    
    # 2. Structural Similarity
    struct_sim = 0
    if language == 'python':
        def get_ast_dump(c):
            try: return ast.dump(ast.parse(c))
            except: return ""
        ast1, ast2 = get_ast_dump(code1), get_ast_dump(code2)
        if ast1 and ast2:
            struct_sim = round(difflib.SequenceMatcher(None, ast1, ast2).ratio() * 100, 2)
    
    # Fallback for others or if AST fails: Token-based structural comparison
    if struct_sim == 0:
        # Extract structure-defining tokens: brackets, operators, and common keywords
        keywords = r'\b(if|else|for|while|return|function|class|def|import|include|public|private|static|void|int|float|let|const|var)\b'
        tokens_regex = keywords + r'|[\{\}\(\)\[\]\+\-\*\/%&|^!<>=\.,;]'
        
        def get_structural_tokens(c):
            return "".join(re.findall(tokens_regex, c))
            
        t1, t2 = get_structural_tokens(c1_clean), get_structural_tokens(c2_clean)
        if t1 and t2:
            struct_sim = round(difflib.SequenceMatcher(None, t1, t2).ratio() * 100, 2)
    
    # Final Similarity (Average)
    total_sim = round((text_sim + struct_sim) / 2, 2)

    # Highlight Logic
    lines1, lines2 = code1.splitlines(), code2.splitlines()
    matcher = difflib.SequenceMatcher(None, lines1, lines2)
    
    results = {
        "textual": text_sim,
        "structural": struct_sim,
        "total": total_sim,
        "execution_time": round(time.time() - start_time, 4),
        "hl1": [],
        "hl2": []
    }

    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            for i in range(i1, i2): results["hl1"].append({"idx": i, "cls": "hl-both"})
            for j in range(j1, j2): results["hl2"].append({"idx": j, "cls": "hl-both"})
        elif opcode == "replace":
            for i in range(max(i2 - i1, j2 - j1)):
                l1 = lines1[i1 + i] if i1 + i < i2 else None
                l2 = lines2[j1 + i] if j1 + i < j2 else None
                cls = "hl-struct"
                if l1 is not None and l2 is not None:
                    if difflib.SequenceMatcher(None, l1, l2).ratio() > 0.8: cls = "hl-text"
                if l1 is not None: results["hl1"].append({"idx": i1 + i, "cls": cls})
                if l2 is not None: results["hl2"].append({"idx": j1 + i, "cls": cls})
        elif opcode == "delete":
            for i in range(i1, i2): results["hl1"].append({"idx": i, "cls": "hl-struct"})
        elif opcode == "insert":
            for j in range(j1, j2): results["hl2"].append({"idx": j, "cls": "hl-struct"})
            
    return results

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['POST'])
def signup():
    data = request.json
    name = data.get('name')
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    conn = get_db_connection()
    c = conn.cursor()
    
    # Check if email exists
    c.execute("SELECT 1 FROM users WHERE email = ?", (email,))
    if c.fetchone():
        conn.close()
        return jsonify({"error": "This email is already registered."}), 400
        
    # Check if username exists
    c.execute("SELECT 1 FROM users WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        return jsonify({"error": "This username is already taken."}), 400

    try:
        hashed_pw = generate_password_hash(password)
        c.execute("INSERT INTO users (full_name, username, email, password) VALUES (?, ?, ?, ?)",
                  (name, username, email, hashed_pw))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": "Database error: " + str(e)}), 500
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ?", (data['email'],))
    user = c.fetchone()
    conn.close()
    
    if user and check_password_hash(user[4], data['password']):
        session['user_name'] = user[1]
        session['user_email'] = user[3]
        return jsonify({"success": True, "name": user[1]})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    code1, code2 = data.get('code1', ''), data.get('code2', '')
    language = data.get('language', 'python')
    user_email = session.get('user_email', 'Guest')
    
    results = calculate_similarity(code1, code2, language)
    
    # Verdict
    if results['total'] > 75: verdict = "High Plagiarism Detected"
    elif results['total'] > 40: verdict = "Caution: Significant Overlap"
    else: verdict = "Original Content"
    
    results['verdict'] = verdict

    # Save to Database History
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""INSERT INTO scans 
                     (user_email, code1_snippet, code2_snippet, structural_sim, textual_sim, total_sim, verdict) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (user_email, code1[:100], code2[:100], results['structural'], results['textual'], results['total'], verdict))
        conn.commit()
        conn.close()
    except:
        pass

    return jsonify(results)

@app.route('/history', methods=['GET'])
def history():
    user_email = session.get('user_email', 'Guest')
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT structural_sim, textual_sim, total_sim, verdict, timestamp FROM scans WHERE user_email = ? ORDER BY timestamp DESC LIMIT 5", (user_email,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{"structural": r[0], "textual": r[1], "total": r[2], "verdict": r[3], "time": r[4]} for r in rows])

@app.route('/clear_history', methods=['POST'])
def clear_history():
    user_email = session.get('user_email', 'Guest')
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM scans WHERE user_email = ?", (user_email,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/debug_db')
def debug_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT id, full_name, username, email FROM users")
        users = [dict(row) for row in c.fetchall()]
        
        c.execute("SELECT * FROM scans ORDER BY timestamp DESC")
        scans = [dict(row) for row in c.fetchall()]
        
        conn.close()
        
        # Simple HTML response for debugging
        html = "<html><body style='font-family:sans-serif; padding:20px; background:#1a1a1a; color:white;'>"
        html += "<h1>Database Debug View</h1>"
        
        html += "<h2>Users Table</h2><table border='1' style='width:100%; border-collapse:collapse;'>"
        html += "<tr><th>ID</th><th>Name</th><th>Username</th><th>Email</th></tr>"
        for u in users:
            html += f"<tr><td>{u['id']}</td><td>{u['full_name']}</td><td>{u['username']}</td><td>{u['email']}</td></tr>"
        html += "</table>"
        
        html += "<h2>Scans Table (Plagiarism History)</h2><table border='1' style='width:100%; border-collapse:collapse;'>"
        html += "<tr><th>ID</th><th>User</th><th>Verdict</th><th>Total %</th><th>Time</th></tr>"
        for s in scans:
            html += f"<tr><td>{s['id']}</td><td>{s['user_email']}</td><td>{s['verdict']}</td><td>{s['total_sim']}%</td><td>{s['timestamp']}</td></tr>"
        html += "</table>"
        
        html += "</body></html>"
        return html
    except Exception as e:
        return f"Error connecting to MySQL: {str(e)}<br>Please check your credentials in app.py."

@app.route('/download_report', methods=['POST'])
def download_report():
    data = request.json
    code1, code2 = data.get('code1', ''), data.get('code2', '')
    m = data.get('metrics', {})
    language = data.get('language', 'python').upper()
    
    pdf = FPDF()
    pdf.add_page()
    
    # Header
    # --- 1. Header (Centered Title and Info) ---
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "ONLINE CODE PLAGIARISM REPORT", ln=True, align='C')
    pdf.ln(5)
    
    pdf.set_font("Arial", size=10)
    pdf.set_text_color(0, 0, 0)
    now = datetime.now()
    pdf.cell(0, 8, f"Date: {now.strftime('%d-%m-%Y')}", ln=True)
    pdf.cell(0, 8, f"Time: {now.strftime('%H:%M:%S')}", ln=True)
    pdf.cell(0, 8, f"Language: {language}", ln=True)
    pdf.cell(0, 8, f"Execution Time: {m.get('execution_time', 0)} seconds", ln=True)
    pdf.ln(2)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)
    
    # --- 2. Similarity Metrics (Colored Text) ---
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, "Similarity Metrics", ln=True)
    pdf.set_font("Arial", 'B', 12)
    
    # Structural -> Red
    pdf.set_text_color(255, 77, 77)
    pdf.cell(0, 8, f"Structural Similarity: {m.get('structural', 0)}%", ln=True)
    
    # Textual -> Blue
    pdf.set_text_color(51, 153, 255)
    pdf.cell(0, 8, f"Textual Similarity: {m.get('textual', 0)}%", ln=True)
    
    # Total -> Green
    pdf.set_text_color(0, 255, 136)
    pdf.cell(0, 10, f"Total Similarity: {m.get('total', 0)}%", ln=True)
    
    # Verdict -> Black
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, f"Plagiarism Verdict: {m.get('verdict', 'N/A')}", ln=True)
    pdf.ln(8)
    
    # --- 3. Detailed Technical Analysis ---
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, "Detailed Technical Analysis", ln=True)
    pdf.set_font("Arial", size=10)
    technical_info = (
        "The plagiarism detection system uses a hybrid similarity model combining structural and textual analysis. "
        "Structural similarity is calculated using AST (Abstract Syntax Tree) comparison. AST represents the logical structure "
        "of source code, allowing detection of similarity in loops, conditionals, functions, and execution flow even if variable "
        "names or formatting are changed. Textual similarity is computed using sequence-based token comparison to detect directly "
        "matching or closely similar code lines.\n\n"
        "Color indicators used in analysis:\n"
        "- Red: Structural match (logical similarity)\n"
        "- Blue: Textual match (direct code similarity)\n"
        "- Green: Overall similarity score (Total Match)"
    )
    pdf.multi_cell(0, 5, technical_info)
    pdf.ln(10)
    
    # --- 4. Submitted Codes (Sequential) ---
    def clean_text(t):
        if not t: return ""
        return t.encode('latin-1', 'ignore').decode('latin-1')

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 12, "Submitted Code 1", ln=True)
    
    pdf.set_font("Courier", 'B', 11)
    pdf.multi_cell(0, 6, clean_text(code1), border=1)
    pdf.ln(10)
    
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 12, "Submitted Code 2", ln=True)
    
    pdf.set_font("Courier", 'B', 11)
    pdf.multi_cell(0, 6, clean_text(code2), border=1)
    pdf.set_y(pdf.get_y() + 5)
    
    from flask import make_response
    pdf_bytes = bytes(pdf.output())
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Plagiarism_Report_{int(time.time())}.pdf'
    return response

if __name__ == '__main__':
    app.run(debug=True)
