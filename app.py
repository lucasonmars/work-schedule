import hashlib
import json
import os
import re
import secrets
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
PASSWORD_HASH_METHOD = "pbkdf2:sha256" if not hasattr(hashlib, "scrypt") else "scrypt"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


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


JOIN_REQUEST_TTL_DAYS = 7
SUPERADMIN_USERNAME = (os.environ.get("CHAOJIAN_SUPERADMIN_USER") or "").strip()
SUPERADMIN_PASSWORD = os.environ.get("CHAOJIAN_SUPERADMIN_PASSWORD") or ""


def is_reserved_username(username: str) -> bool:
    name = (username or "").strip().lower()
    reserved = {"superadmin"}
    if SUPERADMIN_USERNAME:
        reserved.add(SUPERADMIN_USERNAME.lower())
    return name in reserved


def ensure_superadmin(db):
    if not SUPERADMIN_USERNAME or not SUPERADMIN_PASSWORD:
        return
    now = datetime.now().isoformat(timespec="seconds")
    row = db.execute("SELECT * FROM users WHERE username = ?", (SUPERADMIN_USERNAME,)).fetchone()
    if row:
        db.execute(
            """
            UPDATE users
            SET is_admin = 1, is_superadmin = 1, status = 'active', organization_id = NULL
            WHERE id = ?
            """,
            (row["id"],),
        )
        return
    db.execute(
        """
        INSERT INTO users (username, password_hash, display_name, created_at, is_admin, organization_id, status, is_superadmin)
        VALUES (?, ?, ?, ?, 1, NULL, 'active', 1)
        """,
        (SUPERADMIN_USERNAME, hash_password(SUPERADMIN_PASSWORD), "超管", now),
    )


