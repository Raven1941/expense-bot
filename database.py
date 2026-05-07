import sqlite3
import os
from datetime import datetime


class Database:
    def __init__(self, db_path='expenses.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS expenses (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER,
                    username    TEXT,
                    amount      REAL,
                    category    TEXT,
                    description TEXT,
                    date        TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def add_expense(self, user_id, username, amount, category, description, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO expenses (user_id, username, amount, category, description, date) VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, username, amount, category, description, date)
            )
            conn.commit()

    def delete_last(self, user_id):
        """Delete the most recent expense for a user."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT id FROM expenses WHERE user_id = ? ORDER BY created_at DESC LIMIT 1',
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                conn.execute('DELETE FROM expenses WHERE id = ?', (row[0],))
                conn.commit()
                return True
        return False

    def get_monthly_summary(self, year=None, month=None):
        if year is None:
            year = datetime.now().year
        if month is None:
            month = datetime.now().month
        month_str = f"{year}-{month:02d}"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                '''SELECT category, SUM(amount) as total, COUNT(*) as count
                   FROM expenses
                   WHERE date LIKE ?
                   GROUP BY category
                   ORDER BY total DESC''',
                (f"{month_str}%",)
            )
            return cursor.fetchall()

    def get_total_for_month(self, year=None, month=None):
        if year is None:
            year = datetime.now().year
        if month is None:
            month = datetime.now().month
        month_str = f"{year}-{month:02d}"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT SUM(amount) FROM expenses WHERE date LIKE ?',
                (f"{month_str}%",)
            )
            result = cursor.fetchone()
            return result[0] or 0.0

    def get_recent_expenses(self, limit=10):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT amount, category, description, date FROM expenses ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return cursor.fetchall()
