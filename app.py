import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "progress.db"
SECRET_PATH = DATA_DIR / "secret.txt"

CATEGORY_COLORS = [
    "#c2410c",
    "#b45309",
    "#4d7c0f",
    "#0f766e",
    "#1d4ed8",
    "#7c3aed",
    "#be185d",
    "#44403c",
]
MENTION_RE = re.compile(r"@([^\s@,，;；]+)")


def iso_week_key(dt=None):
    dt = dt or datetime.now()
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def week_range_bounds(week_key):
    year, week = week_key.split("-W")
    monday = datetime.fromisocalendar(int(year), int(week), 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_secret_key():
    DATA_DIR.mkdir(exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    key = os.urandom(24).hex()
    SECRET_PATH.write_text(key, encoding="utf-8")
    return key


app = Flask(__name__)
app.secret_key = get_secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            assignees TEXT NOT NULL DEFAULT '[]',
            month_goal TEXT NOT NULL DEFAULT '',
            year_goal TEXT NOT NULL DEFAULT '',
            month_goals TEXT NOT NULL DEFAULT '{}',
            sales_enabled INTEGER NOT NULL DEFAULT 0,
            expected_revenue REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            week_key TEXT NOT NULL,
            requirement TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'empty',
            sales_enabled INTEGER NOT NULL DEFAULT 0,
            confirmed_revenue REAL NOT NULL DEFAULT 0,
            collected_revenue REAL NOT NULL DEFAULT 0,
            updated_by INTEGER REFERENCES users(id),
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, week_key)
        );

        CREATE TABLE IF NOT EXISTS weekly_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            week_key TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, week_key)
        );
        """
    )
    now = datetime.now().isoformat(timespec="seconds")
    existing = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        db.execute(
            "INSERT INTO users (username, password_hash, display_name, created_at, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "管理员", now, 1),
        )
        admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        seed = [
            ("产品规划", "#c2410c", ["需求梳理", "原型评审", "排期对齐"]),
            ("研发交付", "#0f766e", ["接口开发", "前端实现", "联调修复"]),
            ("测试上线", "#1d4ed8", ["用例编写", "回归验证", "发布检查"]),
            ("运营支持", "#b45309", ["周报同步", "问题跟进"]),
        ]
        for i, (name, color, task_names) in enumerate(seed):
            cur = db.execute(
                "INSERT INTO categories (name, color, sort_order, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, color, i, admin_id, now),
            )
            cat_id = cur.lastrowid
            for j, task_name in enumerate(task_names):
                db.execute(
                    "INSERT INTO tasks (category_id, name, description, sort_order, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (cat_id, task_name, "", j, admin_id, now),
                )
    migrate_schema(db)
    db.commit()
    db.close()


def migrate_schema(db):
    task_cols = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    if "assignees" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN assignees TEXT NOT NULL DEFAULT '[]'")
    if "month_goal" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN month_goal TEXT NOT NULL DEFAULT ''")
    if "year_goal" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN year_goal TEXT NOT NULL DEFAULT ''")
    if "month_goals" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN month_goals TEXT NOT NULL DEFAULT '{}'")
    if "sales_enabled" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN sales_enabled INTEGER NOT NULL DEFAULT 0")
    if "expected_revenue" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN expected_revenue REAL NOT NULL DEFAULT 0")
    progress_cols = {row[1] for row in db.execute("PRAGMA table_info(progress)")}
    if "requirement" not in progress_cols:
        db.execute("ALTER TABLE progress ADD COLUMN requirement TEXT NOT NULL DEFAULT ''")
    if "sales_enabled" not in progress_cols:
        db.execute("ALTER TABLE progress ADD COLUMN sales_enabled INTEGER NOT NULL DEFAULT 0")
    if "confirmed_revenue" not in progress_cols:
        db.execute("ALTER TABLE progress ADD COLUMN confirmed_revenue REAL NOT NULL DEFAULT 0")
    if "collected_revenue" not in progress_cols:
        db.execute("ALTER TABLE progress ADD COLUMN collected_revenue REAL NOT NULL DEFAULT 0")
    user_cols = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "is_admin" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")


def parse_assignees(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [p.strip().lstrip("@") for p in re.split(r"[\s,，]+", raw) if p.strip()]
    return []


def mention_tags(*texts):
    tags = []
    for text in texts:
        for match in MENTION_RE.findall(text or ""):
            tag = match.strip().lstrip("@")
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def user_aliases(user):
    return {user["username"].lower(), user["display_name"].lower()}


def mention_sentence(text, user):
    text = (text or "").strip()
    if not text:
        return ""
    aliases = user_aliases(user)
    snippets = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip(" ，,;；")
        if not line:
            continue
        mentions = list(MENTION_RE.finditer(line))
        if not mentions:
            continue
        matched = [i for i, m in enumerate(mentions) if m.group(1).lower() in aliases]
        if not matched:
            continue
        if len(mentions) == 1:
            if line not in snippets:
                snippets.append(line)
            continue
        for i in matched:
            start = 0 if i == 0 else mentions[i].start()
            end = mentions[i + 1].start() if i + 1 < len(mentions) else len(line)
            piece = line[start:end].strip(" ，,;；")
            if piece and piece not in snippets:
                snippets.append(piece)
    return "\n".join(snippets)


def mentions_user(user, assignees=None, *texts):
    aliases = user_aliases(user)
    for item in parse_assignees(assignees):
        if item.lower().lstrip("@") in aliases:
            return True
    for tag in mention_tags(*texts):
        if tag.lower() in aliases:
            return True
    return False


def month_key_from_date(dt=None):
    dt = dt or datetime.now()
    return f"{dt.year}-{dt.month:02d}"


def parse_month_goals(raw, fallback=""):
    goals = {}
    if isinstance(raw, dict):
        goals = {str(k): str(v) for k, v in raw.items() if str(v).strip()}
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                goals = {str(k): str(v) for k, v in data.items() if str(v).strip()}
        except json.JSONDecodeError:
            pass
    if not goals and fallback:
        goals[month_key_from_date()] = fallback
    return goals


def ensure_author_prefix(content, display_name, previous=""):
    tag = f"@{display_name}"
    text = (content or "").strip()
    if not text or text == tag:
        return ""
    if text.startswith(tag):
        return text
    prev = (previous or "").strip()
    if prev and text.startswith(prev):
        added = text[len(prev) :].strip()
        if not added:
            return content or text
        if added.startswith(tag):
            return text
        return f"{prev}\n{tag} {added}"
    return f"{tag} {text}"


def parse_money(val, default=0.0):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def last_iso_week(year):
    dt = datetime(int(year), 12, 28)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def weeks_of_year(year):
    year = int(year)
    weeks = []
    for w in range(1, 54):
        key = f"{year}-W{w:02d}"
        try:
            monday = datetime.fromisocalendar(year, w, 1)
        except ValueError:
            break
        thursday = monday + timedelta(days=3)
        if thursday.year != year:
            if w == 1:
                weeks.append(key)
            else:
                break
        else:
            weeks.append(key)
    return weeks


def month_week_labels(weeks, year=None):
    counters = {}
    labels = {}
    for w in weeks:
        try:
            year_s, week_s = w.split("-W")
            monday = datetime.fromisocalendar(int(year_s), int(week_s), 1)
        except (ValueError, TypeError):
            labels[w] = w
            continue
        thursday = monday + timedelta(days=3)
        key = (thursday.year, thursday.month)
        counters[key] = counters.get(key, 0) + 1
        if year is not None and thursday.year != int(year):
            labels[w] = f"{thursday.year % 100}年{thursday.month}月W{counters[key]}"
        else:
            labels[w] = f"{thursday.month}月W{counters[key]}"
    return labels


def normalize_week_key(week_key):
    try:
        year_s, week_s = str(week_key).split("-W")
        return f"{int(year_s)}-W{int(week_s):02d}"
    except (ValueError, TypeError):
        return week_key or ""


def week_month_in_year(week_key, year):
    try:
        year_s, week_s = normalize_week_key(week_key).split("-W")
        monday = datetime.fromisocalendar(int(year_s), int(week_s), 1)
        thursday = monday + timedelta(days=3)
    except (ValueError, TypeError):
        return None
    if thursday.year != int(year):
        return None
    return thursday.month


def week_month_key(week_key):
    try:
        year_s, week_s = normalize_week_key(week_key).split("-W")
        monday = datetime.fromisocalendar(int(year_s), int(week_s), 1)
        thursday = monday + timedelta(days=3)
    except (ValueError, TypeError):
        return None
    return f"{thursday.year}-{thursday.month:02d}"


def shift_month_key(month_key, delta=1):
    year, month = [int(part) for part in month_key.split("-")]
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return f"{year}-{month:02d}"


def iter_month_keys(start_key, end_key):
    current = start_key
    while current <= end_key:
        yield current
        current = shift_month_key(current, 1)


def serialize_task(row):
    task = dict(row)
    task["assignees"] = parse_assignees(task.get("assignees"))
    task["month_goal"] = task.get("month_goal") or ""
    task["year_goal"] = task.get("year_goal") or ""
    task["month_goals"] = parse_month_goals(task.get("month_goals"), task.get("month_goal") or "")
    task["sales_enabled"] = bool(task.get("sales_enabled") or 0)
    task["expected_revenue"] = parse_money(task.get("expected_revenue"))
    return task


def serialize_progress(row):
    item = dict(row)
    item["requirement"] = item.get("requirement") or ""
    item["content"] = item.get("content") or ""
    item["sales_enabled"] = bool(item.get("sales_enabled") or 0)
    item["confirmed_revenue"] = parse_money(item.get("confirmed_revenue"))
    item["collected_revenue"] = parse_money(item.get("collected_revenue"))
    return item


def serialize_user(row, with_admin=True):
    user = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
    }
    if with_admin:
        user["is_admin"] = bool(row["is_admin"] if "is_admin" in row.keys() else row["username"] == "admin")
    return user


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "未登录"}), 401
        return fn(*args, **kwargs)

    return wrapper


def current_user(db):
    row = db.execute(
        "SELECT id, username, display_name, is_admin FROM users WHERE id = ?",
        (session["user_id"],),
    ).fetchone()
    if not row:
        try:
            row = db.execute(
                "SELECT id, username, display_name FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return serialize_user(row) if row else None


def is_admin_user(user):
    return bool(user and user.get("is_admin"))


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "未登录"}), 401
        db = get_db()
        if not is_admin_user(current_user(db)):
            return jsonify({"error": "需要管理员权限"}), 403
        return fn(*args, **kwargs)

    return wrapper


def user_map(db):
    rows = db.execute("SELECT id, username, display_name FROM users").fetchall()
    return {row["id"]: dict(row) for row in rows}


def row_dict(row):
    return dict(row) if row else None


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/me")
def api_me():
    if not session.get("user_id"):
        return jsonify({"user": None})
    db = get_db()
    return jsonify({"user": current_user(db)})


@app.post("/api/register")
def api_register():
    return jsonify({"error": "请联系管理员开通帐号"}), 403


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "帐号或密码错误"}), 401
    session["user_id"] = user["id"]
    return jsonify({"user": serialize_user(user)})


@app.post("/api/logout")
@login_required
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/board")
@login_required
def api_board():
    db = get_db()
    users = user_map(db)
    categories = [dict(r) for r in db.execute("SELECT * FROM categories ORDER BY sort_order, id").fetchall()]
    tasks = [serialize_task(r) for r in db.execute("SELECT * FROM tasks ORDER BY sort_order, id").fetchall()]
    progress_rows = db.execute(
        """
        SELECT p.*, u.display_name AS editor_name
        FROM progress p
        LEFT JOIN users u ON u.id = p.updated_by
        """
    ).fetchall()
    progress = [serialize_progress(r) for r in progress_rows]
    return jsonify(
        {
            "categories": categories,
            "tasks": tasks,
            "progress": progress,
            "users": list(users.values()),
            "current_week": iso_week_key(),
        }
    )


@app.post("/api/categories")
@login_required
def api_create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    color = (data.get("color") or "").strip() or CATEGORY_COLORS[0]
    if not name:
        return jsonify({"error": "请填写分类名称"}), 400
    db = get_db()
    max_order = db.execute("SELECT COALESCE(MAX(sort_order), -1) FROM categories").fetchone()[0]
    cur = db.execute(
        "INSERT INTO categories (name, color, sort_order, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, color, max_order + 1, session["user_id"], datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    row = db.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/categories/<int:cat_id>")
@login_required
def api_update_category(cat_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    if not row:
        return jsonify({"error": "分类不存在"}), 404
    name = (data.get("name") if "name" in data else row["name"]).strip()
    color = (data.get("color") if "color" in data else row["color"]).strip()
    if not name:
        return jsonify({"error": "请填写分类名称"}), 400
    db.execute("UPDATE categories SET name = ?, color = ? WHERE id = ?", (name, color, cat_id))
    db.commit()
    return jsonify(dict(db.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()))


@app.delete("/api/categories/<int:cat_id>")
@login_required
def api_delete_category(cat_id):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    db.commit()
    return jsonify({"ok": True})


def collect_assignees(data, extra_text=""):
    values = parse_assignees(data.get("assignees"))
    for tag in mention_tags(data.get("name") or "", extra_text):
        if tag not in values:
            values.append(tag)
    return values


@app.post("/api/tasks")
@login_required
def api_create_task():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    category_id = data.get("category_id")
    description = (data.get("description") or "").strip()
    if not name:
        return jsonify({"error": "请填写任务名称"}), 400
    if not category_id:
        return jsonify({"error": "请选择分类"}), 400
    db = get_db()
    cat = db.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not cat:
        return jsonify({"error": "分类不存在"}), 404
    max_order = db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM tasks WHERE category_id = ?",
        (category_id,),
    ).fetchone()[0]
    assignees = collect_assignees(data, description)
    me = current_user(db)
    sales_enabled = 1 if is_admin_user(me) and data.get("sales_enabled") else 0
    expected_revenue = parse_money(data.get("expected_revenue")) if is_admin_user(me) else 0
    goals = parse_month_goals(data.get("month_goals"), (data.get("month_goal") or "").strip())
    if data.get("month_key") and "month_goal" in data:
        key = str(data.get("month_key"))
        val = (data.get("month_goal") or "").strip()
        if val:
            goals[key] = val
        else:
            goals.pop(key, None)
    month_goal = (data.get("month_goal") or "").strip()
    cur = db.execute(
        """
        INSERT INTO tasks (category_id, name, description, sort_order, created_by, created_at, assignees, month_goal, year_goal, month_goals, sales_enabled, expected_revenue)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_id,
            name,
            description,
            max_order + 1,
            session["user_id"],
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(assignees, ensure_ascii=False),
            month_goal,
            (data.get("year_goal") or "").strip(),
            json.dumps(goals, ensure_ascii=False),
            sales_enabled,
            expected_revenue,
        ),
    )
    db.commit()
    return jsonify(serialize_task(db.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


@app.put("/api/tasks/<int:task_id>")
@login_required
def api_update_task(task_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return jsonify({"error": "任务不存在"}), 404
    name = (data.get("name") if "name" in data else row["name"]).strip()
    description = data.get("description") if "description" in data else row["description"]
    category_id = data.get("category_id") if "category_id" in data else row["category_id"]
    if not name:
        return jsonify({"error": "请填写任务名称"}), 400
    assignees = collect_assignees(data, description) if "assignees" in data else parse_assignees(row["assignees"])
    year_goal = data.get("year_goal") if "year_goal" in data else (row["year_goal"] if "year_goal" in row.keys() else "")
    goals = parse_month_goals(row["month_goals"] if "month_goals" in row.keys() else "{}", row["month_goal"] if "month_goal" in row.keys() else "")
    if "month_goals" in data:
        goals = parse_month_goals(data.get("month_goals"), "")
    if data.get("month_key") and "month_goal" in data:
        key = str(data.get("month_key"))
        val = (data.get("month_goal") or "").strip()
        if val:
            goals[key] = val
        else:
            goals.pop(key, None)
    month_goal = data.get("month_goal") if "month_goal" in data else (row["month_goal"] if "month_goal" in row.keys() else "")
    sales_enabled = row["sales_enabled"] if "sales_enabled" in row.keys() else 0
    expected_revenue = row["expected_revenue"] if "expected_revenue" in row.keys() else 0
    if is_admin_user(current_user(db)):
        if "sales_enabled" in data:
            sales_enabled = 1 if data.get("sales_enabled") else 0
        if "expected_revenue" in data:
            expected_revenue = parse_money(data.get("expected_revenue"))
    db.execute(
        """
        UPDATE tasks SET name = ?, description = ?, category_id = ?, assignees = ?, month_goal = ?, year_goal = ?, month_goals = ?, sales_enabled = ?, expected_revenue = ?
        WHERE id = ?
        """,
        (
            name,
            description,
            category_id,
            json.dumps(assignees, ensure_ascii=False),
            month_goal or "",
            year_goal or "",
            json.dumps(goals, ensure_ascii=False),
            sales_enabled,
            expected_revenue,
            task_id,
        ),
    )
    db.commit()
    return jsonify(serialize_task(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()))


@app.delete("/api/tasks/<int:task_id>")
@login_required
def api_delete_task(task_id):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.put("/api/progress")
@login_required
def api_upsert_progress():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    week_key = (data.get("week_key") or "").strip()
    requirement = (data.get("requirement") or "").strip()
    content = (data.get("content") or "").strip()
    status = (data.get("status") or "empty").strip()
    if status not in {"empty", "doing", "done", "blocked"}:
        return jsonify({"error": "无效状态"}), 400
    if not task_id or not week_key:
        return jsonify({"error": "缺少任务或周次"}), 400
    if not requirement and not content and status == "empty":
        status = "empty"
    elif (requirement or content) and status == "empty":
        status = "doing"
    db = get_db()
    task = db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    me = current_user(db)
    existing = db.execute(
        "SELECT * FROM progress WHERE task_id = ? AND week_key = ?",
        (task_id, week_key),
    ).fetchone()
    content = ensure_author_prefix(content, me["display_name"], existing["content"] if existing else "")
    sales_enabled = existing["sales_enabled"] if existing and "sales_enabled" in existing.keys() else 0
    confirmed_revenue = existing["confirmed_revenue"] if existing and "confirmed_revenue" in existing.keys() else 0
    collected_revenue = existing["collected_revenue"] if existing and "collected_revenue" in existing.keys() else 0
    if is_admin_user(me):
        if "sales_enabled" in data:
            sales_enabled = 1 if data.get("sales_enabled") else 0
        if "confirmed_revenue" in data:
            confirmed_revenue = parse_money(data.get("confirmed_revenue"))
        if "collected_revenue" in data:
            collected_revenue = parse_money(data.get("collected_revenue"))
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO progress (task_id, week_key, requirement, content, status, sales_enabled, confirmed_revenue, collected_revenue, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id, week_key) DO UPDATE SET
            requirement = excluded.requirement,
            content = excluded.content,
            status = excluded.status,
            sales_enabled = excluded.sales_enabled,
            confirmed_revenue = excluded.confirmed_revenue,
            collected_revenue = excluded.collected_revenue,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (task_id, week_key, requirement, content, status, sales_enabled, confirmed_revenue, collected_revenue, session["user_id"], now),
    )
    tags = mention_tags(requirement)
    if tags:
        db.execute(
            "UPDATE tasks SET assignees = ? WHERE id = ?",
            (json.dumps(tags, ensure_ascii=False), task_id),
        )
    db.commit()
    row = db.execute(
        """
        SELECT p.*, u.display_name AS editor_name
        FROM progress p
        LEFT JOIN users u ON u.id = p.updated_by
        WHERE p.task_id = ? AND p.week_key = ?
        """,
        (task_id, week_key),
    ).fetchone()
    return jsonify(serialize_progress(row))


@app.put("/api/weekly-notes")
@login_required
def api_upsert_note():
    data = request.get_json(silent=True) or {}
    week_key = (data.get("week_key") or "").strip()
    content = data.get("content") or ""
    if not week_key:
        return jsonify({"error": "缺少周次"}), 400
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO weekly_notes (user_id, week_key, content, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, week_key) DO UPDATE SET
            content = excluded.content,
            updated_at = excluded.updated_at
        """,
        (session["user_id"], week_key, content, now),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM weekly_notes WHERE user_id = ? AND week_key = ?",
        (session["user_id"], week_key),
    ).fetchone()
    return jsonify(dict(row))


@app.get("/api/summary")
@login_required
def api_summary():
    week_key = (request.args.get("week") or iso_week_key()).strip()
    db = get_db()
    categories = [dict(r) for r in db.execute("SELECT * FROM categories ORDER BY sort_order, id").fetchall()]
    tasks = [serialize_task(r) for r in db.execute("SELECT * FROM tasks ORDER BY sort_order, id").fetchall()]
    progress = [
        serialize_progress(r)
        for r in db.execute(
            """
            SELECT p.*, u.display_name AS editor_name
            FROM progress p
            LEFT JOIN users u ON u.id = p.updated_by
            WHERE p.week_key = ?
            """,
            (week_key,),
        ).fetchall()
    ]
    notes = [
        dict(r)
        for r in db.execute(
            """
            SELECT n.*, u.display_name, u.username
            FROM weekly_notes n
            JOIN users u ON u.id = n.user_id
            WHERE n.week_key = ?
            ORDER BY u.id
            """,
            (week_key,),
        ).fetchall()
    ]
    monday, sunday = week_range_bounds(week_key)
    return jsonify(
        {
            "week_key": week_key,
            "week_label": f"{monday.strftime('%m/%d')} – {sunday.strftime('%m/%d')}",
            "categories": categories,
            "tasks": tasks,
            "progress": progress,
            "notes": notes,
            "me": current_user(db),
        }
    )


@app.get("/api/inbox")
@login_required
def api_inbox():
    current_week = iso_week_key()
    db = get_db()
    me = current_user(db)
    categories = {r["id"]: dict(r) for r in db.execute("SELECT * FROM categories").fetchall()}
    tasks = [serialize_task(r) for r in db.execute("SELECT * FROM tasks ORDER BY sort_order, id").fetchall()]
    progress_rows = [
        serialize_progress(r)
        for r in db.execute(
            """
            SELECT p.*, u.display_name AS editor_name
            FROM progress p
            LEFT JOIN users u ON u.id = p.updated_by
            """
        ).fetchall()
    ]
    progress_map = {}
    for item in progress_rows:
        progress_map.setdefault(item["task_id"], []).append(item)

    this_week = []
    overdue = []
    for task in tasks:
        cat = categories.get(task["category_id"], {})
        cells = sorted(progress_map.get(task["id"], []), key=lambda x: x["week_key"])
        current_cell = next((p for p in cells if p["week_key"] == current_week), None)
        if current_cell:
            snippet = mention_sentence(current_cell.get("requirement"), me) or mention_sentence(
                current_cell.get("content"), me
            )
            if snippet:
                this_week.append(
                    {
                        "task_id": task["id"],
                        "task_name": task["name"],
                        "category_name": cat.get("name", ""),
                        "category_color": cat.get("color", "#44403c"),
                        "week_key": current_week,
                        "snippet": snippet,
                        "status": current_cell.get("status") or "empty",
                    }
                )
        done_later = any(p["week_key"] >= current_week and p["status"] == "done" for p in cells)
        if done_later:
            continue
        older = [p for p in cells if p["week_key"] < current_week and p["status"] in {"doing", "blocked"}]
        older = [p for p in older if mention_sentence(p.get("requirement"), me) or mention_sentence(p.get("content"), me)]
        if not older:
            continue
        latest = older[-1]
        snippet = mention_sentence(latest.get("requirement"), me) or mention_sentence(latest.get("content"), me)
        overdue.append(
            {
                "task_id": task["id"],
                "task_name": task["name"],
                "category_name": cat.get("name", ""),
                "category_color": cat.get("color", "#44403c"),
                "week_key": latest["week_key"],
                "snippet": snippet,
                "status": latest.get("status") or "empty",
            }
        )
    return jsonify({"current_week": current_week, "this_week": this_week, "overdue": overdue, "me": me})


@app.get("/api/admin/users")
@admin_required
def api_admin_users():
    db = get_db()
    rows = db.execute("SELECT id, username, display_name, is_admin, created_at FROM users ORDER BY id").fetchall()
    return jsonify({"users": [serialize_user(r) for r in rows]})


@app.post("/api/admin/users")
@admin_required
def api_admin_create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or username).strip()
    if not username or not password:
        return jsonify({"error": "请填写帐号和密码"}), 400
    if len(username) < 2:
        return jsonify({"error": "帐号至少 2 个字符"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 位"}), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, display_name, created_at, is_admin) VALUES (?, ?, ?, ?, ?)",
            (
                username,
                generate_password_hash(password),
                display_name,
                datetime.now().isoformat(timespec="seconds"),
                1 if data.get("is_admin") else 0,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "该帐号已被使用"}), 409
    row = db.execute("SELECT id, username, display_name, is_admin FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(serialize_user(row)), 201


@app.put("/api/admin/users/<int:user_id>")
@admin_required
def api_admin_update_user(user_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return jsonify({"error": "用户不存在"}), 404
    display_name = (data.get("display_name") if "display_name" in data else row["display_name"]).strip()
    is_admin = row["is_admin"] if "is_admin" in row.keys() else 0
    if "is_admin" in data:
        is_admin = 1 if data.get("is_admin") else 0
        if not is_admin and row["username"] == "admin":
            return jsonify({"error": "不能取消初始管理员"}), 400
    if is_admin == 0 and row["is_admin"]:
        others = db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND id != ?", (user_id,)).fetchone()[0]
        if others == 0:
            return jsonify({"error": "至少保留一名管理员"}), 400
    if data.get("password"):
        if len(data.get("password") or "") < 4:
            return jsonify({"error": "密码至少 4 位"}), 400
        db.execute(
            "UPDATE users SET display_name = ?, is_admin = ?, password_hash = ? WHERE id = ?",
            (display_name, is_admin, generate_password_hash(data["password"]), user_id),
        )
    else:
        db.execute("UPDATE users SET display_name = ?, is_admin = ? WHERE id = ?", (display_name, is_admin, user_id))
    db.commit()
    updated = db.execute("SELECT id, username, display_name, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(serialize_user(updated))


@app.delete("/api/admin/users/<int:user_id>")
@admin_required
def api_admin_delete_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return jsonify({"error": "用户不存在"}), 404
    if user_id == session.get("user_id"):
        return jsonify({"error": "不能删除当前登录帐号"}), 400
    if row["is_admin"]:
        others = db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND id != ?", (user_id,)).fetchone()[0]
        if others == 0:
            return jsonify({"error": "至少保留一名管理员"}), 400
    db.execute("UPDATE categories SET created_by = NULL WHERE created_by = ?", (user_id,))
    db.execute("UPDATE tasks SET created_by = NULL WHERE created_by = ?", (user_id,))
    db.execute("UPDATE progress SET updated_by = NULL WHERE updated_by = ?", (user_id,))
    db.execute("DELETE FROM weekly_notes WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/sales")
@admin_required
def api_sales():
    now = datetime.now()
    db = get_db()
    tasks = [serialize_task(r) for r in db.execute("SELECT * FROM tasks ORDER BY sort_order, id").fetchall()]
    progress = [
        serialize_progress(r)
        for r in db.execute(
            "SELECT p.*, u.display_name AS editor_name FROM progress p LEFT JOIN users u ON u.id = p.updated_by"
        ).fetchall()
    ]
    by_task = {}
    for item in progress:
        by_task.setdefault(item["task_id"], []).append(item)

    money_months = []
    for item in progress:
        if parse_money(item.get("confirmed_revenue")) or parse_money(item.get("collected_revenue")):
            month_key = week_month_key(item.get("week_key"))
            if month_key:
                money_months.append(month_key)

    year_start = f"{now.year}-01"
    if money_months:
        start_month = min(year_start, min(money_months))
        end_month = max(money_months)
    else:
        start_month = year_start
        end_month = f"{now.year}-12"
    if end_month < start_month:
        end_month = start_month

    month_keys = list(iter_month_keys(start_month, end_month))
    months_meta = []
    for key in month_keys:
        year, month = [int(part) for part in key.split("-")]
        label = f"{month}月" if year == now.year else f"{year}年{month}月"
        months_meta.append({"key": key, "year": year, "month": month, "label": label})
    month_index = {item["key"]: i for i, item in enumerate(months_meta)}

    monthly = [
        {
            "key": item["key"],
            "year": item["year"],
            "month": item["month"],
            "label": item["label"],
            "expected": 0.0,
            "confirmed": 0.0,
            "collected": 0.0,
        }
        for item in months_meta
    ]

    projects = []
    for task in tasks:
        cells = by_task.get(task["id"], [])
        expected = parse_money(task.get("expected_revenue"))
        confirmed = sum(parse_money(p.get("confirmed_revenue")) for p in cells)
        collected = sum(parse_money(p.get("collected_revenue")) for p in cells)
        if not task.get("sales_enabled") and expected <= 0 and confirmed <= 0 and collected <= 0:
            continue
        by_month = [
            {"key": item["key"], "month": item["month"], "label": item["label"], "confirmed": 0.0, "collected": 0.0}
            for item in months_meta
        ]
        for cell in cells:
            confirmed_amt = parse_money(cell.get("confirmed_revenue"))
            collected_amt = parse_money(cell.get("collected_revenue"))
            if not confirmed_amt and not collected_amt:
                continue
            key = week_month_key(cell.get("week_key"))
            if key not in month_index:
                continue
            idx = month_index[key]
            by_month[idx]["confirmed"] += confirmed_amt
            by_month[idx]["collected"] += collected_amt
            monthly[idx]["confirmed"] += confirmed_amt
            monthly[idx]["collected"] += collected_amt
        projects.append(
            {
                "task_id": task["id"],
                "name": task["name"],
                "expected": expected,
                "confirmed": confirmed,
                "collected": collected,
                "uncollected": max(0.0, confirmed - collected),
                "planning": confirmed <= 0,
                "by_month": by_month,
            }
        )

    projects.sort(key=lambda p: (p["planning"], -(p["expected"] or 0), p["name"]))
    total_expected = sum(p["expected"] for p in projects)

    monthly_cumulative = []
    acc = {"confirmed": 0.0, "collected": 0.0}
    for slot in monthly:
        acc["confirmed"] += slot["confirmed"]
        acc["collected"] += slot["collected"]
        monthly_cumulative.append(
            {
                "key": slot["key"],
                "label": slot["label"],
                "expected": total_expected,
                "confirmed": acc["confirmed"],
                "collected": acc["collected"],
            }
        )

    end_label = months_meta[-1]["label"] if months_meta else f"{now.year}年"
    return jsonify(
        {
            "year": now.year,
            "start_month": start_month,
            "end_month": end_month,
            "end_label": end_label,
            "months": months_meta,
            "projects": projects,
            "monthly": monthly,
            "monthly_cumulative": monthly_cumulative,
        }
    )


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
