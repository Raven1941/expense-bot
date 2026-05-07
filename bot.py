import os
import re
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
def cat_keyboard(prefix='c') -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'{prefix}|{k}'))
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
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f'xls_{record_type}|{record_id}')]])


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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _store_pending(context, amount: float, description: str):
    context.user_data['pending'] = {
        'amount':      amount,
        'description': description,
        'date':        datetime.now().strftime('%Y-%m-%d'),
    }


def _expense_preview(amount: float, description: str) -> str:
    desc = f'\n📝 *Описание:* {description}' if description else ''
    return f'💰 *Сумма:* {amount:.2f} CAD{desc}\n\nВыберите категорию:'


def _format_receipt_summary(lines: list, total) -> str:
    text = '📄 *Строки чека:*\n\n'
    for i, item in enumerate(lines, 1):
        text += f'`{i}.` {item["name"]} — `{item["amount"]:.2f} CAD`\n'
    text += '\n━━━━━━━━━━━━━━\n'
    if total:
        text += f'💰 *Итого: {total:.2f} CAD*\n\n'
    text += 'Как сохранить расходы?'
    return text


def _current_line_prompt(context) -> str:
    receipt = context.user_data.get('receipt', {})
    lines   = receipt.get('lines', [])
    index   = receipt.get('index', 0)
    if index >= len(lines):
        return ''
    item = lines[index]
    return (
        f'📋 *Строка {index + 1} из {len(lines)}*\n\n'
        f'🏷️ {item["name"]}\n'
        f'💰 `{item["amount"]:.2f} CAD`\n\n'
        f'Выберите категорию или пропустите:'
    )


async def _post_expense(context, exp_id, amount, category, description, date, username):
    group_id = db.get_config('group_id')
    topic_id = db.get_config('topic_expenses')
    if not group_id or not topic_id:
        logger.warning('Expenses topic not configured — skipping post')
        return
    text = (
        f'💸 *Расход*\n'
        f'💰 `{amount:.2f} CAD`\n'
        f'🏷️ {category}\n'
        f'📝 {description or "—"}\n'
        f'📅 {date} | 👤 {username}'
    )
    try:
        kwargs = dict(chat_id=int(group_id), text=text, parse_mode='Markdown',
                      reply_markup=excel_btn('e', exp_id, False))
        tid = int(topic_id)
        if tid:
            kwargs['message_thread_id'] = tid
        msg = await context.bot.send_message(**kwargs)
        db.set_expense_message(exp_id, int(group_id), msg.message_id)
    except Exception as e:
        logger.error(f'_post_expense error: {e}')


async def _post_income(context, inc_id, amount, category, description, date, username):
    group_id = db.get_config('group_id')
    topic_id = db.get_config('topic_income')
    if not group_id or not topic_id:
        logger.warning('Income topic not configured — skipping post')
        return
    text = (
        f'💰 *Доход*\n'
        f'💵 `{amount:.2f} CAD`\n'
        f'🏷️ {category}\n'
        f'📝 {description or "—"}\n'
        f'📅 {date} | 👤 {username}'
    )
    try:
        kwargs = dict(chat_id=int(group_id), text=text, parse_mode='Markdown',
                      reply_markup=excel_btn('i', inc_id, False))
        tid = int(topic_id)
        if tid:
            kwargs['message_thread_id'] = tid
        msg = await context.bot.send_message(**kwargs)
        db.set_income_message(inc_id, int(group_id), msg.message_id)
    except Exception as e:
        logger.error(f'_post_income error: {e}')


async def _save_and_next_line(query, context, category: str):
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

    next_index          = index + 1
    receipt['index']    = next_index
    context.user_data['receipt'] = receipt

    if next_index >= len(lines):
        total      = receipt.get('total')
        total_text = f'\n💰 *Итого по чеку: {total:.2f} CAD*' if total else ''
        await query.edit_message_text(
            f'✅ *Все строки сохранены!*{total_text}\n\nВсе позиции отправлены в Расходы.',
            parse_mode='Markdown'
        )
        context.user_data.pop('receipt', None)
    else:
        await query.edit_message_text(
            _current_line_prompt(context),
            reply_markup=line_cat_keyboard(),
            parse_mode='Markdown'
        )


# ── Commands ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '👋 *Трекер расходов и доходов*\n\n'
        '*Добавить расход:*\n'
        '• Напишите: `250 магнит`\n'
        '• `/add 250 магнит`\n'
        '• 📸 Фото чека — читаю строки автоматически\n\n'
        '*Добавить доход:*\n'
        '• `/income 5000 зарплата`\n\n'
        '*Команды:*\n'
        '/create\\_topics — создать топики автоматически\n'
        '/summary — итоги месяца\n'
        '/last — последние 10 расходов\n'
        '/undo — отменить последний расход\n'
        '/categories — все категории\n',
        parse_mode='Markdown'
    )


