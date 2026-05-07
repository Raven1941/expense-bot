import os
import re
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

from database import Database
from ocr import extract_from_image

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN not set')

db = Database()

# ── Categories ────────────────────────────────────────────────────────────────
EXP_CATEGORIES = {
    '1':  '🛒 Продукты',
    '2':  '🍽️ Кафе / Еда на вынос',
    '3':  '⛽ Авто / Бензин',
    '4':  '🔧 Авто / Обслуживание',
    '5':  '🏠 Дом / Коммунальные',
    '6':  '💊 Здоровье / Аптека',
    '7':  '🧴 Химия',
    '8':  '👧 Мирослава',
    '9':  '✈️ Командировка / Питание',
    '10': '🎉 Развлечения',
    '11': '👗 Одежда',
    '12': '📦 Другое',
}

INC_CATEGORIES = {
    '1': '💼 Зарплата — Евгений',
    '2': '💼 Зарплата — Анастасия',
    '3': '✈️ Суточные / Командировочные',
    '4': '💵 Другой доход',
}

MONTH_RU = {
    1: 'Январь',   2: 'Февраль',  3: 'Март',    4: 'Апрель',
    5: 'Май',      6: 'Июнь',     7: 'Июль',    8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}


# ── Keyboards ─────────────────────────────────────────────────────────────────
def cat_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'c|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def line_cat_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'rlcat|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton('⏭️ Пропустить', callback_data='rl_skip'),
        InlineKeyboardButton('✅ Завершить',   callback_data='rl_done'),
    ])
    return InlineKeyboardMarkup(rows)


def inc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(v, callback_data=f'ic|{k}')] for k, v in INC_CATEGORIES.items()]
    )


def excel_btn(record_type: str, record_id: int, marked: bool) -> InlineKeyboardMarkup:
    label = '✅ В Excel' if marked else '⬜ Не в Excel'
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f'xls_{record_type}|{record_id}')
    ]])


def ocr_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('📋 По строкам',     callback_data='rl_start'),
        InlineKeyboardButton('💰 Одна категория', callback_data='rl_one'),
    ]])


def split_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('➕ Ещё часть', callback_data='split'),
        InlineKeyboardButton('✅ Готово',    callback_data='split_done'),
    ]])


def desc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('⏭️ Пропустить', callback_data='desc_skip')
    ]])


# ── Topic post helpers ────────────────────────────────────────────────────────
async def _send_to_topic(context, topic_key: str, text: str, markup: InlineKeyboardMarkup):
    """
    Post a message to a configured forum topic.
    Returns the sent Message object or None on failure.
    topic_key: 'topic_expenses' or 'topic_income'
    """
    group_id = db.get_config('group_id')
    topic_id = db.get_config(topic_key)

    if not group_id or not topic_id:
        logger.warning(f'{topic_key} not configured — skipping post')
        return None

    tid = int(topic_id)
    if tid <= 0:
        logger.warning(f'{topic_key} has invalid id {tid} — skipping post')
        return None

    try:
        msg = await context.bot.send_message(
            chat_id=int(group_id),
            message_thread_id=tid,
            text=text,
            parse_mode='HTML',
            reply_markup=markup,
        )
        logger.info(f'Posted to {topic_key}, msg_id={msg.message_id}')
        return msg
    except Exception as e:
        logger.error(f'_send_to_topic({topic_key}) error: {e}', exc_info=True)
        return None


async def _post_expense(context, exp_id, amount, category, description, date, username):
    text = (
        f'💸 <b>Расход</b>\n'
        f'💰 <code>{amount:.2f} CAD</code>\n'
        f'🏷 {category}\n'
        f'📝 {description or "—"}\n'
        f'📅 {date}  👤 {username}'
    )
    msg = await _send_to_topic(context, 'topic_expenses', text, excel_btn('e', exp_id, False))
    if msg:
        db.set_expense_message(exp_id, int(db.get_config('group_id')), msg.message_id)


async def _post_income(context, inc_id, amount, category, description, date, username):
    text = (
        f'💰 <b>Доход</b>\n'
        f'💵 <code>{amount:.2f} CAD</code>\n'
        f'🏷 {category}\n'
        f'📝 {description or "—"}\n'
        f'📅 {date}  👤 {username}'
    )
    msg = await _send_to_topic(context, 'topic_income', text, excel_btn('i', inc_id, False))
    if msg:
        db.set_income_message(inc_id, int(db.get_config('group_id')), msg.message_id)


