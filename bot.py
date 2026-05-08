import os
import re
import csv
import logging
import io
from datetime import datetime, timedelta, time as dt_time

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllGroupChats,
    InputFile,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

from database import Database
from ocr import extract_from_image
from charts import generate_expense_chart

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
    1:'Январь', 2:'Февраль', 3:'Март',    4:'Апрель',
    5:'Май',    6:'Июнь',    7:'Июль',    8:'Август',
    9:'Сентябрь',10:'Октябрь',11:'Ноябрь',12:'Декабрь',
}

BOT_COMMANDS = [
    BotCommand('add',           '➕ Добавить расход'),
    BotCommand('income',        '💵 Добавить доход'),
    BotCommand('summary',       '📊 Итоги за месяц'),
    BotCommand('summary_week',  '📅 Итоги за неделю'),
    BotCommand('summary_users', '👥 Итоги по пользователям'),
    BotCommand('chart',         '📈 График расходов'),
    BotCommand('last',          '📋 Последние расходы'),
    BotCommand('search',        '🔍 Поиск расходов'),
    BotCommand('edit',          '✏️ Редактировать последний расход'),
    BotCommand('undo',          '↩️ Отменить последний расход'),
    BotCommand('excel',         '📑 Статус Excel'),
    BotCommand('export',        '📥 Экспорт в CSV'),
    BotCommand('budgets',       '💰 Бюджеты по категориям'),
    BotCommand('setbudget',     '⚙️ Установить бюджет'),
    BotCommand('recurring',     '🔁 Регулярные расходы'),
    BotCommand('reminder',      '🔔 Напоминание'),
    BotCommand('categories',    '🏷 Категории'),
    BotCommand('create_topics', '⚙️ Создать топики'),
]


# ── Keyboards ─────────────────────────────────────────────────────────────────
def cat_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'c|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def line_cat_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'rlcat|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
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


def bill_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('💳 Обычный расход', callback_data='bill_single'),
        InlineKeyboardButton('🧾 Общий счёт',     callback_data='bill_combined'),
    ]])


def combined_bill_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('➕ Добавить позицию', callback_data='cb_add'),
            InlineKeyboardButton('✅ Завершить',         callback_data='cb_finish'),
        ],
        [
            InlineKeyboardButton('✏️ Изменить',          callback_data='cb_edit_menu'),
            InlineKeyboardButton('🗑 Удалить',            callback_data='cb_del_menu'),
        ],
        [InlineKeyboardButton('❌ Отмена',               callback_data='cb_cancel')],
    ])


def cb_delete_keyboard(positions: list) -> InlineKeyboardMarkup:
    rows = []
    for i, pos in enumerate(positions):
        label = f'🗑 #{i+1} — {pos["amount"]:.2f} | {pos["category"]} | {pos["description"] or "—"}'
        rows.append([InlineKeyboardButton(label, callback_data=f'cb_del|{i}')])
    rows.append([InlineKeyboardButton('◀️ Назад', callback_data='cb_back')])
    return InlineKeyboardMarkup(rows)


def cb_edit_keyboard(positions: list) -> InlineKeyboardMarkup:
    rows = []
    for i, pos in enumerate(positions):
        label = f'✏️ #{i+1} — {pos["amount"]:.2f} | {pos["category"]} | {pos["description"] or "—"}'
        rows.append([InlineKeyboardButton(label, callback_data=f'cb_edit|{i}')])
    rows.append([InlineKeyboardButton('◀️ Назад', callback_data='cb_back')])
    return InlineKeyboardMarkup(rows)


def cb_edit_field_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('💰 Сумма',     callback_data=f'cb_editamt|{idx}'),
            InlineKeyboardButton('🏷 Категория', callback_data=f'cb_editcat|{idx}'),
            InlineKeyboardButton('📝 Описание',  callback_data=f'cb_editdesc|{idx}'),
        ],
        [InlineKeyboardButton('◀️ Назад', callback_data='cb_edit_menu')],
    ])


def cb_cat_keyboard(idx: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'cb_setcat|{idx}|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def cb_pos_cat_keyboard() -> InlineKeyboardMarkup:
    """Category keyboard for new combined bill position."""
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'cb_poscat|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)



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


def edit_keyboard(exp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✏️ Сумма',       callback_data=f'edit_amt|{exp_id}'),
            InlineKeyboardButton('🏷 Категория',   callback_data=f'edit_cat|{exp_id}'),
            InlineKeyboardButton('📝 Описание',    callback_data=f'edit_desc|{exp_id}'),
        ],
        [InlineKeyboardButton('❌ Отмена', callback_data='edit_cancel')],
    ])


