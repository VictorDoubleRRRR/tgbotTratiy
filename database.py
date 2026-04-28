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