# ── Expense flow helpers ──────────────────────────────────────────────────────
def _store_pending(context, amount: float, description: str):
    context.user_data['pending'] = {
        'amount':      amount,
        'description': description,
        'date':        datetime.now().strftime('%Y-%m-%d'),
    }


async def _finalize_expense(responder, context, user, description_override=None):
    """
    Save pending expense to DB and post to topic.
    responder: a CallbackQuery or Message — used to send the confirmation.
    """
    pending = context.user_data.get('pending')
    if not pending:
        return

    if description_override is not None:
        pending['description'] = description_override

    category = pending.get('category', '📦 Другое')

    exp_id = db.add_expense(
        user_id=user.id,
        username=user.username or user.first_name,
        amount=pending['amount'],
        category=category,
        description=pending['description'],
        date=pending['date'],
    )
    await _post_expense(
        context, exp_id,
        pending['amount'], category,
        pending['description'], pending['date'],
        user.username or user.first_name,
    )

    context.user_data['last_saved'] = pending.copy()
    context.user_data.pop('pending', None)

    confirm = (
        f'✅ <b>Сохранено!</b>\n'
        f'💰 {pending["amount"]:.2f} CAD  |  {category}\n'
        f'📝 {pending["description"] or "—"}  |  📅 {pending["date"]}\n\n'
        f'Разделить на ещё одну категорию?'
    )
    try:
        # CallbackQuery
        await responder.edit_message_text(confirm, reply_markup=split_keyboard(), parse_mode='HTML')
    except AttributeError:
        # Message
        await responder.reply_text(confirm, reply_markup=split_keyboard(), parse_mode='HTML')


async def _save_and_next_line(query, context, category: str):
    """Save current receipt line item, advance to next."""
    receipt = context.user_data.get('receipt', {})
    lines   = receipt.get('lines', [])
    index   = receipt.get('index', 0)
    date    = receipt.get('date', datetime.now().strftime('%Y-%m-%d'))
    user    = query.from_user

    if index >= len(lines):
        await query.edit_message_text('✅ Все строки обработаны.')
        return

    item   = lines[index]
    exp_id = db.add_expense(
        user_id=user.id, username=user.username or user.first_name,
        amount=item['amount'], category=category,
        description=item['name'], date=date,
    )
    await _post_expense(context, exp_id, item['amount'], category,
                        item['name'], date, user.username or user.first_name)

    next_index       = index + 1
    receipt['index'] = next_index
    context.user_data['receipt'] = receipt

    if next_index >= len(lines):
        total      = receipt.get('total')
        total_line = f'\n💰 <b>Итого: {total:.2f} CAD</b>' if total else ''
        await query.edit_message_text(
            f'✅ <b>Все строки сохранены!</b>{total_line}\n\nВсе позиции отправлены в Расходы.',
            parse_mode='HTML'
        )
        context.user_data.pop('receipt', None)
    else:
        await query.edit_message_text(
            _line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML'
        )


def _line_prompt(context) -> str:
    receipt = context.user_data.get('receipt', {})
    lines   = receipt.get('lines', [])
    index   = receipt.get('index', 0)
    if index >= len(lines):
        return ''
    item = lines[index]
    return (
        f'📋 <b>Строка {index + 1} из {len(lines)}</b>\n\n'
        f'🏷 {item["name"]}\n'
        f'💰 <code>{item["amount"]:.2f} CAD</code>\n\n'
        f'Выберите категорию или пропустите:'
    )


def _receipt_summary(lines: list, total) -> str:
    text = '📄 <b>Строки чека:</b>\n\n'
    for i, item in enumerate(lines, 1):
        text += f'{i}. {item["name"]} — <code>{item["amount"]:.2f} CAD</code>\n'
    text += '\n━━━━━━━━━━━━━━\n'
    if total:
        text += f'💰 <b>Итого: {total:.2f} CAD</b>\n\n'
    text += 'Как сохранить расходы?'
    return text


# ── Commands ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '<b>👋 Трекер расходов и доходов</b>\n\n'
        '<b>Добавить расход:</b>\n'
        '• Напишите: <code>250 магнит</code>\n'
        '• /add 250 магнит\n'
        '• 📸 Фото чека — читаю строки автоматически\n\n'
        '<b>Добавить доход:</b>\n'
        '• /income 5000 зарплата\n\n'
        '<b>Команды:</b>\n'
        '/create_topics — создать топики автоматически\n'
        '/summary — итоги месяца\n'
        '/last — последние 10 расходов\n'
        '/undo — отменить последний расход\n'
        '/categories — все категории\n',
        parse_mode='HTML'
    )