def expire_stale_join_requests(db):
    cutoff = (datetime.now() - timedelta(days=JOIN_REQUEST_TTL_DAYS)).isoformat(timespec="seconds")
    stale = db.execute(
        """
        SELECT jr.id, jr.user_id FROM join_requests jr
        WHERE jr.status = 'pending' AND jr.created_at < ?
        """,
        (cutoff,),
    ).fetchall()
    if not stale:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    for row in stale:
        db.execute(
            "UPDATE join_requests SET status = 'expired', reviewed_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        db.execute("DELETE FROM users WHERE id = ? AND status = 'pending'", (row["user_id"],))
    db.commit()
    return len(stale)


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
    db.row_factory = sqlite3.Row
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

        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS join_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT
        );
        """
    )
    migrate_schema(db)
    existing = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        pass
    elif existing > 0 and not db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]:
        now = datetime.now().isoformat(timespec="seconds")
        cur = db.execute("INSERT INTO organizations (name, created_at) VALUES (?, ?)", ("默认组织", now))
        org_id = cur.lastrowid
        db.execute(
            "UPDATE users SET organization_id = ?, status = 'active' WHERE organization_id IS NULL AND COALESCE(is_superadmin, 0) = 0",
            (org_id,),
        )
        db.execute("UPDATE categories SET organization_id = ? WHERE organization_id IS NULL", (org_id,))
    ensure_superadmin(db)
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
    if "organization_id" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN organization_id INTEGER REFERENCES organizations(id)")
    if "status" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    user_cols = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "is_superadmin" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0")
    if "api_key_hash" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN api_key_hash TEXT")
    if "api_key_prefix" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN api_key_prefix TEXT")
    ensure_superadmin(db)
    cat_cols = {row[1] for row in db.execute("PRAGMA table_info(categories)")}
    if "organization_id" not in cat_cols:
        db.execute("ALTER TABLE categories ADD COLUMN organization_id INTEGER REFERENCES organizations(id)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    org_cols = {row[1] for row in db.execute("PRAGMA table_info(organizations)")}
    if "status" not in org_cols:
        db.execute("ALTER TABLE organizations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS join_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT
        )
        """
    )
    orphan_users = db.execute(
        "SELECT COUNT(*) FROM users WHERE organization_id IS NULL AND COALESCE(is_superadmin, 0) = 0"
    ).fetchone()[0]
    if orphan_users:
        default = db.execute("SELECT id FROM organizations WHERE name = ?", ("默认组织",)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")
        if not default:
            cur = db.execute("INSERT INTO organizations (name, created_at) VALUES (?, ?)", ("默认组织", now))
            org_id = cur.lastrowid
        else:
            org_id = default["id"]
        db.execute(
            """
            UPDATE users SET organization_id = ?, status = COALESCE(NULLIF(status, ''), 'active')
            WHERE organization_id IS NULL AND COALESCE(is_superadmin, 0) = 0
            """,
            (org_id,),
        )
        db.execute("UPDATE categories SET organization_id = ? WHERE organization_id IS NULL", (org_id,))
    ensure_superadmin(db)


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


def add_calendar_months(year, month, delta=0):
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return year, month


def first_week_of_month(year, month):
    for w in range(1, 54):
        try:
            monday = datetime.fromisocalendar(int(year), w, 1)
        except ValueError:
            break
        thursday = monday + timedelta(days=3)
        if thursday.year == int(year) and thursday.month == int(month):
            return f"{year}-W{w:02d}"
    return iso_week_key(datetime(int(year), int(month), 1))


def seed_org_demo(db, org_id, user_id, base_dt=None):
    base_dt = base_dt or datetime.now()
    now = base_dt.isoformat(timespec="seconds")
    current_week = iso_week_key(base_dt)
    year, month = base_dt.year, base_dt.month

    seed = [
        (
            "研发",
            "#0f766e",
            [
                ("需求梳理", "", 0, False),
                ("接口开发", "", 0, False),
            ],
        ),
        (
            "销售",
            "#c2410c",
            [
                ("示例项目 · 10万", "", 100000, False),
                ("示例项目 · 20万", "注册月起分三月确认回款", 200000, True),
            ],
        ),
    ]

    for cat_i, (cat_name, color, tasks) in enumerate(seed):
        cur = db.execute(
            "INSERT INTO categories (name, color, sort_order, created_by, created_at, organization_id) VALUES (?, ?, ?, ?, ?, ?)",
            (cat_name, color, cat_i, user_id, now, org_id),
        )
        cat_id = cur.lastrowid
        for task_i, task_def in enumerate(tasks):
            name, desc, expected, split_revenue = task_def
            cur = db.execute(
                """
                INSERT INTO tasks (
                    category_id, name, description, sort_order, created_by, created_at,
                    sales_enabled, expected_revenue
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cat_id,
                    name,
                    desc,
                    task_i,
                    user_id,
                    now,
                    1 if expected else 0,
                    expected or 0,
                ),
            )
            task_id = cur.lastrowid
            if split_revenue:
                confirmed_slots = [50000, 100000, 50000]
                collected_slots = [50000, 0, 0]
                for i in range(3):
                    y, m = add_calendar_months(year, month, i)
                    week_key = first_week_of_month(y, m)
                    confirmed = confirmed_slots[i]
                    collected = collected_slots[i]
                    db.execute(
                        """
                        INSERT INTO progress (
                            task_id, week_key, requirement, content, status,
                            sales_enabled, confirmed_revenue, collected_revenue,
                            updated_by, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            week_key,
                            f"第{i + 1}月确认回款 {int(confirmed / 10000)} 万",
                            "",
                            "done" if collected else "doing",
                            confirmed,
                            collected,
                            user_id,
                            now,
                        ),
                    )
            elif expected and cat_name == "销售":
                db.execute(
                    """
                    INSERT INTO progress (
                        task_id, week_key, requirement, content, status,
                        sales_enabled, confirmed_revenue, collected_revenue,
                        updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, 'doing', 1, 0, 0, ?, ?)
                    """,
                    (task_id, current_week, "策划中，尚未确认回款", "", user_id, now),
                )
            elif cat_name == "研发":
                db.execute(
                    """
                    INSERT INTO progress (
                        task_id, week_key, requirement, content, status,
                        updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, 'doing', ?, ?)
                    """,
                    (task_id, current_week, "示例任务，可随意修改", "", user_id, now),
                )


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
    if "status" in row.keys():
        user["status"] = row["status"] or "active"
    if "organization_id" in row.keys() and row["organization_id"]:
        user["organization_id"] = row["organization_id"]
    if "organization_name" in row.keys() and row["organization_name"]:
        user["organization_name"] = row["organization_name"]
    if with_admin:
        user["is_admin"] = bool(row["is_admin"] if "is_admin" in row.keys() else row["username"] == "admin")
    if "is_superadmin" in row.keys():
        user["is_superadmin"] = bool(row["is_superadmin"])
    elif SUPERADMIN_USERNAME and row["username"] == SUPERADMIN_USERNAME:
        user["is_superadmin"] = True
    if "api_key_hash" in row.keys():
        user["has_api_key"] = bool(row["api_key_hash"])
    if "api_key_prefix" in row.keys() and row["api_key_prefix"]:
        user["api_key_prefix"] = row["api_key_prefix"]
    return user


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return f"jj_{secrets.token_urlsafe(32)}"


def extract_api_key_from_request():
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-API-Key") or "").strip()


def load_user_auth_row(db, user_id):
    return db.execute(
        """
        SELECT u.id, u.username, u.display_name, u.is_admin, u.is_superadmin, u.status,
               u.organization_id, u.api_key_hash, u.api_key_prefix, o.name AS organization_name,
               o.status AS organization_status
        FROM users u
        LEFT JOIN organizations o ON o.id = u.organization_id
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()


def find_user_by_api_key(db, raw_key):
    if not raw_key or not raw_key.startswith("jj_"):
        return None
    digest = hash_api_key(raw_key)
    row = db.execute(
        """
        SELECT u.id, u.username, u.display_name, u.is_admin, u.is_superadmin, u.status,
               u.organization_id, u.api_key_hash, u.api_key_prefix, o.name AS organization_name,
               o.status AS organization_status
        FROM users u
        LEFT JOIN organizations o ON o.id = u.organization_id
        WHERE u.api_key_hash = ?
        """,
        (digest,),
    ).fetchone()
    return row


def auth_user_allowed(row):
    if not row or row["status"] != "active":
        return False
    if row["is_superadmin"]:
        return True
    if not row["organization_id"]:
        return False
    if (row["organization_status"] or "active") == "suspended":
        return False
    return True


def resolve_request_user(db):
    raw_key = extract_api_key_from_request()
    if raw_key:
        row = find_user_by_api_key(db, raw_key)
        if row and auth_user_allowed(row):
            return row
        return None
    user_id = session.get("user_id")
    if not user_id:
        return None
    row = load_user_auth_row(db, user_id)
    if row and auth_user_allowed(row):
        return row
    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = get_db()
        row = resolve_request_user(db)
        if not row:
            if session.get("user_id"):
                session.clear()
            return jsonify({"error": "未登录或 API Key 无效"}), 401
        g.auth_user_id = row["id"]
        return fn(*args, **kwargs)

    return wrapper


def current_user(db):
    user_id = getattr(g, "auth_user_id", None) or session.get("user_id")
    if not user_id:
        return None
    row = db.execute(
        """
        SELECT u.id, u.username, u.display_name, u.is_admin, u.is_superadmin, u.status,
               u.organization_id, u.api_key_hash, u.api_key_prefix, o.name AS organization_name
        FROM users u
        LEFT JOIN organizations o ON o.id = u.organization_id
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row:
        try:
            row = db.execute(
                "SELECT id, username, display_name FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return serialize_user(row) if row else None


def current_org_id(db):
    user_id = getattr(g, "auth_user_id", None) or session.get("user_id")
    if not user_id:
        return None
    row = db.execute("SELECT organization_id FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["organization_id"] if row else None


def auth_user_id():
    return getattr(g, "auth_user_id", None) or session.get("user_id")


def is_admin_user(user):
    return bool(user and user.get("is_admin"))


def is_superadmin_user(user):
    return bool(user and user.get("is_superadmin"))


def superadmin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = get_db()
        row = resolve_request_user(db)
        if not row:
            return jsonify({"error": "未登录"}), 401
        g.auth_user_id = row["id"]
        if not is_superadmin_user(serialize_user(row)):
            return jsonify({"error": "需要超管权限"}), 403
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = get_db()
        row = resolve_request_user(db)
        if not row:
            return jsonify({"error": "未登录"}), 401
        g.auth_user_id = row["id"]
        if not is_admin_user(serialize_user(row)):
            return jsonify({"error": "需要管理员权限"}), 403
        return fn(*args, **kwargs)

    return wrapper


def user_map(db, org_id=None):
    if org_id is None:
        org_id = current_org_id(db)
    rows = db.execute(
        "SELECT id, username, display_name FROM users WHERE organization_id = ? AND status = 'active'",
        (org_id,),
    ).fetchall()
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
    db = get_db()
    expire_stale_join_requests(db)
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or username).strip()
    org_name = (data.get("organization_name") or "").strip()
    mode = (data.get("mode") or "create").strip()

    if not username or not password or not org_name:
        return jsonify({"error": "请填写组织名称、帐号和密码"}), 400
    if len(username) < 2:
        return jsonify({"error": "帐号至少 2 个字符"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 位"}), 400
    if len(org_name) < 2:
        return jsonify({"error": "组织名称至少 2 个字符"}), 400
    if not display_name:
        return jsonify({"error": "请填写显示名"}), 400
    if mode not in {"create", "join"}:
        return jsonify({"error": "无效的注册方式"}), 400
    if is_reserved_username(username):
        return jsonify({"error": "该帐号已被使用，请更换", "code": "username_exists"}), 409

    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify({"error": "该帐号已被使用，请更换", "code": "username_exists"}), 409

    existing_org = db.execute("SELECT id, name, status FROM organizations WHERE name = ?", (org_name,)).fetchone()
    now = datetime.now().isoformat(timespec="seconds")

    if mode == "create":
        if existing_org:
            return jsonify(
                {
                    "error": "该组织名称已被使用",
                    "code": "org_exists",
                    "organization_name": existing_org["name"],
                }
            ), 409
        cur = db.execute("INSERT INTO organizations (name, created_at) VALUES (?, ?)", (org_name, now))
        org_id = cur.lastrowid
        cur = db.execute(
            """
            INSERT INTO users (username, password_hash, display_name, created_at, is_admin, organization_id, status)
            VALUES (?, ?, ?, ?, 1, ?, 'active')
            """,
            (username, hash_password(password), display_name, now, org_id),
        )
        user_id = cur.lastrowid
        seed_org_demo(db, org_id, user_id, datetime.now())
        db.commit()
        session["user_id"] = user_id
        row = db.execute(
            """
            SELECT u.id, u.username, u.display_name, u.is_admin, u.is_superadmin, u.status, u.organization_id, o.name AS organization_name
            FROM users u
            LEFT JOIN organizations o ON o.id = u.organization_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        return jsonify({"user": serialize_user(row)}), 201

    if not existing_org:
        return jsonify({"error": "组织不存在，请检查名称或选择更换名称"}), 404
    if (existing_org["status"] if "status" in existing_org.keys() else "active") == "suspended":
        return jsonify({"error": "该组织已被冻结，暂时无法加入"}), 403
    cur = db.execute(
        """
        INSERT INTO users (username, password_hash, display_name, created_at, is_admin, organization_id, status)
        VALUES (?, ?, ?, ?, 0, ?, 'pending')
        """,
        (username, hash_password(password), display_name, now, existing_org["id"]),
    )
    user_id = cur.lastrowid
    db.execute(
        "INSERT INTO join_requests (organization_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
        (existing_org["id"], user_id, now),
    )
    db.commit()
    return jsonify(
        {
            "ok": True,
            "pending": True,
            "message": "加入申请已提交，请等待组织管理员审批后再登录",
        }
    ), 201


@app.post("/api/login")
def api_login():
    db = get_db()
    expire_stale_join_requests(db)
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "帐号或密码错误"}), 401
    if user["status"] == "pending":
        return jsonify({"error": "您的加入申请待管理员审批，通过后方可登录（申请 7 天内有效）"}), 403
    if user["status"] != "active":
        return jsonify({"error": "帐号不可用，请联系组织管理员"}), 403
    if not user["is_superadmin"] and user["organization_id"]:
        org = db.execute("SELECT status FROM organizations WHERE id = ?", (user["organization_id"],)).fetchone()
        if org and (org["status"] or "active") == "suspended":
            return jsonify({"error": "被冻结"}), 403
    session["user_id"] = user["id"]
    row = db.execute(
        """
        SELECT u.id, u.username, u.display_name, u.is_admin, u.is_superadmin, u.status, u.organization_id, o.name AS organization_name
        FROM users u
        LEFT JOIN organizations o ON o.id = u.organization_id
        WHERE u.id = ?
        """,
        (user["id"],),
    ).fetchone()
    return jsonify({"user": serialize_user(row)})


@app.post("/api/logout")
@login_required
def api_logout():
    session.clear()
    return jsonify({"ok": True})


def load_org_tasks(db, org_id):
    return [
        serialize_task(r)
        for r in db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            ORDER BY t.sort_order, t.id
            """,
            (org_id,),
        ).fetchall()
    ]


def load_org_categories(db, org_id):
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM categories WHERE organization_id = ? ORDER BY sort_order, id",
            (org_id,),
        ).fetchall()
    ]


def load_progress_for_tasks(db, task_ids, week_key=None):
    if not task_ids:
        return []
    placeholders = ",".join("?" * len(task_ids))
    if week_key:
        rows = db.execute(
            f"""
            SELECT p.*, u.display_name AS editor_name
            FROM progress p
            LEFT JOIN users u ON u.id = p.updated_by
            WHERE p.week_key = ? AND p.task_id IN ({placeholders})
            """,
            [week_key, *task_ids],
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT p.*, u.display_name AS editor_name
            FROM progress p
            LEFT JOIN users u ON u.id = p.updated_by
            WHERE p.task_id IN ({placeholders})
            """,
            task_ids,
        ).fetchall()
    return [serialize_progress(r) for r in rows]


def task_assigned_to_user(me, task, progress_items=None):
    texts = [task.get("description") or ""]
    for item in progress_items or []:
        texts.append(item.get("requirement") or "")
        texts.append(item.get("content") or "")
    return mentions_user(me, task.get("assignees"), *texts)


def user_can_write_task_progress(me, task, week_requirement=""):
    if is_admin_user(me):
        return True
    return mentions_user(me, task.get("assignees"), week_requirement or "", task.get("description") or "")


def agent_write_docs():
    return {
        "method": "PUT",
        "url": "/api/agent/progress",
        "headers": {"Authorization": "Bearer <API_KEY>", "Content-Type": "application/json"},
        "body_example": {
            "task_id": 29,
            "week_key": "YYYY-Www",
            "content": "已完成",
            "status": "done",
        },
        "note": "唯一写接口。禁止探测 /api/tasks、/api/agent/task/:id。普通用户仅可写指派/@给自己的任务；管理员可写本组织任务。",
    }


def agent_report_hint():
    return (
        "汇报格式（简练，勿解读过程）：\n"
        "已完成：…\n"
        "进行中：…\n"
        "待做：…\n"
        "受阻：…\n"
        "用户说某任务做完：直接 PUT /api/agent/progress（用该任务 how_to_update，status=done），不要探测其它端点。"
    )


def agent_task_payload(task, category, cell=None, me=None, week_key=None):
    wk = (cell or {}).get("week_key") or week_key or ""
    status = (cell or {}).get("status") or "empty"
    item = {
        "task_id": task["id"],
        "task_name": task["name"],
        "category_id": task["category_id"],
        "category_name": category.get("name", "") if category else "",
        "description": task.get("description") or "",
        "assignees": task.get("assignees") or [],
        "week_key": wk,
        "requirement": (cell or {}).get("requirement") or "",
        "content": (cell or {}).get("content") or "",
        "status": status,
        "status_label": {"empty": "未填", "doing": "进行中", "done": "已完成", "blocked": "受阻"}.get(
            status, status
        ),
    }
    if cell:
        item["updated_at"] = cell.get("updated_at")
        item["editor_name"] = cell.get("editor_name")
        if me:
            item["my_snippet"] = mention_sentence(cell.get("requirement"), me) or mention_sentence(
                cell.get("content"), me
            )
    if wk:
        item["how_to_update"] = {
            "method": "PUT",
            "url": "/api/agent/progress",
            "body": {
                "task_id": task["id"],
                "week_key": wk,
                "content": "已完成",
                "status": "done",
            },
        }
    return item


@app.get("/api/me/api-key")
@login_required
def api_me_api_key_status():
    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me):
        return jsonify({"error": "超管不支持 Agent API Key"}), 403
    if not me.get("organization_id"):
        return jsonify({"error": "未加入组织"}), 400
    row = db.execute(
        "SELECT api_key_hash, api_key_prefix FROM users WHERE id = ?",
        (auth_user_id(),),
    ).fetchone()
    return jsonify(
        {
            "has_api_key": bool(row and row["api_key_hash"]),
            "api_key_prefix": (row["api_key_prefix"] if row else None) or None,
            "user": me,
        }
    )


@app.post("/api/me/api-key")
@login_required
def api_me_create_api_key():
    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me):
        return jsonify({"error": "超管不支持 Agent API Key"}), 403
    if not me.get("organization_id"):
        return jsonify({"error": "未加入组织"}), 400
    raw = generate_api_key()
    prefix = raw[:10] + "…"
    db.execute(
        "UPDATE users SET api_key_hash = ?, api_key_prefix = ? WHERE id = ?",
        (hash_api_key(raw), prefix, auth_user_id()),
    )
    db.commit()
    base = request.url_root.rstrip("/")
    return jsonify(
        {
            "api_key": raw,
            "api_key_prefix": prefix,
            "base_url": f"{base}/api/agent",
            "user": me,
            "message": "请立即复制保存，离开本页后将无法再次查看完整 Key",
        }
    )


@app.delete("/api/me/api-key")
@login_required
def api_me_revoke_api_key():
    db = get_db()
    db.execute(
        "UPDATE users SET api_key_hash = NULL, api_key_prefix = NULL WHERE id = ?",
        (auth_user_id(),),
    )
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/agent/me")
@login_required
def api_agent_me():
    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me) or not me.get("organization_id"):
        return jsonify({"error": "当前帐号无法使用 Agent API"}), 403
    return jsonify(
        {
            "user": me,
            "current_week": iso_week_key(),
            "read": {
                "my_tasks": "GET /api/agent/my-tasks?week=YYYY-Www",
                "week": "GET /api/agent/week?week=YYYY-Www",
            },
            "write": agent_write_docs(),
            "report_hint": agent_report_hint(),
        }
    )


@app.get("/api/agent/my-tasks")
@login_required
def api_agent_my_tasks():
    week_key = normalize_week_key((request.args.get("week") or iso_week_key()).strip())
    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me) or not me.get("organization_id"):
        return jsonify({"error": "当前帐号无法使用 Agent API"}), 403
    org_id = current_org_id(db)
    categories = {c["id"]: c for c in load_org_categories(db, org_id)}
    tasks = load_org_tasks(db, org_id)
    task_ids = [t["id"] for t in tasks]
    all_progress = load_progress_for_tasks(db, task_ids)
    by_task = {}
    for item in all_progress:
        by_task.setdefault(item["task_id"], []).append(item)

    items = []
    for task in tasks:
        cells = by_task.get(task["id"], [])
        if not task_assigned_to_user(me, task, cells):
            continue
        cell = next((p for p in cells if p["week_key"] == week_key), None)
        if cell is None:
            cell = {
                "week_key": week_key,
                "requirement": "",
                "content": "",
                "status": "empty",
            }
        elif not mentions_user(me, task.get("assignees"), cell.get("requirement") or "", cell.get("content") or ""):
            # still include if assigned on task level
            pass
        cat = categories.get(task["category_id"], {})
        items.append(agent_task_payload(task, cat, cell, me, week_key))

    monday, sunday = week_range_bounds(week_key)
    return jsonify(
        {
            "week_key": week_key,
            "week_label": f"{monday.strftime('%m/%d')} – {sunday.strftime('%m/%d')}",
            "current_week": iso_week_key(),
            "me": me,
            "tasks": items,
            "count": len(items),
            "write": agent_write_docs(),
            "report_hint": agent_report_hint(),
        }
    )


@app.get("/api/agent/week")
@login_required
def api_agent_week():
    week_key = normalize_week_key((request.args.get("week") or iso_week_key()).strip())
    mine_only = (request.args.get("mine") or "").strip() in {"1", "true", "yes"}
    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me) or not me.get("organization_id"):
        return jsonify({"error": "当前帐号无法使用 Agent API"}), 403
    org_id = current_org_id(db)
    categories = load_org_categories(db, org_id)
    cat_map = {c["id"]: c for c in categories}
    tasks = load_org_tasks(db, org_id)
    task_ids = [t["id"] for t in tasks]
    progress = load_progress_for_tasks(db, task_ids, week_key)
    progress_map = {p["task_id"]: p for p in progress}
    all_progress = load_progress_for_tasks(db, task_ids) if mine_only else []
    by_task_all = {}
    for item in all_progress:
        by_task_all.setdefault(item["task_id"], []).append(item)

    rows = []
    for task in tasks:
        if mine_only and not task_assigned_to_user(me, task, by_task_all.get(task["id"], [])):
            continue
        cell = progress_map.get(task["id"])
        if not cell:
            cell = {
                "week_key": week_key,
                "requirement": "",
                "content": "",
                "status": "empty",
            }
        rows.append(agent_task_payload(task, cat_map.get(task["category_id"], {}), cell, me, week_key))

    monday, sunday = week_range_bounds(week_key)
    return jsonify(
        {
            "week_key": week_key,
            "week_label": f"{monday.strftime('%m/%d')} – {sunday.strftime('%m/%d')}",
            "current_week": iso_week_key(),
            "organization": me.get("organization_name"),
            "me": me,
            "mine_only": mine_only,
            "tasks": rows,
            "count": len(rows),
            "write": agent_write_docs(),
            "report_hint": agent_report_hint(),
        }
    )


@app.put("/api/agent/progress")
@login_required
def api_agent_upsert_progress():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    week_key = normalize_week_key((data.get("week_key") or "").strip())
    content = data.get("content")
    status = data.get("status")
    if content is None:
        return jsonify({"error": "请提供 content（完成情况）"}), 400
    content = str(content).strip()
    if not task_id or not week_key:
        return jsonify({"error": "缺少 task_id 或 week_key"}), 400
    if status is not None:
        status = str(status).strip()
        if status not in {"empty", "doing", "done", "blocked"}:
            return jsonify({"error": "无效状态，可选 empty/doing/done/blocked"}), 400

    db = get_db()
    me = current_user(db)
    if is_superadmin_user(me) or not me.get("organization_id"):
        return jsonify({"error": "当前帐号无法使用 Agent API"}), 403
    org_id = current_org_id(db)
    task_row = db.execute(
        """
        SELECT t.* FROM tasks t
        JOIN categories c ON c.id = t.category_id
        WHERE t.id = ? AND c.organization_id = ?
        """,
        (task_id, org_id),
    ).fetchone()
    if not task_row:
        return jsonify({"error": "任务不存在"}), 404
    task = serialize_task(task_row)
    existing = db.execute(
        "SELECT * FROM progress WHERE task_id = ? AND week_key = ?",
        (task_id, week_key),
    ).fetchone()
    existing_requirement = existing["requirement"] if existing else ""
    if not user_can_write_task_progress(me, task, existing_requirement):
        return jsonify({"error": "只能写入指派或 @ 给你的任务的完成情况"}), 403

    content = ensure_author_prefix(content, me["display_name"], existing["content"] if existing else "")
    if status is None:
        if existing:
            status = existing["status"] or "empty"
        else:
            status = "doing" if content else "empty"
    if content and status == "empty":
        status = "doing"

    requirement = existing_requirement
    sales_enabled = existing["sales_enabled"] if existing and "sales_enabled" in existing.keys() else 0
    confirmed_revenue = existing["confirmed_revenue"] if existing and "confirmed_revenue" in existing.keys() else 0
    collected_revenue = existing["collected_revenue"] if existing and "collected_revenue" in existing.keys() else 0
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO progress (task_id, week_key, requirement, content, status, sales_enabled, confirmed_revenue, collected_revenue, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id, week_key) DO UPDATE SET
            content = excluded.content,
            status = excluded.status,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (
            task_id,
            week_key,
            requirement,
            content,
            status,
            sales_enabled,
            confirmed_revenue,
            collected_revenue,
            auth_user_id(),
            now,
        ),
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
    return jsonify(
        {
            "ok": True,
            "progress": serialize_progress(row),
            "hint": "已更新完成内容；不可通过 Agent API 修改工作任务(requirement)或销售字段",
        }
    )


@app.get("/api/board")
@login_required
def api_board():
    db = get_db()
    org_id = current_org_id(db)
    users = user_map(db, org_id)
    categories = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM categories WHERE organization_id = ? ORDER BY sort_order, id",
            (org_id,),
        ).fetchall()
    ]
    tasks = [
        serialize_task(r)
        for r in db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            ORDER BY t.sort_order, t.id
            """,
            (org_id,),
        ).fetchall()
    ]
    task_ids = [t["id"] for t in tasks]
    progress_rows = []
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        progress_rows = db.execute(
            f"""
            SELECT p.*, u.display_name AS editor_name
            FROM progress p
            LEFT JOIN users u ON u.id = p.updated_by
            WHERE p.task_id IN ({placeholders})
            """,
            task_ids,
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
    org_id = current_org_id(db)
    max_order = db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM categories WHERE organization_id = ?",
        (org_id,),
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO categories (name, color, sort_order, created_by, created_at, organization_id) VALUES (?, ?, ?, ?, ?, ?)",
        (name, color, max_order + 1, auth_user_id(), datetime.now().isoformat(timespec="seconds"), org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/categories/<int:cat_id>")
@login_required
def api_update_category(cat_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    org_id = current_org_id(db)
    row = db.execute("SELECT * FROM categories WHERE id = ? AND organization_id = ?", (cat_id, org_id)).fetchone()
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
    org_id = current_org_id(db)
    row = db.execute("SELECT id FROM categories WHERE id = ? AND organization_id = ?", (cat_id, org_id)).fetchone()
    if not row:
        return jsonify({"error": "分类不存在"}), 404
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
    org_id = current_org_id(db)
    cat = db.execute("SELECT id FROM categories WHERE id = ? AND organization_id = ?", (category_id, org_id)).fetchone()
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
            auth_user_id(),
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
    org_id = current_org_id(db)
    row = db.execute(
        """
        SELECT t.* FROM tasks t
        JOIN categories c ON c.id = t.category_id
        WHERE t.id = ? AND c.organization_id = ?
        """,
        (task_id, org_id),
    ).fetchone()
    if not row:
        return jsonify({"error": "任务不存在"}), 404
    name = (data.get("name") if "name" in data else row["name"]).strip()
    description = data.get("description") if "description" in data else row["description"]
    category_id = data.get("category_id") if "category_id" in data else row["category_id"]
    if category_id != row["category_id"]:
        cat = db.execute(
            "SELECT id FROM categories WHERE id = ? AND organization_id = ?",
            (category_id, org_id),
        ).fetchone()
        if not cat:
            return jsonify({"error": "分类不存在"}), 404
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
    org_id = current_org_id(db)
    row = db.execute(
        """
        SELECT t.id FROM tasks t
        JOIN categories c ON c.id = t.category_id
        WHERE t.id = ? AND c.organization_id = ?
        """,
        (task_id, org_id),
    ).fetchone()
    if not row:
        return jsonify({"error": "任务不存在"}), 404
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
    org_id = current_org_id(db)
    task = db.execute(
        """
        SELECT t.id FROM tasks t
        JOIN categories c ON c.id = t.category_id
        WHERE t.id = ? AND c.organization_id = ?
        """,
        (task_id, org_id),
    ).fetchone()
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
        (task_id, week_key, requirement, content, status, sales_enabled, confirmed_revenue, collected_revenue, auth_user_id(), now),
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
        (auth_user_id(), week_key, content, now),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM weekly_notes WHERE user_id = ? AND week_key = ?",
        (auth_user_id(), week_key),
    ).fetchone()
    return jsonify(dict(row))


@app.get("/api/summary")
@login_required
def api_summary():
    week_key = (request.args.get("week") or iso_week_key()).strip()
    db = get_db()
    org_id = current_org_id(db)
    categories = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM categories WHERE organization_id = ? ORDER BY sort_order, id",
            (org_id,),
        ).fetchall()
    ]
    tasks = [
        serialize_task(r)
        for r in db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            ORDER BY t.sort_order, t.id
            """,
            (org_id,),
        ).fetchall()
    ]
    task_ids = [t["id"] for t in tasks]
    progress = []
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        progress = [
            serialize_progress(r)
            for r in db.execute(
                f"""
                SELECT p.*, u.display_name AS editor_name
                FROM progress p
                LEFT JOIN users u ON u.id = p.updated_by
                WHERE p.week_key = ? AND p.task_id IN ({placeholders})
                """,
                [week_key, *task_ids],
            ).fetchall()
        ]
    notes = [
        dict(r)
        for r in db.execute(
            """
            SELECT n.*, u.display_name, u.username
            FROM weekly_notes n
            JOIN users u ON u.id = n.user_id
            WHERE n.week_key = ? AND u.organization_id = ? AND u.status = 'active'
            ORDER BY u.id
            """,
            (week_key, org_id),
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
    org_id = current_org_id(db)
    me = current_user(db)
    categories = {
        r["id"]: dict(r)
        for r in db.execute("SELECT * FROM categories WHERE organization_id = ?", (org_id,)).fetchall()
    }
    tasks = [
        serialize_task(r)
        for r in db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            ORDER BY t.sort_order, t.id
            """,
            (org_id,),
        ).fetchall()
    ]
    task_ids = [t["id"] for t in tasks]
    progress_rows = []
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        progress_rows = [
            serialize_progress(r)
            for r in db.execute(
                f"""
                SELECT p.*, u.display_name AS editor_name
                FROM progress p
                LEFT JOIN users u ON u.id = p.updated_by
                WHERE p.task_id IN ({placeholders})
                """,
                task_ids,
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
    org_id = current_org_id(db)
    rows = db.execute(
        """
        SELECT id, username, display_name, is_admin, created_at, status, api_key_hash, api_key_prefix
        FROM users
        WHERE organization_id = ?
        ORDER BY id
        """,
        (org_id,),
    ).fetchall()
    return jsonify({"users": [serialize_user(r) for r in rows]})


@app.put("/api/admin/organization")
@admin_required
def api_admin_update_organization():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if len(name) < 2:
        return jsonify({"error": "组织名称至少 2 个字符"}), 400
    db = get_db()
    org_id = current_org_id(db)
    row = db.execute("SELECT id, name FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        return jsonify({"error": "组织不存在"}), 404
    if name == row["name"]:
        return jsonify({"name": name})
    if db.execute("SELECT id FROM organizations WHERE name = ? AND id != ?", (name, org_id)).fetchone():
        return jsonify({"error": "该名称已被使用，请更换"}), 409
    db.execute("UPDATE organizations SET name = ? WHERE id = ?", (name, org_id))
    db.commit()
    return jsonify({"name": name})


@app.get("/api/admin/join-requests")
@admin_required
def api_admin_join_requests():
    db = get_db()
    expire_stale_join_requests(db)
    org_id = current_org_id(db)
    rows = db.execute(
        """
        SELECT jr.id, jr.created_at, u.id AS user_id, u.username, u.display_name
        FROM join_requests jr
        JOIN users u ON u.id = jr.user_id
        WHERE jr.organization_id = ? AND jr.status = 'pending'
        ORDER BY jr.created_at
        """,
        (org_id,),
    ).fetchall()
    return jsonify({"requests": [dict(r) for r in rows]})


@app.post("/api/admin/join-requests/<int:req_id>/approve")
@admin_required
def api_admin_approve_join(req_id):
    db = get_db()
    org_id = current_org_id(db)
    row = db.execute(
        """
        SELECT jr.*, u.username
        FROM join_requests jr
        JOIN users u ON u.id = jr.user_id
        WHERE jr.id = ? AND jr.organization_id = ? AND jr.status = 'pending'
        """,
        (req_id, org_id),
    ).fetchone()
    if not row:
        return jsonify({"error": "申请不存在或已处理"}), 404
    now = datetime.now().isoformat(timespec="seconds")
    db.execute("UPDATE users SET status = 'active' WHERE id = ?", (row["user_id"],))
    db.execute(
        "UPDATE join_requests SET status = 'approved', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (auth_user_id(), now, req_id),
    )
    db.commit()
    return jsonify({"ok": True, "username": row["username"]})


@app.post("/api/admin/join-requests/<int:req_id>/reject")
@admin_required
def api_admin_reject_join(req_id):
    db = get_db()
    org_id = current_org_id(db)
    row = db.execute(
        "SELECT * FROM join_requests WHERE id = ? AND organization_id = ? AND status = 'pending'",
        (req_id, org_id),
    ).fetchone()
    if not row:
        return jsonify({"error": "申请不存在或已处理"}), 404
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE join_requests SET status = 'rejected', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (auth_user_id(), now, req_id),
    )
    db.execute("DELETE FROM users WHERE id = ? AND status = 'pending'", (row["user_id"],))
    db.commit()
    return jsonify({"ok": True})


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
    if is_reserved_username(username):
        return jsonify({"error": "该帐号已被使用"}), 409
    db = get_db()
    org_id = current_org_id(db)
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify({"error": "该帐号已被使用"}), 409
    try:
        cur = db.execute(
            """
            INSERT INTO users (username, password_hash, display_name, created_at, is_admin, organization_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                username,
                hash_password(password),
                display_name,
                datetime.now().isoformat(timespec="seconds"),
                1 if data.get("is_admin") else 0,
                org_id,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "该帐号已被使用"}), 409
    row = db.execute(
        "SELECT id, username, display_name, is_admin, status FROM users WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(serialize_user(row)), 201


@app.put("/api/admin/users/<int:user_id>")
@admin_required
def api_admin_update_user(user_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    org_id = current_org_id(db)
    row = db.execute("SELECT * FROM users WHERE id = ? AND organization_id = ?", (user_id, org_id)).fetchone()
    if not row:
        return jsonify({"error": "用户不存在"}), 404
    display_name = (data.get("display_name") if "display_name" in data else row["display_name"]).strip()
    is_admin = row["is_admin"] if "is_admin" in row.keys() else 0
    if "is_admin" in data:
        is_admin = 1 if data.get("is_admin") else 0
        if not is_admin and row["username"] == "admin":
            return jsonify({"error": "不能取消初始管理员"}), 400
    if is_admin == 0 and row["is_admin"]:
        others = db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND id != ? AND organization_id = ?",
            (user_id, org_id),
        ).fetchone()[0]
        if others == 0:
            return jsonify({"error": "至少保留一名管理员"}), 400
    if data.get("password"):
        if len(data.get("password") or "") < 4:
            return jsonify({"error": "密码至少 4 位"}), 400
        db.execute(
            "UPDATE users SET display_name = ?, is_admin = ?, password_hash = ? WHERE id = ?",
            (display_name, is_admin, hash_password(data["password"]), user_id),
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
    org_id = current_org_id(db)
    row = db.execute("SELECT * FROM users WHERE id = ? AND organization_id = ?", (user_id, org_id)).fetchone()
    if not row:
        return jsonify({"error": "用户不存在"}), 404
    if user_id == auth_user_id():
        return jsonify({"error": "不能删除当前登录帐号"}), 400
    if row["is_admin"]:
        others = db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND id != ? AND organization_id = ?",
            (user_id, org_id),
        ).fetchone()[0]
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
    org_id = current_org_id(db)
    tasks = [
        serialize_task(r)
        for r in db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            ORDER BY t.sort_order, t.id
            """,
            (org_id,),
        ).fetchall()
    ]
    task_ids = [t["id"] for t in tasks]
    progress = []
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        progress = [
            serialize_progress(r)
            for r in db.execute(
                f"""
                SELECT p.*, u.display_name AS editor_name
                FROM progress p
                LEFT JOIN users u ON u.id = p.updated_by
                WHERE p.task_id IN ({placeholders})
                """,
                task_ids,
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


def delete_organization(db, org_id):
    users = db.execute("SELECT id FROM users WHERE organization_id = ?", (org_id,)).fetchall()
    user_ids = [row["id"] for row in users]
    cat_ids = [row["id"] for row in db.execute("SELECT id FROM categories WHERE organization_id = ?", (org_id,)).fetchall()]
    task_ids = []
    if cat_ids:
        placeholders = ",".join("?" * len(cat_ids))
        task_ids = [
            row["id"]
            for row in db.execute(f"SELECT id FROM tasks WHERE category_id IN ({placeholders})", cat_ids).fetchall()
        ]
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        db.execute(f"DELETE FROM progress WHERE task_id IN ({placeholders})", task_ids)
        db.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
    if cat_ids:
        placeholders = ",".join("?" * len(cat_ids))
        db.execute(f"DELETE FROM categories WHERE id IN ({placeholders})", cat_ids)
    if user_ids:
        placeholders = ",".join("?" * len(user_ids))
        db.execute(f"UPDATE progress SET updated_by = NULL WHERE updated_by IN ({placeholders})", user_ids)
        db.execute(f"DELETE FROM weekly_notes WHERE user_id IN ({placeholders})", user_ids)
        db.execute(f"DELETE FROM join_requests WHERE user_id IN ({placeholders})", user_ids)
        db.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    db.execute("DELETE FROM join_requests WHERE organization_id = ?", (org_id,))
    db.execute("DELETE FROM organizations WHERE id = ?", (org_id,))


@app.get("/api/superadmin/overview")
@superadmin_required
def api_superadmin_overview():
    db = get_db()
    expire_stale_join_requests(db)
    orgs = db.execute("SELECT id, name, created_at, status FROM organizations ORDER BY id").fetchall()
    organizations = []
    suspended_count = 0
    for org in orgs:
        org_status = org["status"] or "active"
        if org_status == "suspended":
            suspended_count += 1
        users = [
            {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "status": row["status"] or "active",
                "is_admin": bool(row["is_admin"]),
                "created_at": row["created_at"],
            }
            for row in db.execute(
                """
                SELECT id, username, display_name, status, is_admin, created_at
                FROM users
                WHERE organization_id = ? AND COALESCE(is_superadmin, 0) = 0
                ORDER BY is_admin DESC, id
                """,
                (org["id"],),
            ).fetchall()
        ]
        task_count = db.execute(
            """
            SELECT COUNT(*) FROM tasks t
            JOIN categories c ON c.id = t.category_id
            WHERE c.organization_id = ?
            """,
            (org["id"],),
        ).fetchone()[0]
        active_count = sum(1 for u in users if u["status"] == "active")
        pending_count = sum(1 for u in users if u["status"] == "pending")
        admin_count = sum(1 for u in users if u["is_admin"] and u["status"] == "active")
        organizations.append(
            {
                "id": org["id"],
                "name": org["name"],
                "created_at": org["created_at"],
                "status": org_status,
                "user_count": len(users),
                "active_count": active_count,
                "pending_count": pending_count,
                "admin_count": admin_count,
                "task_count": task_count,
                "users": users,
            }
        )
    user_total = db.execute(
        "SELECT COUNT(*) FROM users WHERE COALESCE(is_superadmin, 0) = 0"
    ).fetchone()[0]
    active_total = db.execute(
        "SELECT COUNT(*) FROM users WHERE COALESCE(is_superadmin, 0) = 0 AND status = 'active'"
    ).fetchone()[0]
    pending_total = db.execute(
        "SELECT COUNT(*) FROM users WHERE COALESCE(is_superadmin, 0) = 0 AND status = 'pending'"
    ).fetchone()[0]
    task_total = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return jsonify(
        {
            "stats": {
                "organizations": len(organizations),
                "suspended_organizations": suspended_count,
                "users": user_total,
                "active_users": active_total,
                "pending_users": pending_total,
                "tasks": task_total,
            },
            "organizations": organizations,
        }
    )


@app.post("/api/superadmin/organizations/<int:org_id>/status")
@superadmin_required
def api_superadmin_set_organization_status(org_id):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in {"active", "suspended"}:
        return jsonify({"error": "无效的组织状态"}), 400
    db = get_db()
    row = db.execute("SELECT id, name, status FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        return jsonify({"error": "组织不存在"}), 404
    db.execute("UPDATE organizations SET status = ? WHERE id = ?", (status, org_id))
    db.commit()
    return jsonify({"ok": True, "id": org_id, "name": row["name"], "status": status})


@app.post("/api/superadmin/users/<int:user_id>/reset-password")
@superadmin_required
def api_superadmin_reset_admin_password(user_id):
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 位"}), 400
    db = get_db()
    row = db.execute(
        """
        SELECT id, username, is_admin, is_superadmin, organization_id, status
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row or row["is_superadmin"]:
        return jsonify({"error": "用户不存在"}), 404
    if not row["is_admin"]:
        return jsonify({"error": "只能重置组织管理员的密码"}), 400
    if not row["organization_id"]:
        return jsonify({"error": "该帐号未归属组织"}), 400
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    db.commit()
    return jsonify({"ok": True, "username": row["username"]})


@app.delete("/api/superadmin/organizations/<int:org_id>")
@superadmin_required
def api_superadmin_delete_organization(org_id):
    db = get_db()
    row = db.execute("SELECT id, name FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        return jsonify({"error": "组织不存在"}), 404
    delete_organization(db, org_id)
    db.commit()
    return jsonify({"ok": True, "name": row["name"]})


init_db()


if __name__ == "__main__":
    host = os.environ.get("CHAOJIAN_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAOJIAN_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
