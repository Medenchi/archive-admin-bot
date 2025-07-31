import os
import asyncio
import httpx
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Часть 1: Веб-сервер, чтобы сервис не засыпал ---
app = Flask('')
@app.route('/')
def home():
    return "Admin Bot is alive"

# --- Часть 2: Настройки бота ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID'))
GITHUB_USERNAME = os.environ.get('GITHUB_USERNAME')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# --- Часть 3: Функции бота (обработчики) ---
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀 Запустить проверку сейчас", callback_data='run_now')],
        [InlineKeyboardButton("🕒 Настроить расписание", callback_data='schedule_menu')],
        [InlineKeyboardButton("📊 Показать статус", callback_data='status')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def trigger_github_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dispatches"
    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {GITHUB_PAT}"}
    data = {"event_type": "start-processing"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data)
        message = f"🚀 Запрос на запуск отправлен. Статус: {response.status_code}"
    except Exception as e:
        message = f"❌ Ошибка при отправке запроса в GitHub: {e}"
    await context.bot.send_message(chat_id=chat_id, text=message)

async def scheduled_trigger(context: ContextTypes.DEFAULT_TYPE):
    await trigger_github_action(context, chat_id=ADMIN_ID)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    await update.message.reply_text("⚙️ Админ-панель", reply_markup=get_main_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'run_now':
        await query.edit_message_text(text="⏳ Отправляю команду...")
        await trigger_github_action(context, chat_id=update.effective_chat.id)
        await asyncio.sleep(2)
        await query.edit_message_text(text="✅ Команда отправлена.", reply_markup=get_main_keyboard())
    elif query.data == 'schedule_menu':
        keyboard = [
            [InlineKeyboardButton("11:00 МСК", callback_data='set_11'), InlineKeyboardButton("15:00 МСК", callback_data='set_15')],
            [InlineKeyboardButton("Оба (11 и 15)", callback_data='set_both')],
            [InlineKeyboardButton("❌ Отключить", callback_data='set_off')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')]
        ]
        await query.edit_message_text(text="🕒 Выберите время запуска:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith('set_'):
        for job in scheduler.get_jobs(): job.remove()
        text = "❌ Расписание отключено."
        if query.data == 'set_11':
            scheduler.add_job(scheduled_trigger, 'cron', hour=11, minute=0, args=[context])
            text = "✅ Расписание: 11:00 МСК."
        elif query.data == 'set_15':
            scheduler.add_job(scheduled_trigger, 'cron', hour=15, minute=0, args=[context])
            text = "✅ Расписание: 15:00 МСК."
        elif query.data == 'set_both':
            scheduler.add_job(scheduled_trigger, 'cron', hour=11, minute=0, args=[context])
            scheduler.add_job(scheduled_trigger, 'cron', hour=15, minute=0, args=[context])
            text = "✅ Расписание: 11:00 и 15:00 МСК."
        await query.edit_message_text(text, reply_markup=get_main_keyboard())
    elif query.data == 'status':
        jobs = scheduler.get_jobs()
        status_text = "\n".join([f"- Запуск в {job.next_run_time.strftime('%H:%M:%S')}" for job in jobs]) if jobs else "Не настроено."
        await query.answer(f"📊 Активное расписание:\n{status_text}", show_alert=True)
    elif query.data == 'main_menu':
         await query.edit_message_text("⚙️ Админ-панель", reply_markup=get_main_keyboard())

async def run_bot_and_scheduler():
    """Главная асинхронная функция, которая запускает все."""
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler(["start", "admin"], start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    scheduler.start()
    print("Планировщик запущен...")
    
    print("Бот запущен...")
    # Используем with, чтобы гарантировать корректное завершение
    async with application:
        await application.start()
        await application.updater.start_polling()
        # Держим программу живой
        await asyncio.Event().wait()

if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=8080))
    flask_thread.daemon = True
    flask_thread.start()
    print("Веб-сервер для UptimeRobot запущен...")

    # Запускаем основной асинхронный цикл
    try:
        asyncio.run(run_bot_and_scheduler())
    except KeyboardInterrupt:
        print("Завершение работы...")