async def create_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg     = await update.message.reply_text('⏳ Создаю топики…')
    try:
        t_input    = await context.bot.create_forum_topic(chat_id, '📝 Ввод')
        t_expenses = await context.bot.create_forum_topic(chat_id, '💸 Расходы')
        t_income   = await context.bot.create_forum_topic(chat_id, '💰 Доходы')

        db.set_config('group_id',       str(chat_id))
        db.set_config('topic_input',    str(t_input.message_thread_id))
        db.set_config('topic_expenses', str(t_expenses.message_thread_id))
        db.set_config('topic_income',   str(t_income.message_thread_id))

        await msg.edit_text(
            f'✅ <b>Топики созданы!</b>\n\n'
            f'📝 Ввод → ID <code>{t_input.message_thread_id}</code>\n'
            f'💸 Расходы → ID <code>{t_expenses.message_thread_id}</code>\n'
            f'💰 Доходы → ID <code>{t_income.message_thread_id}</code>\n\n'
            f'Пишите расходы в топике <b>Ввод</b>.',
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f'create_topics error: {e}')
        await msg.edit_text(
            f'❌ <b>Ошибка:</b> <code>{e}</code>\n\n'
            f'Убедитесь что:\n'
            f'1. Бот является <b>администратором</b> группы\n'
            f'2. У бота есть право <b>Управление темами</b>\n'
            f'3. В настройках группы включены <b>Темы</b>',
            parse_mode='HTML'
        )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: <code>/add 250 магнит</code>', parse_mode='HTML')
        return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.')
        return
    description = ' '.join(context.args[1:])
    _store_pending(context, amount, description)
    await update.message.reply_text(
        _exp_preview(amount, description), reply_markup=cat_keyboard(), parse_mode='HTML'
    )


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: <code>/income 5000 зарплата</code>', parse_mode='HTML')
        return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.')
        return
    description = ' '.join(context.args[1:])
    context.user_data['pending_income'] = {
        'amount': amount, 'description': description,
        'date':   datetime.now().strftime('%Y-%m-%d'),
    }
    await update.message.reply_text(
        f'💵 <b>Доход:</b> {amount:.2f} CAD\n📝 {description or "—"}\n\nВыберите категорию:',
        reply_markup=inc_keyboard(), parse_mode='HTML'
    )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now       = datetime.now()
    exp_rows  = db.get_monthly_expenses()
    total_exp = db.get_total_expenses()
    total_inc = db.get_total_income()

    text = f'📊 <b>{MONTH_RU[now.month]} {now.year}</b>\n\n'

    if exp_rows:
        text += '<b>💸 Расходы:</b>\n'
        for cat, total, count in exp_rows:
            pct   = (total / total_exp * 100) if total_exp else 0
            text += f'{cat}\n  <code>{total:.2f} CAD</code> ({pct:.0f}%) — {count} оп.\n\n'
        text += f'━━━━━━━━━━━━━━\n💳 <b>Расходы: {total_exp:.2f} CAD</b>\n'
    else:
        text += '<i>Расходов нет</i>\n'

    text += f'💰 <b>Доходы: {total_inc:.2f} CAD</b>'

    if total_inc > 0 or total_exp > 0:
        balance = total_inc - total_exp
        sign    = '+' if balance >= 0 else ''
        emoji   = '📈' if balance >= 0 else '📉'
        text   += f'\n{emoji} <b>Баланс: {sign}{balance:.2f} CAD</b>'

    await update.message.reply_text(text, parse_mode='HTML')


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_recent_expenses(10)
    if not rows:
        await update.message.reply_text('Расходов пока нет.')
        return
    text = '<b>📋 Последние расходы:</b>\n\n'
    for amount, cat, desc, date in rows:
        d     = f' — {desc}' if desc else ''
        text += f'• {date} | <code>{amount:.2f}</code> | {cat}{d}\n'
    await update.message.reply_text(text, parse_mode='HTML')


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_chat_id, msg_id = db.delete_last_expense(update.effective_user.id)

    if msg_chat_id is None:
        await update.message.reply_text('Нечего удалять.')
        return

    # Delete message from expenses topic
    if msg_chat_id and msg_id:
        try:
            await context.bot.delete_message(chat_id=msg_chat_id, message_id=msg_id)
        except Exception as e:
            logger.warning(f'Could not delete topic message: {e}')

    await update.message.reply_text('↩️ Последний расход удалён.')


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = '<b>📋 Категории расходов:</b>\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in EXP_CATEGORIES.items())
    text += '\n\n<b>💵 Категории доходов:</b>\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in INC_CATEGORIES.items())
    await update.message.reply_text(text, parse_mode='HTML')


