import os
import re
import json
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

import truststore
truststore.inject_into_ssl()

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, g, stream_with_context
from google import genai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
# SQLite database path
# Vercel serverless environment uses /tmp for writable files.
if os.getenv("VERCEL") == "1":
    DB_PATH = "/tmp/chatbot.db"
else:
    DB_PATH = os.path.join(BASE_DIR, "data", "chatbot.db")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing. Create a .env file from .env.example.")

client = genai.Client(api_key=API_KEY)

# Gemini model failover/retry configuration.
# The primary model is configurable through GEMINI_MODEL.
# If Gemini returns a transient 503/UNAVAILABLE or a retired model error,
# the next model is tried automatically.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.6-flash").split(",")
    if m.strip()
]
MAX_GEMINI_RETRIES = max(1, int(os.getenv("GEMINI_RETRIES", "3")))
RETRY_DELAY_SECONDS = max(1, float(os.getenv("GEMINI_RETRY_DELAY", "2")))

# Small in-process rate limiter for a local/demo deployment.
RATE_WINDOW = 60
RATE_LIMIT = 30
rate_state = {}

def db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL DEFAULT 'New Chat',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        extracted_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);
    """)
    conn.commit()
    conn.close()

def now():
    return datetime.now(timezone.utc).isoformat()

def new_conversation(title="New Chat"):
    conn = db()
    stamp = now()
    cur = conn.execute(
        "INSERT INTO conversations(title, created_at, updated_at) VALUES (?, ?, ?)",
        (title[:80] or "New Chat", stamp, stamp),
    )
    conn.commit()
    return cur.lastrowid

def conversation_exists(cid):
    return db().execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone() is not None

def require_conversation(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            cid = int(request.view_args.get("conversation_id") or request.args.get("conversation_id") or (request.get_json(silent=True) or {}).get("conversation_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "A valid conversation_id is required."}), 400
        if not conversation_exists(cid):
            return jsonify({"error": "Conversation not found."}), 404
        g.conversation_id = cid
        return fn(*args, **kwargs)
    return wrapper

def rate_allowed():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "local").split(",")[0].strip()
    t = time.time()
    bucket = rate_state.get(ip, [])
    bucket = [x for x in bucket if t - x < RATE_WINDOW]
    if len(bucket) >= RATE_LIMIT:
        rate_state[ip] = bucket
        return False
    bucket.append(t)
    rate_state[ip] = bucket
    return True

def friendly_error(exc):
    text = str(exc)
    upper = text.upper()
    if "401" in text or "UNAUTHENTICATED" in upper or "INVALID API KEY" in upper:
        return "Gemini API key is invalid or expired. Create a new Gemini API key and update your .env file."
    if "403" in text or "PERMISSION_DENIED" in upper:
        return "Gemini API access was denied. Check the API key restrictions/project access in Google AI Studio."
    if "429" in text or "RESOURCE_EXHAUSTED" in upper:
        return "Gemini API quota/rate limit reached. Please wait and try again, or use a key with available quota."
    if "404" in text or "NOT_FOUND" in upper or "MODEL_NOT_FOUND" in upper:
        return "The configured Gemini model is unavailable. Check GEMINI_MODEL in .env."
    if "503" in text or "UNAVAILABLE" in upper:
        return "Gemini service is temporarily unavailable. Automatic retries/fallbacks were exhausted; please try again."
    if "CERTIFICATE_VERIFY_FAILED" in upper or "SSL" in upper:
        return "Secure connection failed. Check your network/certificate settings and try again."
    if "TIMEOUT" in upper or "CONNECTERROR" in upper:
        return "Network connection failed. Please check your internet connection and try again."
    return "Gemini request failed. Check the server terminal for the exact error."

def is_retryable_gemini_error(exc):
    text = str(exc).upper()
    return any(token in text for token in (
        "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
        "504", "DEADLINE_EXCEEDED", "408", "TIMEOUT"
    ))

def model_candidates():
    seen = set()
    for model in [MODEL] + FALLBACK_MODELS:
        if model and model not in seen:
            seen.add(model)
            yield model

def generate_with_failover(prompt):
    last_exc = None
    for model in model_candidates():
        for attempt in range(MAX_GEMINI_RETRIES):
            try:
                print(f"Gemini request: model={model}, attempt={attempt + 1}")
                return client.models.generate_content_stream(model=model, contents=prompt), model
            except Exception as exc:
                last_exc = exc
                print(f"Gemini error: model={model}, attempt={attempt + 1}: {repr(exc)}")
                if not is_retryable_gemini_error(exc):
                    raise
                if attempt < MAX_GEMINI_RETRIES - 1:
                    time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
                else:
                    break
    raise last_exc

def build_prompt(conversation_id, user_message):
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 24",
        (conversation_id,),
    ).fetchall()
    rows = list(reversed(rows))
    parts = []
    for row in rows:
        role = "User" if row["role"] == "user" else "Assistant"
        parts.append(f"{role}: {row['content']}")
    context = "\n\n".join(parts)
    doc = conn.execute(
        "SELECT filename, extracted_text FROM documents WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if doc and doc["extracted_text"]:
        context += f"\n\nAttached PDF ({doc['filename']}):\n{doc['extracted_text'][:45000]}"
    return (
        "You are a helpful professional AI assistant. Maintain continuity using the conversation "
        "history below. Answer clearly and accurately. Use Markdown when useful.\n\n"
        f"Conversation context:\n{context}\n\n"
        f"Current user message:\nUser: {user_message}\nAssistant:"
    )

@app.route("/")
def home():
    return render_template("index.html", model=MODEL)

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "AI Assistant", "model": MODEL})

@app.get("/api/chats")
def list_chats():
    q = request.args.get("q", "").strip()
    conn = db()
    if q:
        rows = conn.execute(
            """SELECT id, title, created_at, updated_at FROM conversations
               WHERE title LIKE ? OR id IN
               (SELECT conversation_id FROM messages WHERE content LIKE ?)
               ORDER BY updated_at DESC""",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return jsonify({"chats": [dict(r) for r in rows]})

@app.post("/api/chats")
def create_chat():
    data = request.get_json(silent=True) or {}
    cid = new_conversation(data.get("title", "New Chat"))
    return jsonify({"id": cid, "title": data.get("title", "New Chat")}), 201

@app.get("/api/chats/<int:conversation_id>")
def get_chat(conversation_id):
    conn = db()
    chat = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if not chat:
        return jsonify({"error": "Conversation not found."}), 404
    messages = conn.execute(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    docs = conn.execute(
        "SELECT id, filename, created_at FROM documents WHERE conversation_id=? ORDER BY id DESC",
        (conversation_id,),
    ).fetchall()
    return jsonify({
        "chat": dict(chat),
        "messages": [dict(x) for x in messages],
        "documents": [dict(x) for x in docs],
    })

@app.patch("/api/chats/<int:conversation_id>")
def rename_chat(conversation_id):
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()[:80]
    if not title:
        return jsonify({"error": "Title cannot be empty."}), 400
    cur = db().execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title, now(), conversation_id))
    db().commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({"ok": True, "title": title})

@app.delete("/api/chats/<int:conversation_id>")
def delete_chat(conversation_id):
    cur = db().execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({"ok": True})

@app.delete("/api/chats")
def clear_all_chats():
    db().execute("DELETE FROM conversations")
    db().commit()
    return jsonify({"ok": True})

@app.post("/api/chat")
def chat():
    if not rate_allowed():
        return jsonify({"error": "Too many requests. Please wait a moment."}), 429
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    try:
        cid = int(data.get("conversation_id"))
    except (TypeError, ValueError):
        cid = None
    if not message:
        return jsonify({"error": "Please enter a message."}), 400
    if not cid or not conversation_exists(cid):
        cid = new_conversation()
    conn = db()
    existing_count = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]
    conn.execute(
        "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?,?,?,?)",
        (cid, "user", message, now()),
    )
    if existing_count == 0:
        title = re.sub(r"\s+", " ", message)[:55]
        conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title or "New Chat", now(), cid))
    else:
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid))
    conn.commit()

    prompt = build_prompt(cid, message)

    def generate():
        try:
            response_stream, used_model = generate_with_failover(prompt)
            chunks = []
            for chunk in response_stream:
                piece = getattr(chunk, "text", None)
                if piece:
                    chunks.append(piece)
                    yield f"data: {json_line({'type':'chunk','text':piece})}\n\n"
            reply = "".join(chunks).strip()
            if not reply:
                reply = "Gemini returned an empty response. Please try again."
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                (cid, "assistant", reply, now()),
            )
            conn2.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid))
            conn2.commit()
            conn2.close()
            yield f"data: {json_line({'type':'done','conversation_id':cid,'reply':reply,'model':used_model})}\n\n"
        except Exception as exc:
            print("CHAT ERROR:", repr(exc))
            yield f"data: {json_line({'type':'error','message':friendly_error(exc)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

def json_line(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)

@app.post("/api/regenerate/<int:conversation_id>")
def regenerate(conversation_id):
    if not conversation_exists(conversation_id):
        return jsonify({"error": "Conversation not found."}), 404
    conn = db()
    last_user = conn.execute(
        "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if not last_user:
        return jsonify({"error": "There is no user message to regenerate."}), 400
    conn.execute(
        "DELETE FROM messages WHERE id=(SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1)",
        (conversation_id,),
    )
    conn.commit()
    # Reuse the normal streaming endpoint internally by duplicating its core.
    prompt = build_prompt(conversation_id, last_user["content"])
    def generate():
        try:
            response_stream, used_model = generate_with_failover(prompt)
            chunks=[]
            for chunk in response_stream:
                piece=getattr(chunk,"text",None)
                if piece:
                    chunks.append(piece)
                    yield f"data: {json_line({'type':'chunk','text':piece})}\n\n"
            reply="".join(chunks).strip() or "Gemini returned an empty response. Please try again."
            c=sqlite3.connect(DB_PATH)
            c.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES (?,?,?,?)",
                      (conversation_id,"assistant",reply,now()))
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(),conversation_id))
            c.commit(); c.close()
            yield f"data: {json_line({'type':'done','conversation_id':conversation_id,'reply':reply,'model':used_model})}\n\n"
        except Exception as exc:
            print("REGENERATE ERROR:", repr(exc))
            yield f"data: {json_line({'type':'error','message':friendly_error(exc)})}\n\n"
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.post("/api/upload-pdf")
def upload_pdf():
    file = request.files.get("file")
    try:
        cid = int(request.form.get("conversation_id"))
    except (TypeError, ValueError):
        cid = None
    if not cid or not conversation_exists(cid):
        return jsonify({"error": "Create/select a chat first."}), 400
    if not file or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400
    try:
        from pypdf import PdfReader
        reader = PdfReader(file.stream)
        pages=[]
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        extracted="\n\n".join(pages).strip()
        if not extracted:
            return jsonify({"error": "This PDF has no extractable text. Scanned PDFs need OCR."}), 400
        extracted=extracted[:45000]
        db().execute(
            "INSERT INTO documents(conversation_id,filename,extracted_text,created_at) VALUES (?,?,?,?)",
            (cid, file.filename[:180], extracted, now()),
        )
        db().commit()
        return jsonify({"ok":True,"filename":file.filename,"characters":len(extracted)})
    except Exception as exc:
        print("PDF ERROR:", repr(exc))
        return jsonify({"error": "Could not read this PDF. Please try another file."}), 400

@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "File too large. Maximum request size is 12 MB."}), 413

@app.errorhandler(Exception)
def unhandled(exc):
    print("UNHANDLED ERROR:", repr(exc))
    return jsonify({"error": "Something went wrong on the server. Please try again."}), 500

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