def cat_keyboard_edit(exp_id: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'edit_setcat|{exp_id}|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def budget_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, v in EXP_CATEGORIES.items():
        row.append(InlineKeyboardButton(v, callback_data=f'budget_set|{k}'))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def recurring_del_keyboard(rows: list) -> InlineKeyboardMarkup:
    kb = []
    for rec_id, amount, category, description in rows:
        kb.append([InlineKeyboardButton(
            f'🗑 {amount:.0f} CAD | {category} | {description or "—"}',
            callback_data=f'del_rec|{rec_id}'
        )])
    kb.append([InlineKeyboardButton('❌ Закрыть', callback_data='rec_close')])
    return InlineKeyboardMarkup(kb)


# ── Date parsing ──────────────────────────────────────────────────────────────
def _parse_date(text: str) -> tuple:
    """
    Extract optional date hint from end of text.
    Returns (cleaned_text, date_str)
    Supports: 'вчера', 'yesterday', 'DD.MM', 'DD.MM.YYYY'
    """
    text = text.strip()

    # вчера / yesterday
    m = re.search(r'\s+(вчера|yesterday)\s*$', text, re.IGNORECASE)
    if m:
        d = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        return text[:m.start()].strip(), d

    # DD.MM.YYYY
    m = re.search(r'\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$', text)
    if m:
        try:
            d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime('%Y-%m-%d')
            return text[:m.start()].strip(), d
        except ValueError:
            pass

    # DD.MM
    m = re.search(r'\s+(\d{1,2})\.(\d{1,2})\s*$', text)
    if m:
        try:
            d = datetime(datetime.now().year, int(m.group(2)), int(m.group(1))).strftime('%Y-%m-%d')
            return text[:m.start()].strip(), d
        except ValueError:
            pass

    return text, datetime.now().strftime('%Y-%m-%d')


# ── Topic post helpers ────────────────────────────────────────────────────────
async def _send_to_topic(context, topic_key: str, text: str, markup: InlineKeyboardMarkup):
    group_id = db.get_config('group_id')
    topic_id = db.get_config(topic_key)
    if not group_id or not topic_id:
        return None
    tid = int(topic_id)
    if tid <= 0:
        return None
    try:
        msg = await context.bot.send_message(
            chat_id=int(group_id), message_thread_id=tid,
            text=text, parse_mode='HTML', reply_markup=markup,
        )
        return msg
    except Exception as e:
        logger.error(f'_send_to_topic({topic_key}): {e}')
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


# ── Combined bill helpers ─────────────────────────────────────────────────────
def _cb_summary_text(cb: dict) -> str:
    """Build the combined bill running summary message."""
    positions  = cb.get('positions', [])
    total      = cb.get('total', 0.0)
    pos_total  = sum(p['amount'] for p in positions)
    diff       = round(total - pos_total, 2)
    store      = cb.get('description', '')
    store_line = f' — {store}' if store else ''

    text = f'🧾 <b>Общий счёт{store_line}</b>\n'
    text += f'💰 Итого: <code>{total:.2f} CAD</code>\n\n'

    if positions:
        for i, pos in enumerate(positions):
            prefix = '└' if i == len(positions) - 1 else '├'
            text += f'{prefix} {i+1}. <code>{pos["amount"]:.2f}</code> | {pos["category"]} | {pos["description"] or "—"}\n'
        text += f'\n<b>Позиций:</b> <code>{pos_total:.2f}</code> / <code>{total:.2f} CAD</code>'
        if diff > 0.005:
            text += f'\n⚠️ Разница (HST): <code>+{diff:.2f} CAD</code> — будет распределена равномерно'
        elif diff < -0.005:
            text += f'\n⚠️ Сумма позиций превышает счёт на <code>{abs(diff):.2f} CAD</code>'
        else:
            text += '\n✅ Сумма сходится'
    else:
        text += '<i>Позиций пока нет. Добавьте первую.</i>'

    return text


async def _post_combined_bill(context, positions: list, total: float,
                               description: str, date: str, username: str,
                               exp_ids: list):
    """Post ONE combined bill message to expenses topic."""
    diff     = round(total - sum(p['amount'] for p in positions), 2)
    hst_line = f' (HST +{diff:.2f})' if diff > 0.005 else ''
    store    = f' — {description}' if description else ''

    text = f'🧾 <b>Общий счёт{store}{hst_line}</b>\n💰 <code>{total:.2f} CAD</code>\n\n'

    for i, pos in enumerate(positions):
        prefix  = '└' if i == len(positions) - 1 else '├'
        adj     = pos.get('adjusted', pos['amount'])
        amt_str = (f'<code>{pos["amount"]:.2f}</code> → <code>{adj:.2f}</code>'
                   if abs(adj - pos['amount']) > 0.005
                   else f'<code>{adj:.2f}</code>')
        text += f'{prefix} {amt_str} | {pos["category"]} | {pos["description"] or "—"}\n'

    text += f'\n📅 {date}  👤 {username}'

    # Use first exp_id for the Excel button — marks all when tapped
    markup = excel_btn('e', exp_ids[0], False) if exp_ids else None
    msg    = await _send_to_topic(context, 'topic_expenses', text, markup)
    if msg and exp_ids:
        gid = int(db.get_config('group_id'))
        # Store message reference on first position only
        db.set_expense_message(exp_ids[0], gid, msg.message_id)


async def _finalize_combined_bill(query, context, user):
    """Distribute HST, save all positions, post combined message."""
    cb        = context.user_data.get('combined_bill', {})
    positions = cb.get('positions', [])
    total     = cb.get('total', 0.0)
    date      = cb.get('date', datetime.now().strftime('%Y-%m-%d'))
    desc      = cb.get('description', '')

    if not positions:
        await query.edit_message_text('❌ Нет позиций для сохранения.')
        return

    pos_total = sum(p['amount'] for p in positions)
    diff      = round(total - pos_total, 2)

    # Distribute difference equally
    if abs(diff) > 0.005:
        share = diff / len(positions)
        for pos in positions:
            pos['adjusted'] = round(pos['amount'] + share, 2)
        # Fix rounding on last item
        adj_sum = sum(p['adjusted'] for p in positions)
        positions[-1]['adjusted'] += round(total - adj_sum, 2)
    else:
        for pos in positions:
            pos['adjusted'] = pos['amount']

    # Save each position to DB
    exp_ids  = []
    username = user.username or user.first_name
    for pos in positions:
        eid = db.add_expense(
            user_id=user.id, username=username,
            amount=pos['adjusted'], category=pos['category'],
            description=pos['description'], date=date,
        )
        exp_ids.append(eid)

    # Post combined message to topic
    await _post_combined_bill(context, positions, total, desc, date, username, exp_ids)

    context.user_data.pop('combined_bill', None)

    # Build confirmation
    hst_note = f'\n⚖️ HST <code>+{diff:.2f} CAD</code> распределён по позициям' if diff > 0.005 else ''
    await query.edit_message_text(
        f'✅ <b>Общий счёт сохранён!</b>\n'
        f'💰 {total:.2f} CAD | {len(positions)} позиций{hst_note}\n\n'
        f'Отправлено в Расходы одним сообщением.',
        parse_mode='HTML'
    )

    # Budget checks
    for pos in positions:
        try:
            await _check_budget(query, pos['category'], pos['adjusted'])
        except Exception:
            pass


async def _check_budget(update_or_query, category: str, amount: float):
    budget = db.get_budget(category)
    if not budget:
        return
    total = db.get_category_total_month(category)
    pct   = (total / budget) * 100
    if pct >= 100:
        msg = f'🚨 <b>Бюджет превышен!</b>\n{category}\n<code>{total:.2f}</code> / <code>{budget:.2f} CAD</code> ({pct:.0f}%)'
    elif pct >= 80:
        msg = f'⚠️ <b>Бюджет на 80%+</b>\n{category}\n<code>{total:.2f}</code> / <code>{budget:.2f} CAD</code> ({pct:.0f}%)'
    else:
        return

    try:
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(msg, parse_mode='HTML')
        elif hasattr(update_or_query, 'edit_message_text'):
            chat_id = update_or_query.message.chat_id
            await update_or_query.get_bot().send_message(chat_id=chat_id, text=msg, parse_mode='HTML')
    except Exception as e:
        logger.warning(f'Budget warning send failed: {e}')


# ── Expense flow helpers ──────────────────────────────────────────────────────
def _store_pending(context, amount: float, description: str, date: str = None):
    context.user_data['pending'] = {
        'amount':      amount,
        'description': description,
        'date':        date or datetime.now().strftime('%Y-%m-%d'),
    }


async def _finalize_expense(responder, context, user, description_override=None):
    pending = context.user_data.get('pending')
    if not pending:
        return
    if description_override is not None:
        pending['description'] = description_override

    category = pending.get('category', '📦 Другое')
    exp_id   = db.add_expense(
        user_id=user.id, username=user.username or user.first_name,
        amount=pending['amount'], category=category,
        description=pending['description'], date=pending['date'],
    )
    await _post_expense(context, exp_id, pending['amount'], category,
                        pending['description'], pending['date'],
                        user.username or user.first_name)

    context.user_data['last_saved'] = pending.copy()
    context.user_data.pop('pending', None)

    confirm = (
        f'✅ <b>Сохранено!</b>\n'
        f'💰 {pending["amount"]:.2f} CAD  |  {category}\n'
        f'📝 {pending["description"] or "—"}  |  📅 {pending["date"]}\n\n'
        f'Разделить на ещё одну категорию?'
    )
    try:
        await responder.edit_message_text(confirm, reply_markup=split_keyboard(), parse_mode='HTML')
    except AttributeError:
        await responder.reply_text(confirm, reply_markup=split_keyboard(), parse_mode='HTML')

    # Budget check
    try:
        await _check_budget(responder, category, pending['amount'])
    except Exception:
        pass


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
    next_index       = index + 1
    receipt['index'] = next_index
    context.user_data['receipt'] = receipt
    if next_index >= len(lines):
        total = receipt.get('total')
        tline = f'\n💰 <b>Итого: {total:.2f} CAD</b>' if total else ''
        await query.edit_message_text(
            f'✅ <b>Все строки сохранены!</b>{tline}', parse_mode='HTML'
        )
        context.user_data.pop('receipt', None)
    else:
        await query.edit_message_text(
            _line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML'
        )


def _exp_preview(amount: float, description: str, date: str = None) -> str:
    desc     = f'\n📝 {description}' if description else ''
    date_str = f'\n📅 {date}' if date and date != datetime.now().strftime('%Y-%m-%d') else ''
    return f'💰 <b>{amount:.2f} CAD</b>{desc}{date_str}\n\nВыберите тип расхода:'


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


def _build_excel_list(rows: list, record_type: str, marked: bool) -> tuple:
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
        label      = f'✅ #{i}' if not marked else f'↩️ #{i}'
        row.append(InlineKeyboardButton(label, callback_data=f'mxls_{record_type}|{rec_id}'))
        if len(row) == 4:
            btn_rows.append(row); row = []
    if row: btn_rows.append(row)
    if not marked:
        btn_rows.append([InlineKeyboardButton('✅ Отметить все', callback_data=f'mxls_all_{record_type}')])
    return text, InlineKeyboardMarkup(btn_rows) if btn_rows else None


# ── Commands ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '<b>👋 Трекер расходов и доходов</b>\n\n'
        'Нажмите <b>/</b> или кнопку меню для списка команд.\n\n'
        '<b>Быстрый ввод:</b>\n'
        '• <code>250 магнит</code> — добавить расход\n'
        '• <code>250 магнит вчера</code> — вчерашний расход\n'
        '• <code>250 магнит 05.05</code> — расход на конкретную дату\n'
        '• 📸 Фото чека — автоматическое чтение строк',
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
            f'📝 Ввод → <code>{t_input.message_thread_id}</code>\n'
            f'💸 Расходы → <code>{t_expenses.message_thread_id}</code>\n'
            f'💰 Доходы → <code>{t_income.message_thread_id}</code>',
            parse_mode='HTML'
        )
    except Exception as e:
        await msg.edit_text(
            f'❌ <b>Ошибка:</b> <code>{e}</code>\n\n'
            f'Убедитесь что бот является администратором и включены Темы.',
            parse_mode='HTML'
        )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: <code>/add 250 магнит</code>', parse_mode='HTML')
        return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.'); return
    raw_desc = ' '.join(context.args[1:])
    description, date = _parse_date(raw_desc)
    _store_pending(context, amount, description, date)
    await update.message.reply_text(
        _exp_preview(amount, description, date), reply_markup=bill_type_keyboard(), parse_mode='HTML'
    )


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: <code>/income 5000 зарплата</code>', parse_mode='HTML')
        return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.'); return
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
    budgets   = {b[0]: b[1] for b in db.get_all_budgets()}

    text = f'📊 <b>{MONTH_RU[now.month]} {now.year}</b>\n\n'
    if exp_rows:
        text += '<b>💸 Расходы:</b>\n'
        for cat, total, count in exp_rows:
            pct  = (total / total_exp * 100) if total_exp else 0
            line = f'{cat}\n  <code>{total:.2f} CAD</code> ({pct:.0f}%) — {count} оп.'
            budget = budgets.get(cat)
            if budget:
                bpct = (total / budget * 100)
                warn = ' 🚨' if bpct >= 100 else (' ⚠️' if bpct >= 80 else '')
                line += f' | бюджет <code>{budget:.0f}</code>{warn}'
            text += line + '\n\n'
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


