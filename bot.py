import asyncio
import csv
import io
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db

BOT_TOKEN = "8728636610:AAG7HY8FazTDkT1x2Po9XQ2oYKlyo_zBYwQ"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class S(StatesGroup):
    # запись расхода
    selecting_category = State()
    expense_cat_emoji  = State()
    expense_cat_name   = State()
    # добавление категории из меню
    cat_emoji = State()
    cat_name  = State()
    # лимиты
    limit_select_cat = State()
    limit_amount     = State()
    # регулярные
    rec_select_cat = State()
    rec_amount     = State()
    rec_comment    = State()
    rec_day        = State()


# ── Утилиты ───────────────────────────────────────────────────────────────────

def parse_expense(text: str) -> tuple[str | None, float | None]:
    parts = text.strip().rsplit(None, 1)
    if len(parts) != 2:
        return None, None
    try:
        amount = float(parts[1].replace(",", "."))
        return (parts[0].strip(), amount) if amount > 0 else (None, None)
    except ValueError:
        return None, None


async def make_categories_kb(user_id: int, prefix: str = "cat") -> types.InlineKeyboardMarkup:
    cats = await db.get_categories(user_id)
    builder = InlineKeyboardBuilder()
    for cat in cats:
        builder.button(text=f"{cat['emoji']} {cat['name']}", callback_data=f"{prefix}:{cat['id']}")
    builder.adjust(2)
    if prefix == "cat":
        builder.row(
            types.InlineKeyboardButton(text="➕ Своя категория", callback_data="newcat"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
        )
    else:
        builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


async def warn_limit(user_id: int, category_id: int):
    """Отправляет предупреждение если лимит на исходе или превышен."""
    info = await db.check_category_limit(user_id, category_id)
    if not info:
        return
    pct = info["pct"]
    if pct >= 100:
        await bot.send_message(
            user_id,
            f"🚨 <b>Лимит превышен!</b>\n"
            f"Потрачено <b>{info['spent']:.0f} ₽</b> из {info['limit']:.0f} ₽ ({pct:.0f}%)",
            parse_mode="HTML",
        )
    elif pct >= 80:
        left = info["limit"] - info["spent"]
        await bot.send_message(
            user_id,
            f"⚠️ <b>Лимит на исходе!</b>\n"
            f"Осталось <b>{left:.0f} ₽</b> из {info['limit']:.0f} ₽ ({pct:.0f}%)",
            parse_mode="HTML",
        )


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я помогу вести учёт расходов.\n\n"
        "Просто напиши что потратил и сколько:\n"
        "<code>кофе 300</code>  <code>такси 500</code>\n\n"
        "📊 /stats — статистика\n"
        "📋 /history — последние расходы\n"
        "📂 /categories — категории\n"
        "🔔 /limits — лимиты по категориям\n"
        "🔁 /recurring — регулярные расходы\n"
        "📤 /export — выгрузить всё в CSV\n"
        "❌ /cancel — отменить действие",
        parse_mode="HTML",
    )


# ── /cancel ───────────────────────────────────────────────────────────────────

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено.")


@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()


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
    lines = ["📂 <b>Ваши категории:</b>\n"] + [f"{c['emoji']} {c['name']}" for c in cats]
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить категорию", callback_data="add_cat")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=builder.as_markup())


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
    emoji, name = data.get("emoji", "📌"), message.text.strip()
    await db.add_category(message.from_user.id, name, emoji)
    await state.clear()
    await message.answer(f"✅ Категория {emoji} {name} добавлена!")


# ── Запись расхода ────────────────────────────────────────────────────────────

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
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
        reply_markup=kb, parse_mode="HTML",
    )