async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton('⬜ Расходы — не в Excel', callback_data='exf|e|0'),
            InlineKeyboardButton('✅ Расходы — в Excel',    callback_data='exf|e|1'),
        ],
        [
            InlineKeyboardButton('⬜ Доходы — не в Excel',  callback_data='exf|i|0'),
            InlineKeyboardButton('✅ Доходы — в Excel',     callback_data='exf|i|1'),
        ],
    ])
    await update.message.reply_text(
        '📊 <b>Статус Excel</b>\n\nЧто показать?',
        reply_markup=keyboard,
        parse_mode='HTML'
    )


def _build_excel_list(rows: list, record_type: str, marked: bool) -> tuple:
    """Returns (text, InlineKeyboardMarkup or None)"""
    type_label = 'Расходы' if record_type == 'e' else 'Доходы'
    icon       = '💸' if record_type == 'e' else '💰'
    status     = '✅ В Excel' if marked else '⬜ Не в Excel'

    if not rows:
        return f'{icon} <b>{type_label} — {status}:</b>\n\n<i>Записей нет.</i>', None

    text     = f'{icon} <b>{type_label} — {status} ({len(rows)}):</b>\n\n'
    btn_rows, row = [], []

    for i, (rec_id, amount, category, description, date) in enumerate(rows, 1):
        short_date = date[5:] if date else '—'
        desc       = f' | {description}' if description else ''
        text      += f'{i}. {short_date} | <code>{amount:.2f}</code> | {category}{desc}\n'

        label = f'✅ #{i}' if not marked else f'↩️ #{i}'
        row.append(InlineKeyboardButton(label, callback_data=f'mxls_{record_type}|{rec_id}'))
        if len(row) == 4:
            btn_rows.append(row); row = []

    if row:
        btn_rows.append(row)

    if not marked:
        btn_rows.append([InlineKeyboardButton('✅ Отметить все', callback_data=f'mxls_all_{record_type}')])

    return text, InlineKeyboardMarkup(btn_rows) if btn_rows else None


# ── Text / Photo Handlers ─────────────────────────────────────────────────────
def _exp_preview(amount: float, description: str) -> str:
    desc = f'\n📝 {description}' if description else ''
    return f'💰 <b>{amount:.2f} CAD</b>{desc}\n\nВыберите категорию:'


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    # Waiting for description after category selected
    if context.user_data.get('waiting_description'):
        context.user_data.pop('waiting_description', None)
        await _finalize_expense(update.message, context, user, description_override=text)
        return

    # Waiting for manual amount after failed OCR
    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: <code>45.50</code>', parse_mode='HTML')
            return
        context.user_data.pop('waiting_for_amount', None)
        _store_pending(context, amount, 'Чек (фото)')
        await update.message.reply_text(
            _exp_preview(amount, 'Чек (фото)'), reply_markup=cat_keyboard(), parse_mode='HTML'
        )
        return

    # Waiting for split amount
    if context.user_data.get('waiting_split_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: <code>45.50</code>', parse_mode='HTML')
            return
        context.user_data.pop('waiting_split_amount', None)
        last = context.user_data.get('last_saved', {})
        _store_pending(context, amount, last.get('description', ''))
        await update.message.reply_text(
            _exp_preview(amount, last.get('description', '')),
            reply_markup=cat_keyboard(), parse_mode='HTML'
        )
        return

    # Natural text: "250" or "250 магнит"
    match = re.match(r'^(\d+(?:[.,]\d+)?)\s*(.*)$', text)
    if not match:
        return
    try:
        amount = float(match.group(1).replace(',', '.'))
    except ValueError:
        return
    description = match.group(2).strip()
    _store_pending(context, amount, description)
    await update.message.reply_text(
        _exp_preview(amount, description), reply_markup=cat_keyboard(), parse_mode='HTML'
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text('📸 Читаю чек, подождите…')

    photo       = update.message.photo[-1]
    photo_file  = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    ocr_text, lines, total = extract_from_image(photo_bytes)
    today = datetime.now().strftime('%Y-%m-%d')

    if lines:
        context.user_data['receipt'] = {'lines': lines, 'index': 0, 'total': total, 'date': today}
        if total:
            context.user_data['ocr_amount'] = total
        await status.edit_text(
            _receipt_summary(lines, total),
            reply_markup=ocr_mode_keyboard(),
            parse_mode='HTML'
        )
    elif total:
        context.user_data['ocr_amount'] = total
        raw = (ocr_text[:400] + '…') if ocr_text and len(ocr_text) > 400 else (ocr_text or '—')
        await status.edit_text(
            f'📄 <b>Текст с чека:</b>\n<code>{raw}</code>\n\n'
            f'💰 <b>Найденная сумма:</b> {total:.2f} CAD\n\nПодтвердите:',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f'✅ Верно — {total:.2f} CAD', callback_data='ocr_ok'),
                InlineKeyboardButton('✏️ Изменить', callback_data='ocr_edit'),
            ]]),
            parse_mode='HTML'
        )
    else:
        context.user_data['waiting_for_amount'] = True
        raw = (ocr_text[:400] + '…') if ocr_text and len(ocr_text) > 400 else (ocr_text or '—')
        await status.edit_text(
            f'📄 <b>Текст с чека:</b>\n<code>{raw}</code>\n\n'
            f'❌ Сумма не найдена. Введите вручную:',
            parse_mode='HTML'
        )


