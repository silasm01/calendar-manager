from flask import Flask, render_template, jsonify, request
from get_ics import fetch_and_update_ics, approve_event, remove_approval
import os
import sqlite3
import subprocess
import threading
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
DATABASE = os.getenv('DATABASE_PATH', 'calmanage.db')

workscrape_lock = threading.Lock()
workscrape_process = None
workscrape_output = []
workscrape_started_at = None
workscrape_finished_at = None
workscrape_return_code = None

WORKSCRAPE_INTERVAL_SECONDS = 3600  # 1 hour


def _run_workscrape():
    global workscrape_process, workscrape_output, workscrape_started_at, workscrape_finished_at, workscrape_return_code

    with workscrape_lock:
        if workscrape_process and workscrape_process.poll() is None:
            return  # Already running

    script_path = Path(app.root_path).parent / 'workscrape.py'
    if not script_path.exists():
        return

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'

    process = subprocess.Popen(
        [sys.executable, '-u', str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(script_path.parent),
        env=env
    )

    with workscrape_lock:
        workscrape_process = process
        workscrape_output = ['Starting workscrape.py...']
        workscrape_started_at = datetime.utcnow().isoformat() + 'Z'
        workscrape_finished_at = None
        workscrape_return_code = None

    reader_thread = threading.Thread(target=_workscrape_reader, args=(process,), daemon=True)
    reader_thread.start()


def _workscrape_scheduler():
    while True:
        _run_workscrape()
        threading.Event().wait(WORKSCRAPE_INTERVAL_SECONDS)


scheduler_thread = threading.Thread(target=_workscrape_scheduler, daemon=True)
scheduler_thread.start()


def _append_workscrape_output(line):
    global workscrape_output
    with workscrape_lock:
        workscrape_output.append(line.rstrip())
        if len(workscrape_output) > 1000:
            workscrape_output = workscrape_output[-1000:]


def _workscrape_reader(proc):
    global workscrape_process, workscrape_finished_at, workscrape_return_code

    if proc.stdout:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            _append_workscrape_output(line)

    proc.wait()

    with workscrape_lock:
        workscrape_return_code = proc.returncode
        workscrape_finished_at = datetime.utcnow().isoformat() + 'Z'
        workscrape_process = None

    if proc.returncode == 0:
        _append_workscrape_output('workscrape.py finished successfully.')
    else:
        _append_workscrape_output(f'workscrape.py exited with code {proc.returncode}.')

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_buffers (
            id INTEGER PRIMARY KEY,
            event_uid TEXT NOT NULL,
            source TEXT NOT NULL,
            buffer_before INTEGER DEFAULT 0,
            buffer_after INTEGER DEFAULT 0,
            UNIQUE(event_uid, source)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_privacy (
            id INTEGER PRIMARY KEY,
            event_uid TEXT NOT NULL,
            source TEXT NOT NULL,
            use_generic_title BOOLEAN DEFAULT 0,
            use_generic_description BOOLEAN DEFAULT 0,
            UNIQUE(event_uid, source)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ignored_events (
            id INTEGER PRIMARY KEY,
            event_uid TEXT NOT NULL UNIQUE
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

@app.route("/")
def index():
    return render_template("index.html")


@app.route('/api/workscrape/start', methods=['POST'])
def start_workscrape():
    with workscrape_lock:
        if workscrape_process and workscrape_process.poll() is None:
            return jsonify({'success': False, 'message': 'workscrape.py is already running'}), 409

    script_path = Path(app.root_path).parent / 'workscrape.py'
    if not script_path.exists():
        return jsonify({'success': False, 'message': f'Cannot find {script_path.name}'}), 404

    try:
        _run_workscrape()
        return jsonify({'success': True, 'message': 'workscrape.py started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/workscrape/status', methods=['GET'])
def workscrape_status():
    with workscrape_lock:
        running = bool(workscrape_process and workscrape_process.poll() is None)
        return jsonify({
            'running': running,
            'output': workscrape_output,
            'started_at': workscrape_started_at,
            'finished_at': workscrape_finished_at,
            'return_code': workscrape_return_code
        })

@app.route("/api/init")
def api_init():
    """Load all initial data: buffers, privacy settings, and ignored events"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get buffers
    cursor.execute('SELECT event_uid, buffer_before, buffer_after FROM event_buffers')
    buffer_rows = cursor.fetchall()
    buffers = {}
    for row in buffer_rows:
        buffers[row['event_uid']] = {
            'before': row['buffer_before'],
            'after': row['buffer_after']
        }
    
    # Get privacy settings
    cursor.execute('SELECT event_uid, use_generic_title, use_generic_description FROM event_privacy')
    privacy_rows = cursor.fetchall()
    privacy = {}
    for row in privacy_rows:
        privacy[row['event_uid']] = {
            'useGenericTitle': bool(row['use_generic_title']),
            'useGenericDescription': bool(row['use_generic_description'])
        }
    
    # Get ignored events
    cursor.execute('SELECT event_uid FROM ignored_events')
    ignored_rows = cursor.fetchall()
    ignored = [row['event_uid'] for row in ignored_rows]
    
    conn.close()
    
    return jsonify({
        'buffers': buffers,
        'privacy': privacy,
        'ignored': ignored
    })

@app.route("/api/pending_events")
def pending_events():
    try:
        events = fetch_and_update_ics()
        return jsonify(events)
    except Exception as e:
        print(f"Error in pending_events: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route("/api/approve", methods=['POST'])
def approve():
    data = request.get_json()
    
    result = approve_event(
        uid=data.get('uid'),
        source=data.get('source'),
        start=data.get('start'),
        end=data.get('end'),
        title=data.get('title'),
        description=data.get('description'),
        use_generic_title=data.get('use_generic_title', False),
        use_generic_description=data.get('use_generic_description', False),
        buffer_before=data.get('buffer_before', 0),
        buffer_after=data.get('buffer_after', 0)
    )
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route("/api/buffers", methods=['GET'])
def get_buffers():
    """Get all saved buffers"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT event_uid, source, buffer_before, buffer_after FROM event_buffers')
    rows = cursor.fetchall()
    conn.close()
    
    buffers = {}
    for row in rows:
        buffers[row['event_uid']] = {
            'before': row['buffer_before'],
            'after': row['buffer_after']
        }
    return jsonify(buffers)

@app.route("/api/buffers", methods=['POST'])
def save_buffers():
    """Save buffer for an event"""
    data = request.get_json()
    uid = data.get('uid')
    source = data.get('source')
    buffer_before = data.get('buffer_before', 0)
    buffer_after = data.get('buffer_after', 0)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO event_buffers (event_uid, source, buffer_before, buffer_after)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(event_uid, source) DO UPDATE SET
            buffer_before = excluded.buffer_before,
            buffer_after = excluded.buffer_after
    ''', (uid, source, buffer_before, buffer_after))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route("/api/privacy", methods=['GET'])
def get_privacy():
    """Get all saved privacy settings"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT event_uid, use_generic_title, use_generic_description FROM event_privacy')
    rows = cursor.fetchall()
    conn.close()
    
    privacy = {}
    for row in rows:
        privacy[row['event_uid']] = {
            'useGenericTitle': bool(row['use_generic_title']),
            'useGenericDescription': bool(row['use_generic_description'])
        }
    return jsonify(privacy)

@app.route("/api/privacy", methods=['POST'])
def save_privacy():
    """Save privacy settings for an event"""
    data = request.get_json()
    uid = data.get('uid')
    source = data.get('source')
    use_generic_title = data.get('use_generic_title', False)
    use_generic_description = data.get('use_generic_description', False)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO event_privacy (event_uid, source, use_generic_title, use_generic_description)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(event_uid, source) DO UPDATE SET
            use_generic_title = excluded.use_generic_title,
            use_generic_description = excluded.use_generic_description
    ''', (uid, source, use_generic_title, use_generic_description))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route("/api/ignored", methods=['GET'])
def get_ignored():
    """Get all ignored event UIDs"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT event_uid FROM ignored_events')
    rows = cursor.fetchall()
    conn.close()
    
    ignored = [row['event_uid'] for row in rows]
    return jsonify(ignored)

@app.route("/api/ignored", methods=['POST'])
def add_ignored():
    """Add event to ignored list"""
    data = request.get_json()
    uid = data.get('uid')
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO ignored_events (event_uid) VALUES (?)', (uid,))
        conn.commit()
        result = {'success': True}
    except sqlite3.IntegrityError:
        result = {'success': True}  # Already ignored
    finally:
        conn.close()
    
    return jsonify(result)

@app.route("/api/ignored/<uid>", methods=['DELETE'])
def delete_ignored(uid):
    """Remove event from ignored list"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM ignored_events WHERE event_uid = ?', (uid,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route("/api/remove-approval", methods=['POST'])
def remove_approval_endpoint():
    """Remove an event from all blocked calendars"""
    data = request.get_json()
    uid = data.get('uid')
    
    if not uid:
        return jsonify({'success': False, 'message': 'No UID provided'}), 400
    
    result = remove_approval(uid)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0')