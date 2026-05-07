import sqlite3
import csv
import io
from datetime import datetime, timedelta


class Database:
    def __init__(self, db_path='expenses.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS expenses (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER,
                    username     TEXT,
                    amount       REAL,
                    category     TEXT,
                    description  TEXT,
                    date         TEXT,
                    excel_marked INTEGER DEFAULT 0,
                    msg_chat_id  INTEGER,
                    msg_id       INTEGER,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS income (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER,
                    username     TEXT,
                    amount       REAL,
                    category     TEXT,
                    description  TEXT,
                    date         TEXT,
                    excel_marked INTEGER DEFAULT 0,
                    msg_chat_id  INTEGER,
                    msg_id       INTEGER,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT PRIMARY KEY,
                    amount   REAL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS recurring (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount      REAL,
                    category    TEXT,
                    description TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            conn.commit()

    # ── Expenses ──────────────────────────────────────────────────────────────
    def add_expense(self, user_id, username, amount, category, description, date=None):
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'INSERT INTO expenses (user_id,username,amount,category,description,date) VALUES (?,?,?,?,?,?)',
                (user_id, username, amount, category, description, date)
            )
            conn.commit()
            return cur.lastrowid

    def set_expense_message(self, exp_id, chat_id, msg_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE expenses SET msg_chat_id=?,msg_id=? WHERE id=?', (chat_id, msg_id, exp_id))
            conn.commit()

    def mark_expense_excel(self, exp_id, marked):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE expenses SET excel_marked=? WHERE id=?', (1 if marked else 0, exp_id))
            conn.commit()

    def get_expense(self, exp_id):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,amount,category,description,date,excel_marked FROM expenses WHERE id=?', (exp_id,)
            )
            return cur.fetchone()

    def get_last_expense(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,amount,category,description,date FROM expenses WHERE user_id=? ORDER BY created_at DESC LIMIT 1',
                (user_id,)
            )
            return cur.fetchone()

    def update_expense(self, exp_id, amount=None, category=None, description=None):
        with sqlite3.connect(self.db_path) as conn:
            if amount is not None:
                conn.execute('UPDATE expenses SET amount=? WHERE id=?', (amount, exp_id))
            if category is not None:
                conn.execute('UPDATE expenses SET category=? WHERE id=?', (category, exp_id))
            if description is not None:
                conn.execute('UPDATE expenses SET description=? WHERE id=?', (description, exp_id))
            conn.commit()

    def delete_last_expense(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,msg_chat_id,msg_id FROM expenses WHERE user_id=? ORDER BY created_at DESC LIMIT 1',
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                exp_id, msg_chat_id, msg_id = row
                conn.execute('DELETE FROM expenses WHERE id=?', (exp_id,))
                conn.commit()
                return msg_chat_id, msg_id
        return None, None

    def get_monthly_expenses(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT category,SUM(amount),COUNT(*) FROM expenses WHERE date LIKE ? GROUP BY category ORDER BY SUM(amount) DESC',
                (f'{y}-{m:02d}%',)
            )
            return cur.fetchall()

    def get_total_expenses(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT SUM(amount) FROM expenses WHERE date LIKE ?', (f'{y}-{m:02d}%',))
            r = cur.fetchone(); return r[0] or 0.0

    def get_weekly_expenses(self):
        today     = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT category,SUM(amount),COUNT(*) FROM expenses WHERE date >= ? GROUP BY category ORDER BY SUM(amount) DESC',
                (week_start.strftime('%Y-%m-%d'),)
            )
            return cur.fetchall()

    def get_total_week(self):
        today      = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT SUM(amount) FROM expenses WHERE date >= ?', (week_start.strftime('%Y-%m-%d'),)
            )
            r = cur.fetchone(); return r[0] or 0.0

    def get_recent_expenses(self, limit=10):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT amount,category,description,date FROM expenses ORDER BY created_at DESC LIMIT ?', (limit,)
            )
            return cur.fetchall()

    def search_expenses(self, keyword, limit=20):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                '''SELECT amount,category,description,date FROM expenses
                   WHERE lower(description) LIKE lower(?) OR lower(category) LIKE lower(?)
                   ORDER BY created_at DESC LIMIT ?''',
                (f'%{keyword}%', f'%{keyword}%', limit)
            )
            return cur.fetchall()

    def get_expenses_by_user(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT username,SUM(amount),COUNT(*) FROM expenses WHERE date LIKE ? GROUP BY username ORDER BY SUM(amount) DESC',
                (f'{y}-{m:02d}%',)
            )
            return cur.fetchall()

    def get_category_total_month(self, category, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT SUM(amount) FROM expenses WHERE category=? AND date LIKE ?',
                (category, f'{y}-{m:02d}%')
            )
            r = cur.fetchone(); return r[0] or 0.0

    def get_expenses_by_excel(self, marked: bool, limit=20):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,amount,category,description,date FROM expenses WHERE excel_marked=? ORDER BY created_at DESC LIMIT ?',
                (1 if marked else 0, limit)
            )
            return cur.fetchall()

    def mark_all_expenses_excel(self, marked=True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE expenses SET excel_marked=? WHERE excel_marked=?',
                         (1 if marked else 0, 0 if marked else 1))
            conn.commit()

    def export_expenses_csv(self, year=None, month=None) -> str:
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT date,amount,category,description,username,excel_marked FROM expenses WHERE date LIKE ? ORDER BY date,created_at',
                (f'{y}-{m:02d}%',)
            )
            rows = cur.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Date', 'Amount (CAD)', 'Category', 'Description', 'User', 'In Excel'])
        for r in rows:
            writer.writerow([r[0], f'{r[1]:.2f}', r[2], r[3] or '', r[4] or '', 'Yes' if r[5] else 'No'])
        return output.getvalue()

    def export_income_csv(self, year=None, month=None) -> str:
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT date,amount,category,description,username,excel_marked FROM income WHERE date LIKE ? ORDER BY date,created_at',
                (f'{y}-{m:02d}%',)
            )
            rows = cur.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Date', 'Amount (CAD)', 'Category', 'Description', 'User', 'In Excel'])
        for r in rows:
            writer.writerow([r[0], f'{r[1]:.2f}', r[2], r[3] or '', r[4] or '', 'Yes' if r[5] else 'No'])
        return output.getvalue()

    def get_today_expense_count(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT COUNT(*) FROM expenses WHERE date=?', (today,))
            r = cur.fetchone(); return r[0] or 0

    # ── Income ────────────────────────────────────────────────────────────────
    def add_income(self, user_id, username, amount, category, description, date=None):
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'INSERT INTO income (user_id,username,amount,category,description,date) VALUES (?,?,?,?,?,?)',
                (user_id, username, amount, category, description, date)
            )
            conn.commit()
            return cur.lastrowid

    def set_income_message(self, inc_id, chat_id, msg_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE income SET msg_chat_id=?,msg_id=? WHERE id=?', (chat_id, msg_id, inc_id))
            conn.commit()

    def mark_income_excel(self, inc_id, marked):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE income SET excel_marked=? WHERE id=?', (1 if marked else 0, inc_id))
            conn.commit()

    def get_income(self, inc_id):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,amount,category,description,date,excel_marked FROM income WHERE id=?', (inc_id,)
            )
            return cur.fetchone()

    def get_monthly_income(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT category,SUM(amount),COUNT(*) FROM income WHERE date LIKE ? GROUP BY category ORDER BY SUM(amount) DESC',
                (f'{y}-{m:02d}%',)
            )
            return cur.fetchall()

    def get_total_income(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT SUM(amount) FROM income WHERE date LIKE ?', (f'{y}-{m:02d}%',))
            r = cur.fetchone(); return r[0] or 0.0

    def get_income_by_excel(self, marked: bool, limit=20):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id,amount,category,description,date FROM income WHERE excel_marked=? ORDER BY created_at DESC LIMIT ?',
                (1 if marked else 0, limit)
            )
            return cur.fetchall()

    def mark_all_income_excel(self, marked=True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE income SET excel_marked=? WHERE excel_marked=?',
                         (1 if marked else 0, 0 if marked else 1))
            conn.commit()

    # ── Budgets ───────────────────────────────────────────────────────────────
    def set_budget(self, category: str, amount: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR REPLACE INTO budgets (category,amount) VALUES (?,?)', (category, amount))
            conn.commit()

    def get_budget(self, category: str):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT amount FROM budgets WHERE category=?', (category,))
            r = cur.fetchone(); return r[0] if r else None

    def get_all_budgets(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT category,amount FROM budgets ORDER BY category')
            return cur.fetchall()

    def delete_budget(self, category: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM budgets WHERE category=?', (category,))
            conn.commit()

    # ── Recurring ─────────────────────────────────────────────────────────────
    def add_recurring(self, amount: float, category: str, description: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'INSERT INTO recurring (amount,category,description) VALUES (?,?,?)',
                (amount, category, description)
            )
            conn.commit()
            return cur.lastrowid

    def get_recurring(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT id,amount,category,description FROM recurring ORDER BY id')
            return cur.fetchall()

    def delete_recurring(self, rec_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM recurring WHERE id=?', (rec_id,))
            conn.commit()

    # ── Config ────────────────────────────────────────────────────────────────
    def set_config(self, key, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR REPLACE INTO config (key,value) VALUES (?,?)', (key, str(value)))
            conn.commit()

    def get_config(self, key):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT value FROM config WHERE key=?', (key,))
            r = cur.fetchone(); return r[0] if r else None