# ── Callback Handler ──────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    logger.info(f'Callback [{user.username or user.id}]: {data}')

    try:

        # ── Receipt: choose mode ───────────────────────────────────────────
        if data == 'rl_start':
            receipt = context.user_data.get('receipt')
            if not receipt or not receipt.get('lines'):
                await query.edit_message_text('❌ Нет данных о строках.')
                return
            receipt['index'] = 0
            context.user_data['receipt'] = receipt
            await query.edit_message_text(
                _line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML'
            )

        elif data == 'rl_one':
            amount = context.user_data.pop('ocr_amount', None)
            if not amount:
                amount = context.user_data.get('receipt', {}).get('total')
            context.user_data.pop('receipt', None)
            if not amount:
                await query.edit_message_text('❌ Нет данных о сумме.')
                return
            _store_pending(context, amount, 'Чек (фото)')
            await query.edit_message_text(
                _exp_preview(amount, 'Чек (фото)'), reply_markup=cat_keyboard(), parse_mode='HTML'
            )

        # ── Receipt: line-by-line category ────────────────────────────────
        elif data.startswith('rlcat|'):
            category = EXP_CATEGORIES.get(data.split('|')[1], '📦 Другое')
            await _save_and_next_line(query, context, category)

        elif data == 'rl_skip':
            receipt = context.user_data.get('receipt', {})
            lines   = receipt.get('lines', [])
            index   = receipt.get('index', 0)
            receipt['index'] = index + 1
            context.user_data['receipt'] = receipt
            if index + 1 >= len(lines):
                await query.edit_message_text('✅ Готово. Все строки обработаны.')
                context.user_data.pop('receipt', None)
            else:
                await query.edit_message_text(
                    _line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML'
                )

        elif data == 'rl_done':
            context.user_data.pop('receipt', None)
            await query.edit_message_text('✅ Готово.')

        # ── Expense: category selected → ask description ──────────────────
        elif data.startswith('c|'):
            category = EXP_CATEGORIES.get(data.split('|')[1], '📦 Другое')
            pending  = context.user_data.get('pending')
            if not pending:
                await query.edit_message_text('❌ Сессия истекла — введите сумму заново.')
                return

            pending['category'] = category
            context.user_data['pending']             = pending
            context.user_data['waiting_description'] = True

            existing = f'\nУже есть: <i>{pending["description"]}</i>' if pending.get('description') else ''
            await query.edit_message_text(
                f'💰 {pending["amount"]:.2f} CAD  |  {category}{existing}\n\n'
                f'📝 Добавьте описание или пропустите:',
                reply_markup=desc_keyboard(),
                parse_mode='HTML'
            )

        # ── Expense: description skipped ──────────────────────────────────
        elif data == 'desc_skip':
            context.user_data.pop('waiting_description', None)
            await _finalize_expense(query, context, user)

        # ── Split ─────────────────────────────────────────────────────────
        elif data == 'split':
            context.user_data['waiting_split_amount'] = True
            await query.edit_message_text('➕ Введите сумму следующей части:')

        elif data == 'split_done':
            context.user_data.pop('last_saved', None)
            await query.edit_message_text('✅ Чек полностью сохранён.')

        # ── OCR: confirm total ────────────────────────────────────────────
        elif data == 'ocr_ok':
            amount = context.user_data.pop('ocr_amount', None)
            if not amount:
                await query.edit_message_text('❌ Сессия истекла.')
                return
            _store_pending(context, amount, 'Чек (фото)')
            await query.edit_message_text(
                _exp_preview(amount, 'Чек (фото)'), reply_markup=cat_keyboard(), parse_mode='HTML'
            )

        elif data == 'ocr_edit':
            context.user_data.pop('ocr_amount', None)
            context.user_data['waiting_for_amount'] = True
            await query.edit_message_text('✏️ Введите сумму вручную:')

        # ── Income: category selected ─────────────────────────────────────
        elif data.startswith('ic|'):
            category = INC_CATEGORIES.get(data.split('|')[1], '💵 Другой доход')
            pending  = context.user_data.get('pending_income')
            if not pending:
                await query.edit_message_text('❌ Сессия истекла.')
                return
            inc_id = db.add_income(
                user_id=user.id, username=user.username or user.first_name,
                amount=pending['amount'], category=category,
                description=pending['description'], date=pending['date'],
            )
            await _post_income(
                context, inc_id, pending['amount'], category,
                pending['description'], pending['date'],
                user.username or user.first_name,
            )
            context.user_data.pop('pending_income', None)
            await query.edit_message_text(
                f'✅ <b>Доход сохранён!</b>\n'
                f'💵 {pending["amount"]:.2f} CAD\n'
                f'🏷 {category}\n'
                f'📝 {pending["description"] or "—"}',
                parse_mode='HTML'
            )

        # ── Excel filter view ─────────────────────────────────────────────
        elif data.startswith('exf|'):
            _, rtype, mval = data.split('|')
            marked = mval == '1'
            rows   = (db.get_expenses_by_excel(marked) if rtype == 'e'
                      else db.get_income_by_excel(marked))
            text, markup = _build_excel_list(rows, rtype, marked)
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')

        elif data.startswith('mxls_all_'):
            rtype = data[-1]   # 'e' or 'i'
            if rtype == 'e':
                db.mark_all_expenses_excel(True)
            else:
                db.mark_all_income_excel(True)
            await query.answer('✅ Все отмечены как В Excel')
            await query.edit_message_text(
                '✅ <b>Все записи отмечены как В Excel.</b>', parse_mode='HTML'
            )

        # ── Excel toggle ──────────────────────────────────────────────────
        elif data.startswith('xls_e|'):
            exp_id = int(data.split('|')[1])
            row    = db.get_expense(exp_id)
            if not row:
                return
            new = not bool(row[5])
            db.mark_expense_excel(exp_id, new)
            await query.edit_message_reply_markup(reply_markup=excel_btn('e', exp_id, new))

        elif data.startswith('xls_i|'):
            inc_id = int(data.split('|')[1])
            row    = db.get_income(inc_id)
            if not row:
                return
            new = not bool(row[5])
            db.mark_income_excel(inc_id, new)
            await query.edit_message_reply_markup(reply_markup=excel_btn('i', inc_id, new))

    except Exception as e:
        # Log only — do NOT post error to chat/topic
        logger.error(f'Callback error [{data}]: {e}', exc_info=True)


# ── Main ──────────────────────────────────────────────────────────────────────
async def post_init(application: Application):
    """Set bot command menu — shows as '/' button in Telegram."""
    await application.bot.set_my_commands([
        BotCommand('add',           '➕ Добавить расход'),
        BotCommand('income',        '💵 Добавить доход'),
        BotCommand('summary',       '📊 Итоги за месяц'),
        BotCommand('last',          '📋 Последние расходы'),
        BotCommand('excel',         '📑 Статус Excel'),
        BotCommand('undo',          '↩️ Отменить последний расход'),
        BotCommand('categories',    '🏷 Категории'),
        BotCommand('create_topics', '⚙️ Создать топики'),
    ])


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler('start',         start))
    app.add_handler(CommandHandler('help',          start))
    app.add_handler(CommandHandler('create_topics', create_topics_command))
    app.add_handler(CommandHandler('add',           add_command))
    app.add_handler(CommandHandler('income',        income_command))
    app.add_handler(CommandHandler('summary',       summary_command))
    app.add_handler(CommandHandler('last',          last_command))
    app.add_handler(CommandHandler('undo',          undo_command))
    app.add_handler(CommandHandler('categories',    categories_command))
    app.add_handler(CommandHandler('excel',         excel_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info('Bot started.')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
