import os
import requests
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler # <-- ИЗМЕНЕНИЕ 1

# --- Часть 1: Веб-сервер для UptimeRobot ---
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
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID'))
GITHUB_USERNAME = os.environ.get('GITHUB_USERNAME')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
    
# --- ИЗМЕНЕНИЕ 2: Используем другой, "фоновый" планировщик ---
scheduler = BackgroundScheduler(timezone="Europe/Moscow")

# --- Часть 3: Функции нашего бота ---
def trigger_github_action(context: ContextTypes.DEFAULT_TYPE = None, chat_id: int = None):
    """Отправляет API-запрос на запуск GitHub Actions (теперь это СИНХРОННАЯ функция)."""
    print("Отправляю запрос на запуск GitHub Actions...")
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dispatches"
    headers = { "Accept": "application/vnd.github.v3+json", "Authorization": f"token {GITHUB_PAT}" }
    data = {"event_type": "start-processing"}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        message = f"🚀 Запрос на запуск 'фабрики' отправлен.\nСтатус ответа GitHub: {response.status_code}"
        if response.status_code != 204: message += f"\nОтвет: {response.text}"
    except Exception as e:
        message = f"❌ Ошибка при отправке запроса в GitHub: {e}"

    target_chat_id = chat_id
    if context and context.job: target_chat_id = context.job.data.get('chat_id')
    
    # --- ИЗМЕНЕНИЕ 3: Отправляем сообщение через context.bot ---
    if target_chat_id:
        context.bot.send_message(chat_id=target_chat_id, text=message)

# --- ИЗМЕНЕНИЕ 4: Все функции-обработчики теперь СИНХРОННЫЕ (без async/await) ---
def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("⛔️ У вас нет доступа к этой админ-панели.")
        return
    keyboard = [
        [InlineKeyboardButton("🚀 Запустить проверку сейчас", callback_data='run_now')],
        [InlineKeyboardButton("🕒 Настроить расписание", callback_data='schedule_menu')],
        [InlineKeyboardButton("📊 Показать статус", callback_data='status')],
    ]
    update.message.reply_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=InlineKeyboardMarkup(keyboard))

def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    query.answer()
    chat_id = update.effective_chat.id

    if query.data == 'run_now':
        query.edit_message_text(text="⏳ Отправляю команду на запуск...")
        trigger_github_action(context=context, chat_id=chat_id)
        # Ждем немного и возвращаем меню
        time.sleep(2)
        query.edit_message_text(text="✅ Команда отправлена. Логи скоро начнут приходить в ЛС.\n\nВозвращаю главное меню...", reply_markup=get_main_keyboard()) # get_main_keyboard нужно определить
    
    elif query.data == 'schedule_menu':
        keyboard = [
            [InlineKeyboardButton("Включить 11:00 МСК", callback_data='set_schedule_11')],
            [InlineKeyboardButton("Включить 15:00 МСК", callback_data='set_schedule_15')],
            [InlineKeyboardButton("Включить оба (11 и 15)", callback_data='set_schedule_both')],
            [InlineKeyboardButton("❌ Отключить", callback_data='set_schedule_off')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')]
        ]
        query.edit_message_text(text="🕒 Выберите время автоматического запуска (по Москве):", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data.startswith('set_schedule'):
        for job in scheduler.get_jobs(): job.remove()
        text = "❌ Расписание отключено."
        job_kwargs = {'context': context, 'chat_id': chat_id} # Передаем context и chat_id
        if query.data == 'set_schedule_11':
            scheduler.add_job(trigger_github_action, 'cron', hour=11, minute=0, kwargs=job_kwargs, id='job_11')
            text = "✅ Расписание установлено на 11:00 МСК."
        elif query.data == 'set_schedule_15':
            scheduler.add_job(trigger_github_action, 'cron', hour=15, minute=0, kwargs=job_kwargs, id='job_15')
            text = "✅ Расписание установлено на 15:00 МСК."
        elif query.data == 'set_schedule_both':
            scheduler.add_job(trigger_github_action, 'cron', hour=11, minute=0, kwargs=job_kwargs, id='job_11')
            scheduler.add_job(trigger_github_action, 'cron', hour=15, minute=0, kwargs=job_kwargs, id='job_15')
            text = "✅ Расписание установлено на 11:00 и 15:00 МСК."
        query.edit_message_text(text, reply_markup=get_main_keyboard())

    elif query.data == 'status':
        jobs = scheduler.get_jobs()
        if not jobs: status_text = "📊 Расписание не настроено."
        else:
            status_text = "📊 Активное расписание:\n"
            for job in jobs: status_text += f"- Следующий запуск в {job.next_run_time.strftime('%H:%M:%S')} по МСК\n"
        query.answer(status_text, show_alert=True)
    
    elif query.data == 'main_menu':
         query.edit_message_text("⚙️ Админ-панель 'Видео Архив'", reply_markup=get_main_keyboard())

# --- Часть 4: Запуск всей системы (теперь СИНХРОННЫЙ) ---
def main():
    """Главная функция, которая запускает все."""
    # Запускаем веб-сервер в отдельном потоке
    keep_alive()
    print("Веб-сервер для UptimeRobot запущен...")
    
    # Создаем приложение бота
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler(["start", "admin"], start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Запускаем планировщик
    scheduler.start()
    print("Планировщик запущен...")
    
    # Запускаем бота
    print("Бот-админка запущен и слушает команды...")
    application.run_polling()

if __name__ == '__main__':
    import time
    def get_main_keyboard(): # Определяем get_main_keyboard здесь, чтобы избежать ошибок
        keyboard = [
            [InlineKeyboardButton("🚀 Запустить проверку сейчас", callback_data='run_now')],
            [InlineKeyboardButton("🕒 Настроить расписание", callback_data='schedule_menu')],
            [InlineKeyboardButton("📊 Показать статус", callback_data='status')],
        ]
        return InlineKeyboardMarkup(keyboard)

    main()
