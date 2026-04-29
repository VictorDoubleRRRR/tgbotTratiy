import aiosqlite
from datetime import datetime, timedelta

DB_PATH = "expenses.db"

DEFAULT_CATEGORIES = [
    ("Еда", "🍕"),
    ("Транспорт", "🚕"),
    ("Жильё", "🏠"),
    ("Одежда", "👕"),
    ("Здоровье", "💊"),
    ("Развлечения", "🎮"),
    ("Связь", "📱"),
    ("Спорт", "💪"),
    ("Образование", "📚"),
    ("Другое", "🛒"),
]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                emoji      TEXT NOT NULL DEFAULT '📌',
                is_default INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                amount      REAL NOT NULL,
                comment     TEXT,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS limits (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                category_id    INTEGER NOT NULL,
                monthly_amount REAL NOT NULL,
                UNIQUE(user_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS recurring (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                category_id  INTEGER NOT NULL,
                amount       REAL NOT NULL,
                comment      TEXT NOT NULL,
                day_of_month INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_log (
                user_id     INTEGER PRIMARY KEY,
                last_report TEXT
            );
        """)
        await conn.commit()


async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        if await cur.fetchone():
            return
        await conn.execute("INSERT INTO users VALUES (?)", (user_id,))
        for name, emoji in DEFAULT_CATEGORIES:
            await conn.execute(
                "INSERT INTO categories (user_id, name, emoji, is_default) VALUES (?, ?, ?, 1)",
                (user_id, name, emoji),
            )
        await conn.commit()


async def get_categories(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, name, emoji FROM categories WHERE user_id = ? ORDER BY is_default DESC, id",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_category(user_id: int, cat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, name, emoji FROM categories WHERE user_id = ? AND id = ?",
            (user_id, cat_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def add_category(user_id: int, name: str, emoji: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO categories (user_id, name, emoji) VALUES (?, ?, ?)",
            (user_id, name, emoji),
        )
        await conn.commit()
        return cur.lastrowid


async def add_expense(user_id: int, category_id: int, amount: float, comment: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO expenses (user_id, category_id, amount, comment) VALUES (?, ?, ?, ?)",
            (user_id, category_id, amount, comment),
        )
        await conn.commit()


async def get_recent(user_id: int, limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT e.amount, e.comment, e.created_at, c.name, c.emoji
            FROM expenses e JOIN categories c ON e.category_id = c.id
            WHERE e.user_id = ?
            ORDER BY e.created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_stats(user_id: int, period: str) -> dict | None:
    now = datetime.now()
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None

    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = ? AND created_at >= ?",
            (user_id, since_str),
        )
        total = (await cur.fetchone())["total"]
        if not total:
            return None

        cur = await conn.execute(
            """
            SELECT c.name, c.emoji, SUM(e.amount) as amount
            FROM expenses e JOIN categories c ON e.category_id = c.id
            WHERE e.user_id = ? AND e.created_at >= ?
            GROUP BY c.id ORDER BY amount DESC
            """,
            (user_id, since_str),
        )
        by_cat = [dict(r) for r in await cur.fetchall()]

        return {"total": total, "by_category": by_cat}


# ── Лимиты ───────────────────────────────────────────────────────────────────

async def set_limit(user_id: int, category_id: int, monthly_amount: float):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO limits (user_id, category_id, monthly_amount)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, category_id) DO UPDATE SET monthly_amount = excluded.monthly_amount""",
            (user_id, category_id, monthly_amount),
        )
        await conn.commit()


async def delete_limit(user_id: int, category_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        await conn.commit()


async def get_limits_with_spending(user_id: int) -> list[dict]:
    """Лимиты + сколько уже потрачено в этом месяце."""
    since = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT l.category_id, l.monthly_amount, c.name, c.emoji,
                   COALESCE(SUM(e.amount), 0) as spent
            FROM limits l
            JOIN categories c ON l.category_id = c.id
            LEFT JOIN expenses e
                ON e.category_id = l.category_id
                AND e.user_id = l.user_id
                AND e.created_at >= ?
            WHERE l.user_id = ?
            GROUP BY l.category_id
            """,
            (since_str, user_id),
        )
        return [dict(r) for r in await cur.fetchall()]


async def check_category_limit(user_id: int, category_id: int) -> dict | None:
    """Возвращает {spent, limit, pct} если лимит есть, иначе None."""
    since = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT monthly_amount FROM limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        limit = row["monthly_amount"]

        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as spent FROM expenses WHERE user_id = ? AND category_id = ? AND created_at >= ?",
            (user_id, category_id, since_str),
        )
        spent = (await cur.fetchone())["spent"]
        return {"spent": spent, "limit": limit, "pct": spent / limit * 100}


# ── Регулярные расходы ────────────────────────────────────────────────────────

async def add_recurring(user_id: int, category_id: int, amount: float, comment: str, day_of_month: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO recurring (user_id, category_id, amount, comment, day_of_month) VALUES (?, ?, ?, ?, ?)",
            (user_id, category_id, amount, comment, day_of_month),
        )
        await conn.commit()
        return cur.lastrowid


async def get_recurring(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT r.id, r.amount, r.comment, r.day_of_month, c.name, c.emoji
            FROM recurring r JOIN categories c ON r.category_id = c.id
            WHERE r.user_id = ?
            ORDER BY r.day_of_month
            """,
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_recurring(user_id: int, rec_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM recurring WHERE id = ? AND user_id = ?",
            (rec_id, user_id),
        )
        await conn.commit()


async def get_recurring_due_today() -> list[dict]:
    """Все напоминания всех пользователей на сегодняшний день месяца."""
    day = datetime.now().day
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT r.id, r.user_id, r.category_id, r.amount, r.comment, c.name, c.emoji
            FROM recurring r JOIN categories c ON r.category_id = c.id
            WHERE r.day_of_month = ?
            """,
            (day,),
        )
        return [dict(r) for r in await cur.fetchall()]


# ── Месячный отчёт ────────────────────────────────────────────────────────────

async def get_monthly_stats(user_id: int, year: int, month: int) -> dict | None:
    since = f"{year}-{month:02d}-01 00:00:00"
    # конец месяца — начало следующего
    if month == 12:
        until = f"{year + 1}-01-01 00:00:00"
    else:
        until = f"{year}-{month + 1:02d}-01 00:00:00"

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = ? AND created_at >= ? AND created_at < ?",
            (user_id, since, until),
        )
        total = (await cur.fetchone())["total"]
        if not total:
            return None

        cur = await conn.execute(
            """
            SELECT c.name, c.emoji, SUM(e.amount) as amount
            FROM expenses e JOIN categories c ON e.category_id = c.id
            WHERE e.user_id = ? AND e.created_at >= ? AND e.created_at < ?
            GROUP BY c.id ORDER BY amount DESC
            """,
            (user_id, since, until),
        )
        by_cat = [dict(r) for r in await cur.fetchall()]

        # топ расход
        cur = await conn.execute(
            """
            SELECT e.amount, e.comment, c.name, c.emoji
            FROM expenses e JOIN categories c ON e.category_id = c.id
            WHERE e.user_id = ? AND e.created_at >= ? AND e.created_at < ?
            ORDER BY e.amount DESC LIMIT 1
            """,
            (user_id, since, until),
        )
        top = await cur.fetchone()

        return {
            "total": total,
            "by_category": by_cat,
            "top": dict(top) if top else None,
        }


async def get_all_users() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


async def get_last_report_month(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT last_report FROM report_log WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def set_last_report_month(user_id: int, year_month: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO report_log (user_id, last_report) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_report = excluded.last_report",
            (user_id, year_month),
        )
        await conn.commit()


# ── Экспорт CSV ───────────────────────────────────────────────────────────────

async def get_all_expenses(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT e.created_at, c.name as category, c.emoji, e.comment, e.amount
            FROM expenses e JOIN categories c ON e.category_id = c.id
            WHERE e.user_id = ?
            ORDER BY e.created_at
            """,
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