async def summary_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows  = db.get_weekly_expenses()
    total = db.get_total_week()
    today = datetime.now().date()
    start = today - timedelta(days=today.weekday())

    text = f'📅 <b>Неделя {start.strftime("%d.%m")} — {today.strftime("%d.%m")}</b>\n\n'
    if rows:
        for cat, amt, count in rows:
            pct   = (amt / total * 100) if total else 0
            text += f'{cat}\n  <code>{amt:.2f} CAD</code> ({pct:.0f}%) — {count} оп.\n\n'
        text += f'━━━━━━━━━━━━━━\n💳 <b>Итого: {total:.2f} CAD</b>'
    else:
        text += '<i>Расходов на этой неделе нет.</i>'
    await update.message.reply_text(text, parse_mode='HTML')


async def summary_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now   = datetime.now()
    rows  = db.get_expenses_by_user()
    total = db.get_total_expenses()

    text = f'👥 <b>{MONTH_RU[now.month]} {now.year} — по пользователям</b>\n\n'
    if rows:
        for username, amt, count in rows:
            pct   = (amt / total * 100) if total else 0
            text += f'👤 <b>{username}</b>\n  <code>{amt:.2f} CAD</code> ({pct:.0f}%) — {count} оп.\n\n'
        text += f'━━━━━━━━━━━━━━\n💳 <b>Итого: {total:.2f} CAD</b>'
    else:
        text += '<i>Расходов нет.</i>'
    await update.message.reply_text(text, parse_mode='HTML')


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now      = datetime.now()
    rows     = db.get_monthly_expenses()
    total    = db.get_total_expenses()
    budgets  = {b[0]: b[1] for b in db.get_all_budgets()}

    if not rows:
        await update.message.reply_text('📊 Нет данных за текущий месяц.')
        return

    msg = await update.message.reply_text('⏳ Генерирую график…')
    try:
        buf = generate_expense_chart(rows, total, f'{MONTH_RU[now.month]} {now.year}', budgets)
        if buf:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=buf,
                caption=f'📈 Расходы за {MONTH_RU[now.month]} {now.year} — <b>{total:.2f} CAD</b>',
                parse_mode='HTML'
            )
            await msg.delete()
        else:
            await msg.edit_text('❌ Не удалось сгенерировать график.')
    except Exception as e:
        logger.error(f'chart error: {e}')
        await msg.edit_text('❌ Ошибка при генерации графика.')


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_recent_expenses(10)
    if not rows:
        await update.message.reply_text('Расходов пока нет.'); return
    text = '<b>📋 Последние расходы:</b>\n\n'
    for amount, cat, desc, date in rows:
        d     = f' — {desc}' if desc else ''
        text += f'• {date} | <code>{amount:.2f}</code> | {cat}{d}\n'
    await update.message.reply_text(text, parse_mode='HTML')


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Пример: <code>/search walmart</code>', parse_mode='HTML')
        return
    keyword = ' '.join(context.args)
    rows    = db.search_expenses(keyword)
    if not rows:
        await update.message.reply_text(f'🔍 По запросу <b>{keyword}</b> ничего не найдено.', parse_mode='HTML')
        return
    text = f'🔍 <b>Результаты: «{keyword}»</b>\n\n'
    for amount, cat, desc, date in rows:
        d     = f' — {desc}' if desc else ''
        text += f'• {date} | <code>{amount:.2f}</code> | {cat}{d}\n'
    await update.message.reply_text(text, parse_mode='HTML')


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row  = db.get_last_expense(user.id)
    if not row:
        await update.message.reply_text('Нечего редактировать.'); return
    exp_id, amount, category, description, date = row
    await update.message.reply_text(
        f'✏️ <b>Последний расход:</b>\n'
        f'💰 <code>{amount:.2f} CAD</code>\n'
        f'🏷 {category}\n'
        f'📝 {description or "—"}\n'
        f'📅 {date}\n\n'
        f'Что изменить?',
        reply_markup=edit_keyboard(exp_id),
        parse_mode='HTML'
    )


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_chat_id, msg_id = db.delete_last_expense(update.effective_user.id)
    if msg_chat_id is None:
        await update.message.reply_text('Нечего удалять.'); return
    if msg_chat_id and msg_id:
        try:
            await context.bot.delete_message(chat_id=msg_chat_id, message_id=msg_id)
        except Exception as e:
            logger.warning(f'delete topic msg: {e}')
    await update.message.reply_text('↩️ Последний расход удалён.')


