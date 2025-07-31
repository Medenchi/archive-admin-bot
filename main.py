import os
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Часть 1: Веб-сервер для UptimeRobot, чтобы Replit не засыпал ---
app = Flask('')

@app.route('/')
def home():
    return "Admin Bot is alive"

def run_flask():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- Часть 2: Настройки и переменные для бота ---

# Загружаем "секреты" из окружения Replit
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID'))
GITHUB_USERNAME = os.environ.get('GITHUB_USERNAME')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
    
# Создаем планировщик, который будет работать по московскому времени
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# --- Часть 3: Функции нашего бота ---

def get_main_keyboard():
    """Создает и возвращает главное меню с инлайн-кнопками."""
    keyboard = [
        [InlineKeyboardButton("🚀 Запустить проверку сейчас", callback_data='run_now')],
        [InlineKeyboardButton("🕒 Настроить расписание", callback_data='schedule_menu')],
        [InlineKeyboardButton("📊 Показать статус", callback_data='status')],
    ]
    return InlineKeyboardMarkup(keyboard)
        
async def trigger_github_action(context: ContextTypes.DEFAULT_TYPE = None, chat_id: int = None):
    """Отправляет API-запрос на запуск GitHub Actions."""
    print("Отправляю запрос на запуск GitHub Actions...")
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dispatches"
    headers = { 
        "Accept": "application/vnd.github.v3+json", 
        "Authorization": f"token {GITHUB_PAT}" 
    }
    data = {"event_type": "start-processing"}
    
    # Используем асинхронную библиотеку, чтобы не "вешать" бота
    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
    
    message = f"🚀 Запрос на запуск 'фабрики' отправлен.\nСтатус ответа GitHub: {response.status_code}"
    if response.status_code != 204: 
        message += f"\nОтвет: {response.text}"
    
    # Определяем, кому отправить ответ
    target_chat_id = chat_id
    if context and context.job and context.job.data:
        target_chat_id = context.job.data.get('chat_id')
    
    if target_chat_id: 
        await context.bot.send_message(chat_id=target_chat_id, text=message)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команды /start и /admin."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ У вас нет доступа к этой админ-панели.")
        return
    await update.message.reply_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=get_main_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на все инлайн-кнопки."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    if query.data == 'run_now':
        await query.edit_message_text(text="⏳ Отправляю команду на запуск...")
        await trigger_github_action(context=context, chat_id=chat_id)
        # Ждем немного и возвращаем меню
        await asyncio.sleep(2)
        await query.edit_message_text(text="✅ Команда отправлена. Логи скоро начнут приходить в ЛС.\n\n"
                                             "Возвращаю главное меню...", reply_markup=get_main_keyboard())
    
    elif query.data == 'schedule_menu':
        keyboard = [
            [InlineKeyboardButton("Включить 11:00 МСК", callback_data='set_schedule_11')],
            [InlineKeyboardButton("Включить 15:00 МСК", callback_data='set_schedule_15')],
            [InlineKeyboardButton("Включить оба (11 и 15)", callback_data='set_schedule_both')],
            [InlineKeyboardButton("❌ Отключить расписание", callback_data='set_schedule_off')],
            [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data='main_menu')]
        ]
        await query.edit_message_text(text="🕒 Выберите время автоматического запуска (по Москве):", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('set_schedule'):
        # Удаляем все старые задачи, чтобы не было дублей
        for job in scheduler.get_jobs():
            job.remove()
        
        text = "❌ Расписание отключено."
        if query.data == 'set_schedule_11':
            scheduler.add_job(trigger_github_action, 'cron', hour=11, minute=0, kwargs={'context': context, 'chat_id': chat_id}, id='job_11')
            text = "✅ Расписание установлено на 11:00 МСК."
        elif query.data == 'set_schedule_15':
            scheduler.add_job(trigger_github_action, 'cron', hour=15, minute=0, kwargs={'context': context, 'chat_id': chat_id}, id='job_15')
            text = "✅ Расписание установлено на 15:00 МСК."
        elif query.data == 'set_schedule_both':
            scheduler.add_job(trigger_github_action, 'cron', hour=11, minute=0, kwargs={'context': context, 'chat_id': chat_id}, id='job_11')
            scheduler.add_job(trigger_github_action, 'cron', hour=15, minute=0, kwargs={'context': context, 'chat_id': chat_id}, id='job_15')
            text = "✅ Расписание установлено на 11:00 и 15:00 МСК."
        
        await query.edit_message_text(text, reply_markup=get_main_keyboard())

    elif query.data == 'status':
        jobs = scheduler.get_jobs()
        if not jobs:
            status_text = "📊 Расписание не настроено."
        else:
            status_text = "📊 Активное расписание:\n"
            for job in jobs:
                status_text += f"- Следующий запуск в {job.next_run_time.strftime('%H:%M:%S')} по МСК\n"
        await query.answer(status_text, show_alert=True)
        
    elif query.data == 'main_menu':
         await query.edit_message_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=get_main_keyboard())

# --- Часть 4: Запуск всей системы ---

async def main_async():
    """Главная асинхронная функция, которая запускает бота и планировщик."""
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики команд и кнопок
    application.add_handler(CommandHandler(["start", "admin"], start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Запускаем планировщик
    scheduler.start()
    print("Планировщик запущен...")
    
    # Запускаем бота в режиме polling (постоянно опрашивает Telegram)
    print("Бот-админка запущен...")
    await application.run_polling()

if __name__ == '__main__':
    # Устанавливаем httpx, если его нет
    try:
        import httpx
    except ImportError:
        import subprocess
        subprocess.run(['pip', 'install', 'httpx'])
        
    # Запускаем веб-сервер в отдельном потоке, чтобы Replit не засыпал
    keep_alive()
    print("Веб-сервер для UptimeRobot запущен...")
    
    # Запускаем основной асинхронный цикл для бота
    asyncio.run(main_async())
