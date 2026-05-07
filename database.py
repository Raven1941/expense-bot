import sqlite3
from datetime import datetime


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

    def delete_last_expense(self, user_id):
        """
        Delete the most recent expense for a user.
        Returns (msg_chat_id, msg_id) so the caller can delete the Telegram message,
        or (None, None) if nothing was deleted.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT id, msg_chat_id, msg_id FROM expenses WHERE user_id=? ORDER BY created_at DESC LIMIT 1',
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
                '''SELECT category, SUM(amount), COUNT(*) FROM expenses
                   WHERE date LIKE ? GROUP BY category ORDER BY SUM(amount) DESC''',
                (f"{y}-{m:02d}%",)
            )
            return cur.fetchall()

    def get_total_expenses(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT SUM(amount) FROM expenses WHERE date LIKE ?', (f"{y}-{m:02d}%",))
            r = cur.fetchone()
            return r[0] or 0.0

    def get_recent_expenses(self, limit=10):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'SELECT amount,category,description,date FROM expenses ORDER BY created_at DESC LIMIT ?', (limit,)
            )
            return cur.fetchall()

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
                '''SELECT category, SUM(amount), COUNT(*) FROM income
                   WHERE date LIKE ? GROUP BY category ORDER BY SUM(amount) DESC''',
                (f"{y}-{m:02d}%",)
            )
            return cur.fetchall()

    def get_total_income(self, year=None, month=None):
        y, m = year or datetime.now().year, month or datetime.now().month
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT SUM(amount) FROM income WHERE date LIKE ?', (f"{y}-{m:02d}%",))
            r = cur.fetchone()
            return r[0] or 0.0

    def get_expenses_by_excel(self, marked: bool, limit=20):
        """Get expenses filtered by excel status."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                '''SELECT id, amount, category, description, date
                   FROM expenses WHERE excel_marked=?
                   ORDER BY created_at DESC LIMIT ?''',
                (1 if marked else 0, limit)
            )
            return cur.fetchall()

    def get_income_by_excel(self, marked: bool, limit=20):
        """Get income filtered by excel status."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                '''SELECT id, amount, category, description, date
                   FROM income WHERE excel_marked=?
                   ORDER BY created_at DESC LIMIT ?''',
                (1 if marked else 0, limit)
            )
            return cur.fetchall()

    def mark_all_expenses_excel(self, marked=True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE expenses SET excel_marked=? WHERE excel_marked=?',
                         (1 if marked else 0, 0 if marked else 1))
            conn.commit()

    def mark_all_income_excel(self, marked=True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE income SET excel_marked=? WHERE excel_marked=?',
                         (1 if marked else 0, 0 if marked else 1))
            conn.commit()

    # ── Config ────────────────────────────────────────────────────────────────
    def set_config(self, key, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR REPLACE INTO config (key,value) VALUES (?,?)', (key, str(value)))
            conn.commit()

    def get_config(self, key):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT value FROM config WHERE key=?', (key,))
            r = cur.fetchone()
            return r[0] if r else None
