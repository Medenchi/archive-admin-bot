import os
import asyncio
import httpx
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Часть 1: Веб-сервер для UptimeRobot ---
app = Flask('')
@app.route('/')
def home(): return "Admin Bot is alive"
def run_flask(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run_flask).start()

# --- Часть 2: Настройки и переменные для бота ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID'))
GITHUB_USERNAME = os.environ.get('GITHUB_USERNAME')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# --- Часть 3: АСИНХРОННЫЕ функции нашего бота ---
def get_main_keyboard():
    keyboard = [[InlineKeyboardButton("🚀 Запустить проверку сейчас", callback_data='run_now')],
                [InlineKeyboardButton("🕒 Настроить расписание", callback_data='schedule_menu')],
                [InlineKeyboardButton("📊 Показать статус", callback_data='status')]]
    return InlineKeyboardMarkup(keyboard)

async def trigger_github_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    print("Отправляю запрос на запуск GitHub Actions...")
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dispatches"
    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {GITHUB_PAT}"}
    data = {"event_type": "start-processing"}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
    
    message = f"🚀 Запрос на запуск 'фабрики' отправлен.\nСтатус ответа GitHub: {response.status_code}"
    if response.status_code != 204: message += f"\nОтвет: {response.text}"
    
    await context.bot.send_message(chat_id=chat_id, text=message)

async def scheduled_trigger(context: ContextTypes.DEFAULT_TYPE):
    """Специальная функция для вызова из планировщика."""
    await trigger_github_action(context, chat_id=ADMIN_ID)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ У вас нет доступа.")
        return
    await update.message.reply_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=get_main_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'run_now':
        await query.edit_message_text(text="⏳ Отправляю команду на запуск...")
        await trigger_github_action(context, chat_id=update.effective_chat.id)
        await asyncio.sleep(2)
        await query.edit_message_text(text="✅ Команда отправлена. Логи скоро начнут приходить в ЛС.\n\nВозвращаю главное меню...", reply_markup=get_main_keyboard())
    
    elif query.data == 'schedule_menu':
        keyboard = [[InlineKeyboardButton("Включить 11:00 МСК", callback_data='set_schedule_11')],
                    [InlineKeyboardButton("Включить 15:00 МСК", callback_data='set_schedule_15')],
                    [InlineKeyboardButton("Включить оба (11 и 15)", callback_data='set_schedule_both')],
                    [InlineKeyboardButton("❌ Отключить", callback_data='set_schedule_off')],
                    [InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')]]
        await query.edit_message_text(text="🕒 Выберите время автоматического запуска (по Москве):", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('set_schedule'):
        for job in scheduler.get_jobs(): job.remove()
        text = "❌ Расписание отключено."
        if query.data == 'set_schedule_11':
            scheduler.add_job(scheduled_trigger, 'cron', hour=11, minute=0, args=[context], id='job_11')
            text = "✅ Расписание установлено на 11:00 МСК."
        elif query.data == 'set_schedule_15':
            scheduler.add_job(scheduled_trigger, 'cron', hour=15, minute=0, args=[context], id='job_15')
            text = "✅ Расписание установлено на 15:00 МСК."
        elif query.data == 'set_schedule_both':
            scheduler.add_job(scheduled_trigger, 'cron', hour=11, minute=0, args=[context], id='job_11')
            scheduler.add_job(scheduled_trigger, 'cron', hour=15, minute=0, args=[context], id='job_15')
            text = "✅ Расписание установлено на 11:00 и 15:00 МСК."
        await query.edit_message_text(text, reply_markup=get_main_keyboard())

    elif query.data == 'status':
        jobs = scheduler.get_jobs()
        status_text = "📊 Активное расписание:\n" + "\n".join([f"- Следующий запуск в {job.next_run_time.strftime('%H:%M:%S')} по МСК" for job in jobs]) if jobs else "📊 Расписание не настроено."
        await query.answer(status_text, show_alert=True)
        
    elif query.data == 'main_menu':
         await query.edit_message_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=get_main_keyboard())

# --- Часть 4: Запуск всей системы ---
def main():
    """Главная функция, которая настраивает и запускает все."""
    # Запускаем веб-сервер в отдельном потоке
    keep_alive()
    print("Веб-сервер для UptimeRobot запущен...")
    
    # Создаем приложение бота
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler(["start", "admin"], start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # --- ИСПРАВЛЕНИЕ: Запускаем планировщик ВНУТРИ асинхронного контекста бота ---
    # Это позволяет им использовать один и тот же "event loop"
    if not scheduler.running:
        scheduler.start()
        print("Планировщик запущен...")
    
    # Запускаем бота
    print("Бот-админка запущен и слушает команды...")
    application.run_polling()

if __name__ == '__main__':
    try:
        import httpx
    except ImportError:
        import subprocess
        subprocess.run(['pip', 'install', 'httpx'])
    main()