async def create_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Automatically create all 3 forum topics and save their IDs.
    Bot must be admin with 'can_manage_topics' permission.
    Run this command once in the group.
    """
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
            '✅ *Топики созданы и настроены!*\n\n'
            f'📝 Ввод → ID `{t_input.message_thread_id}`\n'
            f'💸 Расходы → ID `{t_expenses.message_thread_id}`\n'
            f'💰 Доходы → ID `{t_income.message_thread_id}`\n\n'
            'Теперь пишите расходы в топике *Ввод*.',
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f'create_topics error: {e}')
        await msg.edit_text(
            f'❌ *Ошибка:* `{e}`\n\n'
            'Убедитесь что:\n'
            '1. Бот является *администратором* группы\n'
            '2. У бота есть право *Управление темами*\n'
            '3. В настройках группы включены *Темы* (Topics)',
            parse_mode='Markdown'
        )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: `/add 250 магнит`', parse_mode='Markdown')
        return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.')
        return
    description = ' '.join(context.args[1:])
    _store_pending(context, amount, description)
    await update.message.reply_text(
        _expense_preview(amount, description), reply_markup=cat_keyboard(), parse_mode='Markdown'
    )


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: `/income 5000 зарплата`', parse_mode='Markdown')
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
        f'💵 *Доход:* {amount:.2f} CAD\n📝 {description or "—"}\n\nВыберите категорию:',
        reply_markup=inc_keyboard(), parse_mode='Markdown'
    )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now       = datetime.now()
    exp_rows  = db.get_monthly_expenses()
    total_exp = db.get_total_expenses()
    total_inc = db.get_total_income()

    text = f'📊 *{MONTH_RU[now.month]} {now.year}*\n\n'
    if exp_rows:
        text += '*💸 Расходы:*\n'
        for cat, total, count in exp_rows:
            pct   = (total / total_exp * 100) if total_exp else 0
            text += f'{cat}\n  `{total:.2f} CAD` ({pct:.0f}%) — {count} оп.\n\n'
        text += f'━━━━━━━━━━━━━━\n💳 *Расходы: {total_exp:.2f} CAD*\n'
    else:
        text += '_Расходов нет_\n'

    text += f'💰 *Доходы: {total_inc:.2f} CAD*'
    if total_inc > 0 or total_exp > 0:
        balance = total_inc - total_exp
        sign    = '+' if balance >= 0 else ''
        emoji   = '📈' if balance >= 0 else '📉'
        text   += f'\n{emoji} *Баланс: {sign}{balance:.2f} CAD*'

    await update.message.reply_text(text, parse_mode='Markdown')


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_recent_expenses(10)
    if not rows:
        await update.message.reply_text('Расходов пока нет.')
        return
    text = '📋 *Последние расходы:*\n\n'
    for amount, cat, desc, date in rows:
        d     = f' — {desc}' if desc else ''
        text += f'• {date} | `{amount:.2f}` | {cat}{d}\n'
    await update.message.reply_text(text, parse_mode='Markdown')


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db.delete_last_expense(update.effective_user.id):
        await update.message.reply_text('↩️ Последний расход удалён.')
    else:
        await update.message.reply_text('Нечего удалять.')


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = '📋 *Категории расходов:*\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in EXP_CATEGORIES.items())
    text += '\n\n💵 *Категории доходов:*\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in INC_CATEGORIES.items())
    await update.message.reply_text(text, parse_mode='Markdown')


# ── Message Handlers ──────────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: `45.50`', parse_mode='Markdown')
            return
        context.user_data.pop('waiting_for_amount', None)
        _store_pending(context, amount, 'Чек (фото)')
        await update.message.reply_text(
            _expense_preview(amount, 'Чек (фото)'), reply_markup=cat_keyboard(), parse_mode='Markdown'
        )
        return

    if context.user_data.get('waiting_split_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: `45.50`', parse_mode='Markdown')
            return
        context.user_data.pop('waiting_split_amount', None)
        last = context.user_data.get('last_saved', {})
        _store_pending(context, amount, last.get('description', ''))
        await update.message.reply_text(
            _expense_preview(amount, last.get('description', '')),
            reply_markup=cat_keyboard(), parse_mode='Markdown'
        )
        return

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
        _expense_preview(amount, description), reply_markup=cat_keyboard(), parse_mode='Markdown'
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
            context.user_data['ocr_desc']   = 'Чек (фото)'
        await status.edit_text(
            _format_receipt_summary(lines, total),
            reply_markup=ocr_mode_keyboard(),
            parse_mode='Markdown'
        )
    elif total:
        context.user_data['ocr_amount'] = total
        context.user_data['ocr_desc']   = 'Чек (фото)'
        raw = (ocr_text[:400] + '…') if ocr_text and len(ocr_text) > 400 else (ocr_text or '—')
        await status.edit_text(
            f'📄 *Текст с чека:*\n```\n{raw}\n```\n\n'
            f'💰 *Найденная сумма:* {total:.2f} CAD\n\nПодтвердите:',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f'✅ Верно — {total:.2f} CAD', callback_data='ocr_ok'),
                InlineKeyboardButton('✏️ Изменить', callback_data='ocr_edit'),
            ]]),
            parse_mode='Markdown'
        )
    else:
        context.user_data['waiting_for_amount'] = True
        raw = (ocr_text[:400] + '…') if ocr_text and len(ocr_text) > 400 else (ocr_text or '—')
        await status.edit_text(
            f'📄 *Текст с чека:*\n```\n{raw}\n```\n\n❌ Сумма не найдена. Введите вручную:',
            parse_mode='Markdown'
        )


# ── Callback Handler ──────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    logger.info(f'Callback from {user.username or user.id}: {data}')

    try:

        # ── Receipt mode ───────────────────────────────────────────────────
        if data == 'rl_start':
            receipt = context.user_data.get('receipt')
            if not receipt or not receipt.get('lines'):
                await query.edit_message_text('❌ Нет данных о строках.')
                return
            receipt['index'] = 0
            context.user_data['receipt'] = receipt
            await query.edit_message_text(
                _current_line_prompt(context),
                reply_markup=line_cat_keyboard(),
                parse_mode='Markdown'
            )

        elif data == 'rl_one':
            amount = context.user_data.pop('ocr_amount', None)
            if not amount:
                amount = context.user_data.get('receipt', {}).get('total')
            desc = context.user_data.pop('ocr_desc', 'Чек (фото)')
            context.user_data.pop('receipt', None)
            if not amount:
                await query.edit_message_text('❌ Нет данных о сумме.')
                return
            _store_pending(context, amount, desc)
            await query.edit_message_text(
                _expense_preview(amount, desc), reply_markup=cat_keyboard(), parse_mode='Markdown'
            )

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
                    _current_line_prompt(context),
                    reply_markup=line_cat_keyboard(),
                    parse_mode='Markdown'
                )

        elif data == 'rl_done':
            context.user_data.pop('receipt', None)
            await query.edit_message_text('✅ Готово.')

        # ── Expense category ───────────────────────────────────────────────
        elif data.startswith('c|'):
            category = EXP_CATEGORIES.get(data.split('|')[1], '📦 Другое')
            pending  = context.user_data.get('pending')

            if not pending:
                await query.edit_message_text(
                    '❌ Сессия истекла — введите сумму заново.'
                )
                return

            exp_id = db.add_expense(
                user_id=user.id, username=user.username or user.first_name,
                amount=pending['amount'], category=category,
                description=pending['description'], date=pending['date'],
            )
            await _post_expense(
                context, exp_id, pending['amount'], category,
                pending['description'], pending['date'],
                user.username or user.first_name,
            )
            context.user_data['last_saved'] = pending.copy()
            context.user_data.pop('pending', None)

            await query.edit_message_text(
                f'✅ *Сохранено!*\n'
                f'💰 {pending["amount"]:.2f} CAD | {category}\n'
                f'📝 {pending["description"] or "—"} | 📅 {pending["date"]}\n\n'
                f'Разделить этот чек на ещё одну категорию?',
                reply_markup=split_keyboard(),
                parse_mode='Markdown'
            )

        # ── Split ──────────────────────────────────────────────────────────
        elif data == 'split':
            context.user_data['waiting_split_amount'] = True
            await query.edit_message_text('➕ Введите сумму следующей части:')

        elif data == 'split_done':
            context.user_data.pop('last_saved', None)
            await query.edit_message_text('✅ Чек полностью сохранён.')

        # ── OCR confirm ────────────────────────────────────────────────────
        elif data == 'ocr_ok':
            amount = context.user_data.pop('ocr_amount', None)
            desc   = context.user_data.pop('ocr_desc', 'Чек (фото)')
            if not amount:
                await query.edit_message_text('❌ Сессия истекла.')
                return
            _store_pending(context, amount, desc)
            await query.edit_message_text(
                _expense_preview(amount, desc), reply_markup=cat_keyboard(), parse_mode='Markdown'
            )

        elif data == 'ocr_edit':
            context.user_data.pop('ocr_amount', None)
            context.user_data['waiting_for_amount'] = True
            await query.edit_message_text('✏️ Введите сумму вручную:')

        # ── Income ────────────────────────────────────────────────────────
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
                f'✅ *Доход сохранён!*\n'
                f'💵 {pending["amount"]:.2f} CAD\n'
                f'🏷️ {category}\n'
                f'📝 {pending["description"] or "—"}',
                parse_mode='Markdown'
            )

        # ── Excel marking ──────────────────────────────────────────────────
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
        logger.error(f'Callback error [{data}]: {e}', exc_info=True)
        try:
            await query.edit_message_text(f'❌ Ошибка: {e}\n\nНачните заново.')
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start',         start))
    app.add_handler(CommandHandler('help',          start))
    app.add_handler(CommandHandler('create_topics', create_topics_command))
    app.add_handler(CommandHandler('add',           add_command))
    app.add_handler(CommandHandler('income',        income_command))
    app.add_handler(CommandHandler('summary',       summary_command))
    app.add_handler(CommandHandler('last',          last_command))
    app.add_handler(CommandHandler('undo',          undo_command))
    app.add_handler(CommandHandler('categories',    categories_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info('Bot started.')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
