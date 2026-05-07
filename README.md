# 💰 Expense Tracker Bot — Setup Guide

## Step 1 — Create Telegram Bot (BotFather)

1. Open Telegram → search **@BotFather**
2. Send: `/newbot`
3. Enter a name: e.g. `Family Budget`
4. Enter a username: e.g. `yev_family_budget_bot` (must end in `bot`)
5. BotFather gives you a **token** — copy it, looks like:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

## Step 2 — Enable Forum Topics in your Telegram Group

1. Create a new Telegram Group
2. Go to Group Settings → Edit → **Topics → Enable**
3. Add your bot to the group as **Admin** (allow all permissions)
4. Create two topics inside the group:
   - `💰 Расходы` — for typing expenses
   - `🧾 Чеки` — for sending receipt photos
   *(The bot works in both — no extra config needed)*

## Step 3 — Get free OCR API key (for photo reading)

1. Go to: https://ocr.space/ocrapi/freekey
2. Enter your email → get free API key
3. Free tier: **25,000 requests/month** — enough for daily use

## Step 4 — Deploy to Railway.app

1. Go to https://railway.app → Sign up (free)
2. New Project → **Deploy from GitHub repo**
   - Or: New Project → **Empty Project** → upload files manually
3. In your project → **Variables** tab, add:
   ```
   BOT_TOKEN = your_token_here
   OCR_API_KEY = your_ocr_key_here
   ```
4. Railway auto-detects `Procfile` and starts the bot
5. Done ✅

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/add 250 магнит` | Add expense manually |
| `/summary` | Monthly spending by category |
| `/last` | Last 10 expenses |
| `/undo` | Delete last expense |
| `/categories` | Show all categories |

## Natural Input (no command needed)

Just type in the chat:
- `250` → prompts for category
- `250 магнит` → shows amount + description, prompts category
- Send a photo of receipt → auto-reads total, prompts category

## Categories

1. 🛒 Продукты
2. 🍽️ Кафе / Еда на вынос
3. 🚗 Транспорт / Бензин
4. 🏠 Дом / Коммунальные
5. 💊 Здоровье / Аптека
6. 🎉 Развлечения
7. 👗 Одежда
8. 📦 Другое

## Notes

- **Database:** SQLite file `expenses.db` — stored on Railway volume
  ⚠️ On free Railway plan, storage resets on redeploy. To make data permanent,
  use Railway's **PostgreSQL** addon (free) — ask for upgrade instructions.
- **OCR accuracy:** Works best on clear, well-lit receipt photos
- Both Yevhenii and Anastasiia can use the bot — each entry records the username
