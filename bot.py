import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db

# ─── Вставьте сюда токен от @BotFather ───────────────────────────────────────
BOT_TOKEN = "8728636610:AAG7HY8FazTDkT1x2Po9XQ2oYKlyo_zBYwQ"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class S(StatesGroup):
    selecting_category = State()       # ждём выбора категории для расхода
    cat_emoji = State()                # ввод эмодзи (отдельное добавление категории)
    cat_name = State()                 # ввод названия (отдельное добавление категории)
    expense_cat_emoji = State()        # ввод эмодзи (новая категория во время записи расхода)
    expense_cat_name = State()         # ввод названия (новая категория во время записи расхода)


# ── Парсинг сообщения вида "кофе 300" ────────────────────────────────────────

def parse_expense(text: str) -> tuple[str | None, float | None]:
    parts = text.strip().rsplit(None, 1)
    if len(parts) != 2:
        return None, None
    try:
        amount = float(parts[1].replace(",", "."))
        if amount <= 0:
            return None, None
        return parts[0].strip(), amount
    except ValueError:
        return None, None


# ── Клавиатура с категориями ──────────────────────────────────────────────────

async def make_categories_kb(user_id: int) -> types.InlineKeyboardMarkup:
    cats = await db.get_categories(user_id)
    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=f"{cat['emoji']} {cat['name']}", callback_data=f"cat:{cat['id']}")
    builder.adjust(2)
    builder.row(
        types.InlineKeyboardButton(text="➕ Своя категория", callback_data="newcat"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    return builder.as_markup()


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я помогу вести учёт расходов.\n\n"
        "Просто напиши что потратил и сколько:\n"
        "<code>кофе 300</code>\n"
        "<code>такси 500</code>\n"
        "<code>продукты 2500</code>\n\n"
        "📊 /stats — статистика\n"
        "📋 /history — последние расходы\n"
        "📂 /categories — категории\n"
        "❌ /cancel — отменить текущее действие",
        parse_mode="HTML",
    )


# ── /cancel ───────────────────────────────────────────────────────────────────

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.")


# ── /stats ────────────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    lines = ["📊 <b>Статистика расходов</b>\n"]

    for period, label in [("today", "Сегодня"), ("week", "Эта неделя"), ("month", "Этот месяц")]:
        stats = await db.get_stats(user_id, period)
        if stats:
            lines.append(f"<b>{label}: {stats['total']:.0f} ₽</b>")
            for row in stats["by_category"]:
                lines.append(f"  {row['emoji']} {row['name']}: {row['amount']:.0f} ₽")
            lines.append("")

    if len(lines) == 1:
        lines.append("Расходов пока нет.")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /history ──────────────────────────────────────────────────────────────────

@dp.message(Command("history"))
async def cmd_history(message: types.Message):
    expenses = await db.get_recent(message.from_user.id)
    if not expenses:
        await message.answer("📭 Расходов пока нет.")
        return

    lines = ["📋 <b>Последние расходы:</b>\n"]
    for e in expenses:
        dt = datetime.fromisoformat(e["created_at"]).strftime("%d.%m %H:%M")
        lines.append(f"{e['emoji']} {e['comment']} — <b>{e['amount']:.0f} ₽</b>  <i>{dt}</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /categories ───────────────────────────────────────────────────────────────

@dp.message(Command("categories"))
async def cmd_categories(message: types.Message):
    cats = await db.get_categories(message.from_user.id)
    lines = ["📂 <b>Ваши категории:</b>\n"]
    for cat in cats:
        lines.append(f"{cat['emoji']} {cat['name']}")

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить категорию", callback_data="add_cat")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


# ── Добавление категории из меню /categories ──────────────────────────────────

@dp.callback_query(F.data == "add_cat")
async def cb_add_cat(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите эмодзи для новой категории (например 🎯):")
    await state.set_state(S.cat_emoji)
    await callback.answer()


@dp.message(S.cat_emoji)
async def process_cat_emoji(message: types.Message, state: FSMContext):
    await state.update_data(emoji=message.text.strip())
    await message.answer("Теперь введите название категории:")
    await state.set_state(S.cat_name)


@dp.message(S.cat_name)
async def process_cat_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    emoji = data.get("emoji", "📌")
    name = message.text.strip()
    await db.add_category(message.from_user.id, name, emoji)
    await state.clear()
    await message.answer(f"✅ Категория {emoji} {name} добавлена!")


# ── Новая категория прямо во время записи расхода ─────────────────────────────

@dp.callback_query(F.data == "newcat")
async def cb_newcat(callback: types.CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current != S.selecting_category:
        await callback.answer("Сначала отправь расход.", show_alert=True)
        return
    await callback.message.answer("Введите эмодзи для новой категории (например 🎯):")
    await state.set_state(S.expense_cat_emoji)
    await callback.answer()


@dp.message(S.expense_cat_emoji)
async def process_expense_cat_emoji(message: types.Message, state: FSMContext):
    await state.update_data(emoji=message.text.strip())
    await message.answer("Введите название категории:")
    await state.set_state(S.expense_cat_name)


@dp.message(S.expense_cat_name)
async def process_expense_cat_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    emoji = data.get("emoji", "📌")
    cat_name = message.text.strip()
    user_id = message.from_user.id

    cat_id = await db.add_category(user_id, cat_name, emoji)
    await db.add_expense(user_id, cat_id, data["amount"], data["name"])
    await state.clear()

    await message.answer(
        f"✅ Записано!\n\n"
        f"{emoji} {cat_name}\n"
        f"💰 {data['amount']:.0f} ₽\n"
        f"📝 {data['name']}"
    )


# ── Выбор категории кнопкой ───────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("cat:"))
async def cb_select_cat(callback: types.CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current != S.selecting_category:
        await callback.answer("Это сообщение устарело. Отправь расход заново.", show_alert=True)
        return

    cat_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    user_id = callback.from_user.id

    cat = await db.get_category(user_id, cat_id)
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    await db.add_expense(user_id, cat_id, data["amount"], data["name"])
    await state.clear()

    await callback.message.edit_text(
        f"✅ Записано!\n\n"
        f"{cat['emoji']} {cat['name']}\n"
        f"💰 {data['amount']:.0f} ₽\n"
        f"📝 {data['name']}"
    )
    await callback.answer()


# ── Отмена из inline-кнопки ───────────────────────────────────────────────────

@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()


# ── Главный обработчик текста: парсим расход ──────────────────────────────────

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    # Пока идёт ввод (эмодзи/название категории) — игнорируем текст
    if await state.get_state() is not None:
        return

    name, amount = parse_expense(message.text)
    if name is None:
        await message.answer(
            "❓ Не понял. Напиши расход в формате:\n<code>кофе 300</code>",
            parse_mode="HTML",
        )
        return

    await db.ensure_user(message.from_user.id)
    await state.set_state(S.selecting_category)
    await state.update_data(name=name, amount=amount)

    kb = await make_categories_kb(message.from_user.id)
    await message.answer(
        f"📝 <b>{name}</b> — {amount:.0f} ₽\n\nВыбери категорию:",
        reply_markup=kb,
        parse_mode="HTML",
    )


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