@dp.callback_query(F.data == "newcat")
async def cb_newcat(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() != S.selecting_category:
        await callback.answer("Сначала отправь расход.", show_alert=True)
        return
    await callback.message.answer("Введите эмодзи для новой категории:")
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
    emoji, cat_name = data.get("emoji", "📌"), message.text.strip()
    user_id = message.from_user.id
    cat_id = await db.add_category(user_id, cat_name, emoji)
    await db.add_expense(user_id, cat_id, data["amount"], data["name"])
    await state.clear()
    await message.answer(
        f"✅ Записано!\n\n{emoji} {cat_name}\n💰 {data['amount']:.0f} ₽\n📝 {data['name']}"
    )
    await warn_limit(user_id, cat_id)


@dp.callback_query(F.data.startswith("cat:"))
async def cb_select_cat(callback: types.CallbackQuery, state: FSMContext):
    if await state.get_state() != S.selecting_category:
        await callback.answer("Сообщение устарело. Отправь расход заново.", show_alert=True)
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
        f"✅ Записано!\n\n{cat['emoji']} {cat['name']}\n💰 {data['amount']:.0f} ₽\n📝 {data['name']}"
    )
    await callback.answer()
    await warn_limit(user_id, cat_id)


# ── /limits ───────────────────────────────────────────────────────────────────

@dp.message(Command("limits"))
async def cmd_limits(message: types.Message):
    user_id = message.from_user.id
    await db.ensure_user(user_id)
    limits = await db.get_limits_with_spending(user_id)

    builder = InlineKeyboardBuilder()
    if limits:
        lines = ["🔔 <b>Лимиты на этот месяц:</b>\n"]
        for lim in limits:
            pct = lim["spent"] / lim["monthly_amount"] * 100
            bar = "🟢" if pct < 80 else ("🟡" if pct < 100 else "🔴")
            lines.append(
                f"{bar} {lim['emoji']} {lim['name']}: "
                f"{lim['spent']:.0f} / {lim['monthly_amount']:.0f} ₽ ({pct:.0f}%)"
            )
            builder.button(
                text=f"❌ {lim['emoji']} {lim['name']}",
                callback_data=f"del_limit:{lim['category_id']}",
            )
        builder.adjust(1)
        text = "\n".join(lines)
    else:
        text = "🔔 <b>Лимиты</b>\n\nЛимитов пока нет."

    builder.row(types.InlineKeyboardButton(text="➕ Добавить лимит", callback_data="add_limit"))
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "add_limit")
async def cb_add_limit(callback: types.CallbackQuery, state: FSMContext):
    kb = await make_categories_kb(callback.from_user.id, prefix="lcat")
    await callback.message.answer("Выбери категорию для лимита:", reply_markup=kb)
    await state.set_state(S.limit_select_cat)
    await callback.answer()


@dp.callback_query(F.data.startswith("lcat:"), S.limit_select_cat)
async def cb_limit_cat(callback: types.CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split(":")[1])
    cat = await db.get_category(callback.from_user.id, cat_id)
    await state.update_data(cat_id=cat_id, cat_name=cat["name"], cat_emoji=cat["emoji"])
    await callback.message.answer(
        f"Введи месячный лимит для {cat['emoji']} <b>{cat['name']}</b> (в рублях):",
        parse_mode="HTML",
    )
    await state.set_state(S.limit_amount)
    await callback.answer()