async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '📊 <b>Статус Excel</b>\n\nЧто показать?',
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton('⬜ Расходы — не в Excel', callback_data='exf|e|0'),
                InlineKeyboardButton('✅ Расходы — в Excel',    callback_data='exf|e|1'),
            ],
            [
                InlineKeyboardButton('⬜ Доходы — не в Excel',  callback_data='exf|i|0'),
                InlineKeyboardButton('✅ Доходы — в Excel',     callback_data='exf|i|1'),
            ],
        ]),
        parse_mode='HTML'
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now      = datetime.now()
    msg      = await update.message.reply_text('⏳ Генерирую CSV…')
    exp_csv  = db.export_expenses_csv()
    inc_csv  = db.export_income_csv()
    fname_e  = f'expenses_{now.strftime("%Y_%m")}.csv'
    fname_i  = f'income_{now.strftime("%Y_%m")}.csv'
    try:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(io.BytesIO(exp_csv.encode('utf-8-sig')), filename=fname_e),
            caption=f'💸 Расходы за {MONTH_RU[now.month]} {now.year}'
        )
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(io.BytesIO(inc_csv.encode('utf-8-sig')), filename=fname_i),
            caption=f'💰 Доходы за {MONTH_RU[now.month]} {now.year}'
        )
        await msg.delete()
    except Exception as e:
        logger.error(f'export error: {e}')
        await msg.edit_text('❌ Ошибка экспорта.')


async def budgets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_all_budgets()
    if not rows:
        await update.message.reply_text(
            '💰 <b>Бюджеты не установлены.</b>\n\nИспользуйте /setbudget чтобы добавить.',
            parse_mode='HTML'
        ); return
    now   = datetime.now()
    text  = f'💰 <b>Бюджеты — {MONTH_RU[now.month]}:</b>\n\n'
    for cat, budget in rows:
        spent = db.get_category_total_month(cat)
        pct   = (spent / budget * 100)
        bar   = '█' * int(pct / 10) + '░' * (10 - int(pct / 10))
        warn  = ' 🚨' if pct >= 100 else (' ⚠️' if pct >= 80 else '')
        text += f'{cat}\n<code>{bar}</code> {pct:.0f}%{warn}\n<code>{spent:.2f}</code> / <code>{budget:.2f} CAD</code>\n\n'
    await update.message.reply_text(text, parse_mode='HTML')


async def setbudget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        # /setbudget 800 → but we need category too — show picker
        try:
            amount = float(context.args[0].replace(',', '.'))
            context.user_data['budget_amount'] = amount
            await update.message.reply_text(
                f'💰 Бюджет: <b>{amount:.2f} CAD</b>\n\nВыберите категорию:',
                reply_markup=budget_keyboard(), parse_mode='HTML'
            )
            return
        except ValueError:
            pass
    await update.message.reply_text(
        'Пример: <code>/setbudget 800</code> → затем выберите категорию',
        parse_mode='HTML'
    )


