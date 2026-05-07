import os
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from database import Database
from ocr import extract_amount_from_image

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

# ─── Categories ──────────────────────────────────────────────────────────────
CATEGORIES = {
    '1': '🛒 Продукты',
    '2': '🍽️ Кафе / Еда на вынос',
    '3': '🚗 Транспорт / Бензин',
    '4': '🏠 Дом / Коммунальные',
    '5': '💊 Здоровье / Аптека',
    '6': '🎉 Развлечения',
    '7': '👗 Одежда',
    '8': '📦 Другое',
}

MONTH_NAMES_RU = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}

db = Database()


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _category_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, label in CATEGORIES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}|{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _expense_preview(amount: float, description: str) -> str:
    desc_line = f"\n📝 *Описание:* {description}" if description else ""
    return f"💰 *Сумма:* {amount:.2f} CAD{desc_line}\n\nВыберите категорию:"


# ─── Command Handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет! Я трекер расходов.*\n\n"
        "*Как добавить расход:*\n"
        "• Напишите сумму: `250` или `250 магнит`\n"
        "• Команда: `/add 250 магнит`\n"
        "• Отправьте 📸 фото чека — прочитаю сумму сам\n\n"
        "*Команды:*\n"
        "/add — добавить расход\n"
        "/summary — итоги за месяц\n"
        "/last — последние 10 расходов\n"
        "/undo — отменить последний расход\n"
        "/categories — список категорий\n"
        "/help — помощь",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📋 *Категории:*\n\n" + "\n".join(f"{k}. {v}" for k, v in CATEGORIES.items())
    await update.message.reply_text(text, parse_mode='Markdown')


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/add <amount> [description]"""
    if not context.args:
        await update.message.reply_text(
            "❌ Использование: `/add <сумма> [описание]`\nПример: `/add 250 магнит`",
            parse_mode='Markdown'
        )
        return

    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text("❌ Неверная сумма.", parse_mode='Markdown')
        return

    description = ' '.join(context.args[1:])
    _store_pending(context, amount, description)

    await update.message.reply_text(
        _expense_preview(amount, description),
        reply_markup=_category_keyboard('cat'),
        parse_mode='Markdown'
    )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    rows = db.get_monthly_summary()
    total = db.get_total_for_month()

    if not rows:
        await update.message.reply_text("📊 В этом месяце расходов пока нет.")
        return

    text = f"📊 *Расходы за {MONTH_NAMES_RU[now.month]} {now.year}:*\n\n"
    for category, cat_total, count in rows:
        pct = (cat_total / total * 100) if total else 0
        text += f"{category}\n  `{cat_total:.2f} CAD` ({pct:.0f}%) — {count} оп.\n\n"

    text += f"━━━━━━━━━━━━━━\n💳 *Итого: {total:.2f} CAD*"
    await update.message.reply_text(text, parse_mode='Markdown')


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_recent_expenses(10)
    if not rows:
        await update.message.reply_text("📋 Расходов пока нет.")
        return

    text = "📋 *Последние расходы:*\n\n"
    for amount, category, description, date in rows:
        desc = f" — {description}" if description else ""
        text += f"• {date} | `{amount:.2f} CAD` | {category}{desc}\n"

    await update.message.reply_text(text, parse_mode='Markdown')


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    deleted = db.delete_last(user.id)
    if deleted:
        await update.message.reply_text("↩️ Последний расход удалён.")
    else:
        await update.message.reply_text("❌ Нечего удалять.")


# ─── Message Handlers ─────────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # If we're waiting for a manual amount after failed OCR
    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text(
                "❌ Введите только число, например: `45.50`", parse_mode='Markdown'
            )
            return
        context.user_data.pop('waiting_for_amount', None)
        _store_pending(context, amount, 'Чек (фото)')
        await update.message.reply_text(
            _expense_preview(amount, 'Чек (фото)'),
            reply_markup=_category_keyboard('cat'),
            parse_mode='Markdown'
        )
        return

    # Natural text: "250" or "250 магнит"
    import re
    match = re.match(r'^(\d+(?:[.,]\d+)?)\s*(.*)$', text)
    if not match:
        return  # Not an expense entry — ignore

    try:
        amount = float(match.group(1).replace(',', '.'))
    except ValueError:
        return

    description = match.group(2).strip()
    _store_pending(context, amount, description)

    await update.message.reply_text(
        _expense_preview(amount, description),
        reply_markup=_category_keyboard('cat'),
        parse_mode='Markdown'
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("📸 Читаю чек, подождите…")

    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    amount = extract_amount_from_image(photo_bytes)

    if amount:
        _store_pending(context, amount, 'Чек (фото)')
        await status_msg.edit_text(
            f"✅ *Сумма найдена:* {amount:.2f} CAD\n\nВыберите категорию:",
            reply_markup=_category_keyboard('cat'),
            parse_mode='Markdown'
        )
    else:
        context.user_data['waiting_for_amount'] = True
        await status_msg.edit_text(
            "❌ Не удалось прочитать сумму автоматически.\n\n"
            "Введите сумму вручную (например: `45.50`):",
            parse_mode='Markdown'
        )


# ─── Callback Handler ─────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split('|')
    action, cat_key = parts[0], parts[1]

    if action == 'cat':
        category = CATEGORIES.get(cat_key, '📦 Другое')
        pending = context.user_data.get('pending')

        if not pending:
            await query.edit_message_text("❌ Сессия истекла. Начните заново.")
            return

        user = query.from_user
        db.add_expense(
            user_id=user.id,
            username=user.username or user.first_name,
            amount=pending['amount'],
            category=category,
            description=pending['description'],
            date=pending['date'],
        )
        context.user_data.pop('pending', None)

        await query.edit_message_text(
            f"✅ *Сохранено!*\n\n"
            f"💰 {pending['amount']:.2f} CAD\n"
            f"🏷️ {category}\n"
            f"📝 {pending['description'] or '—'}\n"
            f"📅 {pending['date']}",
            parse_mode='Markdown'
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _store_pending(context: ContextTypes.DEFAULT_TYPE, amount: float, description: str):
    context.user_data['pending'] = {
        'amount': amount,
        'description': description,
        'date': datetime.now().strftime('%Y-%m-%d'),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start',      start))
    app.add_handler(CommandHandler('help',       help_command))
    app.add_handler(CommandHandler('add',        add_command))
    app.add_handler(CommandHandler('summary',    summary_command))
    app.add_handler(CommandHandler('last',       last_command))
    app.add_handler(CommandHandler('undo',       undo_command))
    app.add_handler(CommandHandler('categories', categories_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running…")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