@dp.message(S.limit_amount)
async def process_limit_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Введи число больше нуля:")
        return
    data = await state.get_data()
    await db.set_limit(message.from_user.id, data["cat_id"], amount)
    await state.clear()
    await message.answer(
        f"✅ Лимит установлен!\n{data['cat_emoji']} {data['cat_name']}: <b>{amount:.0f} ₽/мес</b>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("del_limit:"))
async def cb_del_limit(callback: types.CallbackQuery):
    cat_id = int(callback.data.split(":")[1])
    await db.delete_limit(callback.from_user.id, cat_id)
    await callback.answer("Лимит удалён ✅", show_alert=False)
    # обновить список
    await cmd_limits_refresh(callback)


async def cmd_limits_refresh(callback: types.CallbackQuery):
    """Перерисовывает сообщение с лимитами после удаления."""
    user_id = callback.from_user.id
    limits = await db.get_limits_with_spending(user_id)
    builder = InlineKeyboardBuilder()
    if limits:
        lines = ["🔔 <b>Лимиты на этот месяц:</b>\n"]
        for lim in limits:
            pct = lim["spent"] / lim["monthly_amount"] * 100
            bar = "🟢" if pct < 80 else ("🟡" if pct < 100 else "🔴")
            lines.append(
                f"{bar} {lim['emoji']} {lim['name']}: "
                f"{lim['spent']:.0f} / {lim['monthly_amount']:.0f} ₽ ({pct:.0f}%)"
            )
            builder.button(
                text=f"❌ {lim['emoji']} {lim['name']}",
                callback_data=f"del_limit:{lim['category_id']}",
            )
        builder.adjust(1)
        text = "\n".join(lines)
    else:
        text = "🔔 <b>Лимиты</b>\n\nЛимитов пока нет."
    builder.row(types.InlineKeyboardButton(text="➕ Добавить лимит", callback_data="add_limit"))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ── /recurring ────────────────────────────────────────────────────────────────

@dp.message(Command("recurring"))
async def cmd_recurring(message: types.Message):
    user_id = message.from_user.id
    await db.ensure_user(user_id)
    recs = await db.get_recurring(user_id)

    builder = InlineKeyboardBuilder()
    if recs:
        lines = ["🔁 <b>Регулярные расходы:</b>\n"]
        for r in recs:
            lines.append(f"{r['emoji']} {r['comment']} — {r['amount']:.0f} ₽  (каждое {r['day_of_month']}-е)")
            builder.button(text=f"❌ {r['comment']}", callback_data=f"del_rec:{r['id']}")
        builder.adjust(1)
        text = "\n".join(lines)
    else:
        text = "🔁 <b>Регулярные расходы</b>\n\nПока ничего нет."

    builder.row(types.InlineKeyboardButton(text="➕ Добавить", callback_data="add_rec"))
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@dp.callback_query(F.data == "add_rec")
async def cb_add_rec(callback: types.CallbackQuery, state: FSMContext):
    kb = await make_categories_kb(callback.from_user.id, prefix="rcat")
    await callback.message.answer("Выбери категорию:", reply_markup=kb)
    await state.set_state(S.rec_select_cat)
    await callback.answer()


@dp.callback_query(F.data.startswith("rcat:"), S.rec_select_cat)
async def cb_rec_cat(callback: types.CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split(":")[1])
    cat = await db.get_category(callback.from_user.id, cat_id)
    await state.update_data(cat_id=cat_id, cat_name=cat["name"], cat_emoji=cat["emoji"])
    await callback.message.answer(f"Сумма для {cat['emoji']} <b>{cat['name']}</b>:", parse_mode="HTML")
    await state.set_state(S.rec_amount)
    await callback.answer()


@dp.message(S.rec_amount)
async def process_rec_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Введи число больше нуля:")
        return
    await state.update_data(amount=amount)
    await message.answer("Как назвать этот расход? (например: Netflix, Аренда, Абонемент):")
    await state.set_state(S.rec_comment)


@dp.message(S.rec_comment)
async def process_rec_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text.strip())
    await message.answer("Какого числа каждого месяца напоминать? (введи число от 1 до 28):")
    await state.set_state(S.rec_day)