async def recurring_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_recurring()
    if not rows:
        await update.message.reply_text(
            '🔁 <b>Регулярных расходов нет.</b>\n\n'
            'Добавьте командой:\n<code>/addrecurring 1500 🏠 Дом / Коммунальные Аренда</code>',
            parse_mode='HTML'
        ); return
    text = '🔁 <b>Регулярные расходы</b> (добавляются 1-го числа):\n\n'
    for rec_id, amount, category, description in rows:
        text += f'• <code>{amount:.2f} CAD</code> | {category} | {description or "—"}\n'
    await update.message.reply_text(
        text, reply_markup=recurring_del_keyboard(rows), parse_mode='HTML'
    )


async def addrecurring_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            'Пример: <code>/addrecurring 1500 🏠 Дом / Коммунальные Аренда</code>\n'
            'Формат: /addrecurring <сумма> <категория> [описание]',
            parse_mode='HTML'
        ); return
    try:
        amount = float(context.args[0].replace(',', '.'))
    except ValueError:
        await update.message.reply_text('❌ Неверная сумма.'); return

    # Match category from remaining text
    rest     = ' '.join(context.args[1:])
    category = None
    desc     = rest
    for v in EXP_CATEGORIES.values():
        if v.lower() in rest.lower():
            category = v
            desc     = rest.lower().replace(v.lower(), '').strip()
            break
    if not category:
        context.user_data['pending_recurring'] = {'amount': amount, 'description': rest}
        await update.message.reply_text(
            f'💰 <b>{amount:.2f} CAD</b>\n\nВыберите категорию:',
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(v, callback_data=f'addreccat|{k}')] for k, v in EXP_CATEGORIES.items()]
            ),
            parse_mode='HTML'
        )
        return

    db.add_recurring(amount, category, desc)
    await update.message.reply_text(
        f'✅ <b>Регулярный расход добавлен!</b>\n<code>{amount:.2f} CAD</code> | {category}\n📝 {desc or "—"}',
        parse_mode='HTML'
    )


async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = db.get_config('reminder_enabled') or '1'
    if current == '1':
        db.set_config('reminder_enabled', '0')
        await update.message.reply_text('🔕 Ежедневное напоминание <b>отключено</b>.', parse_mode='HTML')
    else:
        db.set_config('reminder_enabled', '1')
        hour = int(db.get_config('reminder_hour') or 0)
        await update.message.reply_text(
            f'🔔 Ежедневное напоминание <b>включено</b> (00:00 UTC / ~21:00 Halifax).\n'
            f'Для изменения времени: <code>/reminder_time 21</code>',
            parse_mode='HTML'
        )


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = '<b>📋 Категории расходов:</b>\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in EXP_CATEGORIES.items())
    text += '\n\n<b>💵 Категории доходов:</b>\n'
    text += '\n'.join(f'  {k}. {v}' for k, v in INC_CATEGORIES.items())
    await update.message.reply_text(text, parse_mode='HTML')