@dp.message(S.rec_day)
async def process_rec_day(message: types.Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 28:
            raise ValueError
    except ValueError:
        await message.answer("❗ Введи число от 1 до 28:")
        return
    data = await state.get_data()
    await db.add_recurring(
        message.from_user.id, data["cat_id"], data["amount"], data["comment"], day
    )
    await state.clear()
    await message.answer(
        f"✅ Добавлено!\n\n{data['cat_emoji']} {data['comment']}\n"
        f"💰 {data['amount']:.0f} ₽ — напоминание {day}-го числа каждого месяца",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("del_rec:"))
async def cb_del_rec(callback: types.CallbackQuery):
    rec_id = int(callback.data.split(":")[1])
    await db.delete_recurring(callback.from_user.id, rec_id)
    await callback.answer("Удалено ✅")
    recs = await db.get_recurring(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    if recs:
        lines = ["🔁 <b>Регулярные расходы:</b>\n"]
        for r in recs:
            lines.append(f"{r['emoji']} {r['comment']} — {r['amount']:.0f} ₽  (каждое {r['day_of_month']}-е)")
            builder.button(text=f"❌ {r['comment']}", callback_data=f"del_rec:{r['id']}")
        builder.adjust(1)
        text = "\n".join(lines)
    else:
        text = "🔁 <b>Регулярные расходы</b>\n\nПока ничего нет."
    builder.row(types.InlineKeyboardButton(text="➕ Добавить", callback_data="add_rec"))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


# Кнопка «Записать» из напоминания о регулярном расходе
@dp.callback_query(F.data.startswith("rec_add:"))
async def cb_rec_add(callback: types.CallbackQuery):
    rec_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    recs = await db.get_recurring(user_id)
    rec = next((r for r in recs if r["id"] == rec_id), None)
    if not rec:
        await callback.answer("Не найдено.", show_alert=True)
        return
    await db.add_expense(user_id, rec["category_id"], rec["amount"], rec["comment"])
    await callback.message.edit_text(
        f"✅ Записано!\n\n{rec['emoji']} {rec['comment']}\n💰 {rec['amount']:.0f} ₽"
    )
    await callback.answer()
    await warn_limit(user_id, rec["category_id"])


@dp.callback_query(F.data.startswith("rec_skip:"))
async def cb_rec_skip(callback: types.CallbackQuery):
    await callback.message.edit_text("⏭ Пропущено.")
    await callback.answer()


# ── /export ───────────────────────────────────────────────────────────────────

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    expenses = await db.get_all_expenses(message.from_user.id)
    if not expenses:
        await message.answer("📭 Нет данных для экспорта.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Дата", "Категория", "Описание", "Сумма (₽)"])
    for e in expenses:
        dt = datetime.fromisoformat(e["created_at"]).strftime("%d.%m.%Y %H:%M")
        writer.writerow([dt, f"{e['emoji']} {e['category']}", e["comment"], e["amount"]])

    filename = f"expenses_{datetime.now().strftime('%Y%m%d')}.csv"
    file_bytes = buf.getvalue().encode("utf-8-sig")  # utf-8-sig = Excel открывает без кракозябр
    await message.answer_document(
        types.BufferedInputFile(file_bytes, filename=filename),
        caption=f"📤 Экспорт расходов — {len(expenses)} записей",
    )


# ── Фоновые задачи ────────────────────────────────────────────────────────────

async def background_tasks():
    """Каждый час проверяет: напоминания о регулярных и месячный отчёт."""
    while True:
        await asyncio.sleep(3600)
        now = datetime.now()

        # — Напоминания о регулярных расходах (в 9:00) —
        if now.hour == 9:
            due = await db.get_recurring_due_today()
            for rec in due:
                builder = InlineKeyboardBuilder()
                builder.button(text="✅ Записать", callback_data=f"rec_add:{rec['id']}")
                builder.button(text="⏭ Пропустить", callback_data=f"rec_skip:{rec['id']}")
                try:
                    await bot.send_message(
                        rec["user_id"],
                        f"🔁 <b>Напоминание о регулярном расходе</b>\n\n"
                        f"{rec['emoji']} {rec['comment']}\n💰 {rec['amount']:.0f} ₽",
                        parse_mode="HTML",
                        reply_markup=builder.as_markup(),
                    )
                except Exception:
                    pass

        # — Месячный отчёт 1-го числа в 9:00 —
        if now.day == 1 and now.hour == 9:
            # отчёт за прошлый месяц
            if now.month == 1:
                rep_year, rep_month = now.year - 1, 12
            else:
                rep_year, rep_month = now.year, now.month - 1

            year_month = f"{rep_year}-{rep_month:02d}"
            users = await db.get_all_users()
            for user_id in users:
                last = await db.get_last_report_month(user_id)
                if last == year_month:
                    continue
                stats = await db.get_monthly_stats(user_id, rep_year, rep_month)
                if not stats:
                    continue
                month_name = [
                    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
                ][rep_month]
                lines = [f"📈 <b>Итоги за {month_name} {rep_year}</b>\n",
                         f"💸 Всего потрачено: <b>{stats['total']:.0f} ₽</b>\n",
                         "<b>По категориям:</b>"]
                for row in stats["by_category"]:
                    pct = row["amount"] / stats["total"] * 100
                    lines.append(f"  {row['emoji']} {row['name']}: {row['amount']:.0f} ₽  ({pct:.0f}%)")
                if stats["top"]:
                    t = stats["top"]
                    lines.append(f"\n🏆 Самая крупная трата: {t['emoji']} {t['comment']} — {t['amount']:.0f} ₽")
                try:
                    await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
                    await db.set_last_report_month(user_id, year_month)
                except Exception:
                    pass


# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    await db.init_db()
    asyncio.create_task(background_tasks())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