# ── Text / Photo Handlers ─────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    # ── Combined bill states ───────────────────────────────────────────────
    if context.user_data.get('cb_waiting_total'):
        context.user_data.pop('cb_waiting_total', None)
        try:
            total = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: <code>250.50</code>', parse_mode='HTML')
            return
        cb           = context.user_data.get('combined_bill', {})
        cb['total']  = total
        context.user_data['combined_bill'] = cb
        await update.message.reply_text(
            _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('cb_waiting_pos_amount'):
        context.user_data.pop('cb_waiting_pos_amount', None)
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число.', parse_mode='HTML')
            return
        context.user_data['cb_pos_amount'] = amount
        await update.message.reply_text(
            f'💰 <b>{amount:.2f} CAD</b>\n\nВыберите категорию:',
            reply_markup=cb_pos_cat_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('cb_waiting_pos_desc'):
        context.user_data.pop('cb_waiting_pos_desc', None)
        cb  = context.user_data.get('combined_bill', {})
        pos = context.user_data.pop('cb_pending_pos', {})
        pos['description'] = '' if text.strip() == '-' else text
        cb.setdefault('positions', []).append(pos)
        context.user_data['combined_bill'] = cb
        await update.message.reply_text(
            _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('cb_waiting_edit_amount') is not None:
        idx = context.user_data.pop('cb_waiting_edit_amount')
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число.'); return
        cb = context.user_data.get('combined_bill', {})
        cb['positions'][idx]['amount'] = amount
        context.user_data['combined_bill'] = cb
        await update.message.reply_text(
            _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('cb_waiting_edit_desc') is not None:
        idx = context.user_data.pop('cb_waiting_edit_desc')
        cb  = context.user_data.get('combined_bill', {})
        cb['positions'][idx]['description'] = text
        context.user_data['combined_bill'] = cb
        await update.message.reply_text(
            _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
        )
        return

    # ── Regular expense states ─────────────────────────────────────────────
    if context.user_data.get('waiting_description'):
        context.user_data.pop('waiting_description', None)
        await _finalize_expense(update.message, context, user, description_override=text)
        return

    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число: <code>45.50</code>', parse_mode='HTML')
            return
        context.user_data.pop('waiting_for_amount', None)
        _store_pending(context, amount, 'Чек (фото)')
        await update.message.reply_text(
            _exp_preview(amount, 'Чек (фото)'), reply_markup=bill_type_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('waiting_split_amount'):
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Введите только число.', parse_mode='HTML')
            return
        context.user_data.pop('waiting_split_amount', None)
        last = context.user_data.get('last_saved', {})
        _store_pending(context, amount, last.get('description', ''))
        await update.message.reply_text(
            _exp_preview(amount, last.get('description', '')),
            reply_markup=bill_type_keyboard(), parse_mode='HTML'
        )
        return

    if context.user_data.get('waiting_edit_amount'):
        exp_id = context.user_data.pop('waiting_edit_amount')
        try:
            amount = float(text.replace(',', '.'))
        except ValueError:
            await update.message.reply_text('❌ Неверная сумма.'); return
        db.update_expense(exp_id, amount=amount)
        await update.message.reply_text(f'✅ Сумма обновлена: <code>{amount:.2f} CAD</code>', parse_mode='HTML')
        return

    if context.user_data.get('waiting_edit_desc'):
        exp_id = context.user_data.pop('waiting_edit_desc')
        db.update_expense(exp_id, description=text)
        await update.message.reply_text(f'✅ Описание обновлено: <i>{text}</i>', parse_mode='HTML')
        return

    # Natural text: "250 магнит" / "250 магнит вчера" / "250 магнит 05.05"
    match = re.match(r'^(\d+(?:[.,]\d+)?)\s*(.*)$', text)
    if not match:
        return
    try:
        amount = float(match.group(1).replace(',', '.'))
    except ValueError:
        return
    raw_desc          = match.group(2).strip()
    description, date = _parse_date(raw_desc)
    _store_pending(context, amount, description, date)
    await update.message.reply_text(
        _exp_preview(amount, description, date),
        reply_markup=bill_type_keyboard(),
        parse_mode='HTML'
    )




async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status      = await update.message.reply_text('📸 Читаю чек, подождите…')
    photo       = update.message.photo[-1]
    photo_file  = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await photo_file.download_as_bytearray())
    ocr_text, lines, total = extract_from_image(photo_bytes)
    today = datetime.now().strftime('%Y-%m-%d')

    if lines:
        # Auto-load all lines into combined bill
        # Category defaults to 📦 Другое — user will edit before saving
        positions = [
            {
                'amount':      item['amount'],
                'category':    '📦 Другое',
                'description': item['name'],
            }
            for item in lines
        ]
        cb = {
            'total':       total or round(sum(p['amount'] for p in positions), 2),
            'description': 'Чек (фото)',
            'date':        today,
            'positions':   positions,
        }
        context.user_data['combined_bill'] = cb
        await status.edit_text(
            f'📄 <b>Чек прочитан — {len(lines)} позиций</b>\n\n'
            + _cb_summary_text(cb)
            + '\n\n<i>Измените названия и категории, затем нажмите ✅ Завершить.</i>',
            reply_markup=combined_bill_keyboard(),
            parse_mode='HTML'
        )

    elif total:
        # No line items — single amount confirmation
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
            f'📄 <b>Текст с чека:</b>\n<code>{raw}</code>\n\n❌ Сумма не найдена. Введите вручную:',
            parse_mode='HTML'
        )




# ── Callback Handler ──────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    logger.info(f'CB [{user.username or user.id}]: {data}')

    try:
        # ── Bill type choice ──────────────────────────────────────────────
        if data == 'bill_single':
            # Regular expense — proceed to category keyboard
            pending = context.user_data.get('pending')
            if not pending:
                await query.edit_message_text('❌ Сессия истекла.'); return
            await query.edit_message_text(
                f'💰 <b>{pending["amount"]:.2f} CAD</b>'
                + (f'\n📝 {pending["description"]}' if pending.get('description') else '')
                + '\n\nВыберите категорию:',
                reply_markup=cat_keyboard(), parse_mode='HTML'
            )

        elif data == 'bill_combined':
            pending = context.user_data.pop('pending', {})
            context.user_data['combined_bill'] = {
                'total':       pending.get('amount', 0.0),
                'description': pending.get('description', ''),
                'date':        pending.get('date', datetime.now().strftime('%Y-%m-%d')),
                'positions':   [],
            }
            cb = context.user_data['combined_bill']
            await query.edit_message_text(
                _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
            )

        # ── Combined bill actions ──────────────────────────────────────────
        elif data == 'cb_add':
            context.user_data['cb_waiting_pos_amount'] = True
            await query.edit_message_text('➕ Введите сумму позиции:')

        elif data.startswith('cb_poscat|'):
            cat_key  = data.split('|')[1]
            category = EXP_CATEGORIES.get(cat_key, '📦 Другое')
            amount   = context.user_data.pop('cb_pos_amount', 0.0)
            context.user_data['cb_pending_pos']       = {'amount': amount, 'category': category}
            context.user_data['cb_waiting_pos_desc']  = True
            await query.edit_message_text(
                f'💰 {amount:.2f} CAD  |  {category}\n\n📝 Введите описание или отправьте <code>-</code> чтобы пропустить:',
                parse_mode='HTML'
            )

        elif data == 'cb_del_menu':
            cb = context.user_data.get('combined_bill', {})
            positions = cb.get('positions', [])
            if not positions:
                await query.answer('Нет позиций для удаления.'); return
            await query.edit_message_text(
                '🗑 <b>Выберите позицию для удаления:</b>',
                reply_markup=cb_delete_keyboard(positions), parse_mode='HTML'
            )

        elif data.startswith('cb_del|'):
            idx = int(data.split('|')[1])
            cb  = context.user_data.get('combined_bill', {})
            positions = cb.get('positions', [])
            if 0 <= idx < len(positions):
                removed = positions.pop(idx)
                cb['positions'] = positions
                context.user_data['combined_bill'] = cb
                await query.answer(f'✅ Удалено: {removed["category"]}')
            await query.edit_message_text(
                _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
            )

        elif data == 'cb_edit_menu':
            cb = context.user_data.get('combined_bill', {})
            positions = cb.get('positions', [])
            if not positions:
                await query.answer('Нет позиций для редактирования.'); return
            await query.edit_message_text(
                '✏️ <b>Выберите позицию для редактирования:</b>',
                reply_markup=cb_edit_keyboard(positions), parse_mode='HTML'
            )

        elif data.startswith('cb_edit|'):
            idx = int(data.split('|')[1])
            await query.edit_message_text(
                f'✏️ <b>Позиция #{idx+1}</b> — что изменить?',
                reply_markup=cb_edit_field_keyboard(idx), parse_mode='HTML'
            )

        elif data.startswith('cb_editamt|'):
            idx = int(data.split('|')[1])
            context.user_data['cb_waiting_edit_amount'] = idx
            await query.edit_message_text(f'💰 Введите новую сумму для позиции #{idx+1}:')

        elif data.startswith('cb_editcat|'):
            idx = int(data.split('|')[1])
            await query.edit_message_text(
                f'🏷 Выберите новую категорию для позиции #{idx+1}:',
                reply_markup=cb_cat_keyboard(idx)
            )

        elif data.startswith('cb_setcat|'):
            parts    = data.split('|')
            idx      = int(parts[1])
            category = EXP_CATEGORIES.get(parts[2], '📦 Другое')
            cb       = context.user_data.get('combined_bill', {})
            cb['positions'][idx]['category'] = category
            context.user_data['combined_bill'] = cb
            await query.edit_message_text(
                _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
            )

        elif data.startswith('cb_editdesc|'):
            idx = int(data.split('|')[1])
            context.user_data['cb_waiting_edit_desc'] = idx
            await query.edit_message_text(f'📝 Введите новое описание для позиции #{idx+1}:')

        elif data == 'cb_back':
            cb = context.user_data.get('combined_bill', {})
            await query.edit_message_text(
                _cb_summary_text(cb), reply_markup=combined_bill_keyboard(), parse_mode='HTML'
            )

        elif data == 'cb_finish':
            cb = context.user_data.get('combined_bill', {})
            if not cb.get('positions'):
                await query.answer('❌ Добавьте хотя бы одну позицию.'); return
            await _finalize_combined_bill(query, context, user)

        elif data == 'cb_cancel':
            context.user_data.pop('combined_bill', None)
            await query.edit_message_text('❌ Общий счёт отменён.')

        # ── Receipt ───────────────────────────────────────────────────────
        if data == 'rl_start':
            receipt = context.user_data.get('receipt')
            if not receipt or not receipt.get('lines'):
                await query.edit_message_text('❌ Нет данных о строках.'); return
            receipt['index'] = 0
            context.user_data['receipt'] = receipt
            await query.edit_message_text(_line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML')

        elif data == 'rl_one':
            amount = context.user_data.pop('ocr_amount', None) or context.user_data.get('receipt', {}).get('total')
            context.user_data.pop('receipt', None)
            if not amount:
                await query.edit_message_text('❌ Нет данных.'); return
            _store_pending(context, amount, 'Чек (фото)')
            await query.edit_message_text(_exp_preview(amount, 'Чек (фото)'), reply_markup=bill_type_keyboard(), parse_mode='HTML')

        elif data.startswith('rlcat|'):
            await _save_and_next_line(query, context, EXP_CATEGORIES.get(data.split('|')[1], '📦 Другое'))

        elif data == 'rl_skip':
            receipt = context.user_data.get('receipt', {})
            lines   = receipt.get('lines', [])
            index   = receipt.get('index', 0)
            receipt['index'] = index + 1
            context.user_data['receipt'] = receipt
            if index + 1 >= len(lines):
                await query.edit_message_text('✅ Готово.')
                context.user_data.pop('receipt', None)
            else:
                await query.edit_message_text(_line_prompt(context), reply_markup=line_cat_keyboard(), parse_mode='HTML')

        elif data == 'rl_done':
            context.user_data.pop('receipt', None)
            await query.edit_message_text('✅ Готово.')

        # ── Expense category → ask description ───────────────────────────
        elif data.startswith('c|'):
            category = EXP_CATEGORIES.get(data.split('|')[1], '📦 Другое')
            pending  = context.user_data.get('pending')
            if not pending:
                await query.edit_message_text('❌ Сессия истекла.'); return
            pending['category'] = category
            context.user_data['pending']             = pending
            context.user_data['waiting_description'] = True
            existing = f'\nУже есть: <i>{pending["description"]}</i>' if pending.get('description') else ''
            await query.edit_message_text(
                f'💰 {pending["amount"]:.2f} CAD  |  {category}{existing}\n\n📝 Добавьте описание или пропустите:',
                reply_markup=desc_keyboard(), parse_mode='HTML'
            )

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

        # ── OCR ───────────────────────────────────────────────────────────
        elif data == 'ocr_ok':
            amount = context.user_data.pop('ocr_amount', None)
            if not amount:
                await query.edit_message_text('❌ Сессия истекла.'); return
            _store_pending(context, amount, 'Чек (фото)')
            await query.edit_message_text(_exp_preview(amount, 'Чек (фото)'), reply_markup=bill_type_keyboard(), parse_mode='HTML')

        elif data == 'ocr_edit':
            context.user_data.pop('ocr_amount', None)
            context.user_data['waiting_for_amount'] = True
            await query.edit_message_text('✏️ Введите сумму вручную:')

        # ── Income ────────────────────────────────────────────────────────
        elif data.startswith('ic|'):
            category = INC_CATEGORIES.get(data.split('|')[1], '💵 Другой доход')
            pending  = context.user_data.get('pending_income')
            if not pending:
                await query.edit_message_text('❌ Сессия истекла.'); return
            inc_id = db.add_income(
                user_id=user.id, username=user.username or user.first_name,
                amount=pending['amount'], category=category,
                description=pending['description'], date=pending['date'],
            )
            await _post_income(context, inc_id, pending['amount'], category,
                               pending['description'], pending['date'],
                               user.username or user.first_name)
            context.user_data.pop('pending_income', None)
            await query.edit_message_text(
                f'✅ <b>Доход сохранён!</b>\n💵 {pending["amount"]:.2f} CAD\n🏷 {category}\n📝 {pending["description"] or "—"}',
                parse_mode='HTML'
            )

        # ── Edit expense ──────────────────────────────────────────────────
        elif data.startswith('edit_amt|'):
            exp_id = int(data.split('|')[1])
            context.user_data['waiting_edit_amount'] = exp_id
            await query.edit_message_text('✏️ Введите новую сумму:')

        elif data.startswith('edit_cat|'):
            exp_id = int(data.split('|')[1])
            await query.edit_message_text(
                '🏷 Выберите новую категорию:',
                reply_markup=cat_keyboard_edit(exp_id)
            )

        elif data.startswith('edit_desc|'):
            exp_id = int(data.split('|')[1])
            context.user_data['waiting_edit_desc'] = exp_id
            await query.edit_message_text('📝 Введите новое описание:')

        elif data.startswith('edit_setcat|'):
            parts    = data.split('|')
            exp_id   = int(parts[1])
            category = EXP_CATEGORIES.get(parts[2], '📦 Другое')
            db.update_expense(exp_id, category=category)
            await query.edit_message_text(f'✅ Категория обновлена: {category}')

        elif data == 'edit_cancel':
            await query.edit_message_text('❌ Редактирование отменено.')

        # ── Budget setter ─────────────────────────────────────────────────
        elif data.startswith('budget_set|'):
            cat_key  = data.split('|')[1]
            category = EXP_CATEGORIES.get(cat_key, '📦 Другое')
            amount   = context.user_data.pop('budget_amount', None)
            if not amount:
                await query.edit_message_text('❌ Сессия истекла.'); return
            db.set_budget(category, amount)
            await query.edit_message_text(
                f'✅ <b>Бюджет установлен!</b>\n{category}: <code>{amount:.2f} CAD/мес</code>',
                parse_mode='HTML'
            )

        # ── Recurring ─────────────────────────────────────────────────────
        elif data.startswith('addreccat|'):
            cat_key  = data.split('|')[1]
            category = EXP_CATEGORIES.get(cat_key, '📦 Другое')
            pending  = context.user_data.pop('pending_recurring', {})
            db.add_recurring(pending.get('amount', 0), category, pending.get('description', ''))
            await query.edit_message_text(
                f'✅ <b>Регулярный расход добавлен!</b>\n<code>{pending.get("amount", 0):.2f} CAD</code> | {category}',
                parse_mode='HTML'
            )

        elif data.startswith('del_rec|'):
            rec_id = int(data.split('|')[1])
            db.delete_recurring(rec_id)
            await query.answer('✅ Удалено')
            rows = db.get_recurring()
            if rows:
                await query.edit_message_reply_markup(reply_markup=recurring_del_keyboard(rows))
            else:
                await query.edit_message_text('🔁 <b>Список регулярных расходов пуст.</b>', parse_mode='HTML')

        elif data == 'rec_close':
            await query.edit_message_reply_markup(reply_markup=None)

        # ── Excel filter ──────────────────────────────────────────────────
        elif data.startswith('exf|'):
            _, rtype, mval = data.split('|')
            marked = mval == '1'
            rows   = db.get_expenses_by_excel(marked) if rtype == 'e' else db.get_income_by_excel(marked)
            text, markup = _build_excel_list(rows, rtype, marked)
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')

        elif data.startswith('mxls_all_'):
            rtype = data[-1]
            if rtype == 'e': db.mark_all_expenses_excel(True)
            else:            db.mark_all_income_excel(True)
            await query.edit_message_text('✅ <b>Все записи отмечены как В Excel.</b>', parse_mode='HTML')

        elif data.startswith('xls_e|'):
            exp_id = int(data.split('|')[1])
            row    = db.get_expense(exp_id)
            if not row: return
            new = not bool(row[5])
            db.mark_expense_excel(exp_id, new)
            await query.edit_message_reply_markup(reply_markup=excel_btn('e', exp_id, new))

        elif data.startswith('xls_i|'):
            inc_id = int(data.split('|')[1])
            row    = db.get_income(inc_id)
            if not row: return
            new = not bool(row[5])
            db.mark_income_excel(inc_id, new)
            await query.edit_message_reply_markup(reply_markup=excel_btn('i', inc_id, new))

        elif data.startswith('mxls_e|'):
            exp_id = int(data.split('|')[1])
            row    = db.get_expense(exp_id)
            if not row: return
            new = not bool(row[5])
            db.mark_expense_excel(exp_id, new)
            await query.answer('✅ Отмечено' if new else '↩️ Снято')

        elif data.startswith('mxls_i|'):
            inc_id = int(data.split('|')[1])
            row    = db.get_income(inc_id)
            if not row: return
            new = not bool(row[5])
            db.mark_income_excel(inc_id, new)
            await query.answer('✅ Отмечено' if new else '↩️ Снято')

    except Exception as e:
        logger.error(f'CB error [{data}]: {e}', exc_info=True)


# ── Scheduled Jobs ────────────────────────────────────────────────────────────
async def job_daily_reminder(context):
    """Send daily reminder if no expenses were added today."""
    enabled = db.get_config('reminder_enabled') or '1'
    if enabled != '1':
        return
    group_id = db.get_config('group_id')
    topic_id = db.get_config('topic_input')
    if not group_id or not topic_id:
        return
    count = db.get_today_expense_count()
    if count == 0:
        try:
            await context.bot.send_message(
                chat_id=int(group_id),
                message_thread_id=int(topic_id),
                text='🔔 <b>Напоминание!</b>\n\nСегодня расходы ещё не добавлены. Не забудьте записать траты за день! 💸',
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f'Reminder error: {e}')


async def job_recurring(context):
    """Add recurring expenses on the 1st of each month."""
    rows = db.get_recurring()
    if not rows:
        return
    date = datetime.now().strftime('%Y-%m-%d')
    for rec_id, amount, category, description in rows:
        exp_id = db.add_expense(
            user_id=0, username='🔁 Авто',
            amount=amount, category=category,
            description=description, date=date,
        )
        await _post_expense(context, exp_id, amount, category, description, date, '🔁 Авто')

    group_id = db.get_config('group_id')
    topic_id = db.get_config('topic_input')
    if group_id and topic_id and int(topic_id) > 0:
        try:
            await context.bot.send_message(
                chat_id=int(group_id),
                message_thread_id=int(topic_id),
                text=f'🔁 <b>Регулярные расходы добавлены</b> ({len(rows)} записей)',
                parse_mode='HTML'
            )
        except Exception:
            pass


# ── Setup & Main ──────────────────────────────────────────────────────────────
async def post_init(application: Application):
    # Set menu for private chats AND group chats
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())

    # Daily reminder — 00:00 UTC (≈ 21:00 Halifax ADT)
    application.job_queue.run_daily(
        job_daily_reminder,
        time=dt_time(hour=0, minute=0),
    )
    # Recurring expenses — 1st of each month at 09:00 UTC
    application.job_queue.run_monthly(
        job_recurring,
        when=dt_time(hour=9, minute=0),
        day=1,
    )
    logger.info('Jobs and commands registered.')


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler('start',           start))
    app.add_handler(CommandHandler('help',            start))
    app.add_handler(CommandHandler('create_topics',   create_topics_command))
    app.add_handler(CommandHandler('add',             add_command))
    app.add_handler(CommandHandler('income',          income_command))
    app.add_handler(CommandHandler('summary',         summary_command))
    app.add_handler(CommandHandler('summary_week',    summary_week_command))
    app.add_handler(CommandHandler('summary_users',   summary_users_command))
    app.add_handler(CommandHandler('chart',           chart_command))
    app.add_handler(CommandHandler('last',            last_command))
    app.add_handler(CommandHandler('search',          search_command))
    app.add_handler(CommandHandler('edit',            edit_command))
    app.add_handler(CommandHandler('undo',            undo_command))
    app.add_handler(CommandHandler('excel',           excel_command))
    app.add_handler(CommandHandler('export',          export_command))
    app.add_handler(CommandHandler('budgets',         budgets_command))
    app.add_handler(CommandHandler('setbudget',       setbudget_command))
    app.add_handler(CommandHandler('recurring',       recurring_command))
    app.add_handler(CommandHandler('addrecurring',    addrecurring_command))
    app.add_handler(CommandHandler('reminder',        reminder_command))
    app.add_handler(CommandHandler('categories',      categories_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO,                      handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,    handle_text))

    logger.info('Bot started.')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
