import os
import json
import subprocess
import feedparser
import time
import logging
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio

# --- НАСТРОЙКИ ---
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAvrIl6ltV8MdJo3mV4Nl4Q"
TEMP_FOLDER = 'temp_videos'
DB_FILE = 'videos.json'
MAX_VIDEOS_ENTRIES = 25 
CHUNK_DURATION_SECONDS = 240
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID'))
GITHUB_USERNAME = os.environ.get('GITHUB_USERNAME')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_PAT = os.environ.get('GITHUB_PAT')
GIT_REPO_URL = f"https://{GITHUB_USERNAME}:{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO}.git"
CRON_SECRET_KEY = os.environ.get('CRON_SECRET_KEY', 'default_secret_key') 

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
is_processing = False
current_status_message = ""

# =================================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =================================================================================

def setup_git_repo():
    if os.path.exists(GITHUB_REPO):
        logger.info("Обновляю репозиторий со склада...")
        subprocess.run(f"cd {GITHUB_REPO} && git pull", shell=True, check=True)
    else:
        logger.info("Клонирую репозиторий со склада...")
        subprocess.run(f"git clone {GIT_REPO_URL}", shell=True, check=True)

def get_video_db():
    db_path = os.path.join(GITHUB_REPO, DB_FILE)
    if os.path.exists(db_path):
        try:
            with open(db_path, 'r') as f: return json.load(f)
        except json.JSONDecodeError:
            return [] # Возвращаем пустой список, если JSON битый
    return []

def save_and_push_db(db):
    db_path = os.path.join(GITHUB_REPO, DB_FILE)
    with open(db_path, 'w') as f:
        json.dump(db, f, indent=4)
    try:
        logger.info("Сохраняю изменения на GitHub...")
        subprocess.run(f'cd {GITHUB_REPO} && git config user.name "Video Assistant Bot" && git config user.email "bot@render.com"', shell=True, check=True)
        subprocess.run(f'cd {GITHUB_REPO} && git add {DB_FILE}', shell=True, check=True)
        result = subprocess.run(f'cd {GITHUB_REPO} && git diff --staged --quiet', shell=True)
        if result.returncode != 0:
            subprocess.run(f'cd {GITHUB_REPO} && git commit -m "Автоматическое обновление базы видео"', shell=True, check=True)
            subprocess.run(f'cd {GITHUB_REPO} && git push', shell=True, check=True)
            logger.info("Изменения успешно отправлены на GitHub.")
        else:
            logger.info("Нет изменений для отправки.")
    except Exception as e:
        logger.error(f"Не удалось отправить изменения на GitHub: {e}")

async def update_status(context: ContextTypes.DEFAULT_TYPE, text: str):
    global current_status_message
    try:
        new_text = f"{current_status_message}\n{text}"
        await context.bot.edit_message_text(text=new_text, chat_id=ADMIN_ID, message_id=context.user_data['status_message_id'])
        current_status_message = new_text
    except Exception: pass

async def process_single_video(video_id: str, title: str, context: ContextTypes.DEFAULT_TYPE):
    global is_processing
    is_processing = True
    if context: await update_status(context, f"🎬 Начинаю обработку: {title[:50]}...")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    video_parts_info = []
    try:
        if context: await update_status(context, "📥 Скачиваю видео (480p)...")
        temp_filepath_template = os.path.join(TEMP_FOLDER, f'{video_id}_full.%(ext)s')
        command_dl = ['yt-dlp', '-f', 'best[height<=480]', '--output', temp_filepath_template, video_url]
        subprocess.run(command_dl, check=True, timeout=900)
        full_filename = next((f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_full")), None)
        if not full_filename: raise Exception("Файл не скачался")
        full_filepath = os.path.join(TEMP_FOLDER, full_filename)
        if context: await update_status(context, "🔪 Начинаю нарезку...")
        chunk_filename_template = os.path.join(TEMP_FOLDER, f"{video_id}_part_%03d.mp4")
        command_ffmpeg = ['ffmpeg', '-i', full_filepath, '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-map', '0', '-segment_time', str(CHUNK_DURATION_SECONDS), '-f', 'segment', '-reset_timestamps', '1', '-movflags', '+faststart', chunk_filename_template]
        subprocess.run(command_ffmpeg, check=True, timeout=1800)
        os.remove(full_filepath)
        chunks = sorted([f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_part_")])
        if context: await update_status(context, f"📤 Нарезано {len(chunks)} частей. Загружаю...")
        for i, chunk_filename in enumerate(chunks):
            if context: await update_status(context, f"  > Загружаю часть {i+1}/{len(chunks)}...")
            chunk_filepath = os.path.join(TEMP_FOLDER, chunk_filename)
            part_title = f"{title} - Часть {i+1}"
            with open(chunk_filepath, 'rb') as video_file:
                message = await context.bot.send_video(chat_id=CHANNEL_ID, video=video_file, caption=part_title, read_timeout=300, write_timeout=300, connect_timeout=300)
            video_parts_info.append({'part_num': i + 1, 'file_id': message.video.file_id})
            os.remove(chunk_filepath)
        if context: await update_status(context, "💾 Обновляю базу данных...")
        if video_parts_info:
            new_entry = {'id': video_id, 'title': title, 'parts': video_parts_info}
            db = get_video_db(); db = [v for v in db if v['id'] != video_id]; db.insert(0, new_entry)
            while len(db) > MAX_VIDEOS_ENTRIES: db.pop()
            save_and_push_db(db)
        if context: await update_status(context, "🎉 Готово! Видео успешно обработано.")
    except Exception as e:
        logger.error(f"Критическая ошибка при обработке '{title}': {e}")
        if context: await update_status(context, f"❌ Ошибка: {e}")
    finally:
        is_processing = False

def run_check_job(app):
    asyncio.run(check_for_new_videos_async(app))

async def check_for_new_videos_async(app):
    global is_processing
    if is_processing:
        logger.info("Проверка по расписанию пропущена: бот уже занят."); return
    logger.info("--- Запуск проверки по расписанию ---"); setup_git_repo()
    db = get_video_db(); existing_ids = {video['id'] for video in db}
    feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
    new_videos = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]
    if not new_videos:
        logger.info("Новых видео не найдено."); return
    logger.info(f"Найдено {len(new_videos)} новых видео. Обрабатываю самое старое.")
    video_to_process = reversed(new_videos).__next__()
    context = ContextTypes.DEFAULT_TYPE(application=app, chat_id=ADMIN_ID, user_id=ADMIN_ID)
    message = await context.bot.send_message(chat_id=ADMIN_ID, text="Начинаю автоматическую обработку...")
    context.user_data['status_message_id'] = message.message_id
    await process_single_video(video_to_process['id'], video_to_process['title'], context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    keyboard = [[InlineKeyboardButton("📋 Показать незагруженные видео", callback_data='list_new_videos')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Привет! Я твой видео-ассистент.', reply_markup=reply_markup)
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if is_processing:
        await update.message.reply_text(f"Я сейчас занят. Текущий статус:\n{current_status_message}")
    else:
        await update.message.reply_text("Я свободен и готов к работе! ✅")
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if is_processing:
        await query.edit_message_text(text="Я сейчас занят, попробуй позже."); return
    if query.data == 'list_new_videos':
        await query.edit_message_text(text="Проверяю YouTube, подожди...")
        setup_git_repo(); db = get_video_db(); existing_ids = {video['id'] for video in db}
        feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
        new_videos = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]
        if not new_videos:
            await query.edit_message_text(text="Все последние видео уже загружены! ✅"); return
        keyboard = [[InlineKeyboardButton(video['title'][:50] + "...", callback_data=f"process_{video['id']}")] for video in new_videos[:5]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Вот последние незагруженные видео:", reply_markup=reply_markup)
    elif query.data.startswith('process_'):
        video_id = query.data.split('_')[1]
        feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
        video_info = next((v for v in feed.entries if v.yt_videoid == video_id), None)
        if video_info:
            global current_status_message
            current_status_message = "Начинаю ручную обработку..."
            message = await query.edit_message_text(text=current_status_message)
            context.user_data['status_message_id'] = message.message_id
            thread = Thread(target=asyncio.run, args=(process_single_video(video_id, video_info.title, context),))
            thread.start()
        else:
            await query.edit_message_text("Не удалось найти информацию об этом видео.")

# --- ЗАПУСК ---
async def main_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CallbackQueryHandler(button_callback))
    logger.info("Бот настроен. Запускаю polling...")
    await application.initialize()
    await application.start()
    await application.run_polling()

def run_flask(app):
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=app.run, kwargs={'host':'0.0.0.0', 'port':int(os.environ.get('PORT', 10000))})
    flask_thread.daemon = True
    flask_thread.start()

if __name__ == '__main__':
    if not os.path.exists(TEMP_FOLDER): os.makedirs(TEMP_FOLDER)
    setup_git_repo()
    
    flask_app = Flask(__name__)
    @flask_app.route('/')
    def hello_world():
        return 'Бот-ассистент работает!'

    @flask_app.route(f'/run-check/{CRON_SECRET_KEY}')
    def trigger_check():
        if is_processing:
            return "Бот уже занят обработкой.", 429
        
        # Для запуска асинхронной функции из Flask нам нужна временная асинхронная обертка
        async def run_check_in_background():
            temp_app_for_context = Application.builder().token(BOT_TOKEN).build()
            await check_for_new_videos_async(temp_app_for_context)

        check_thread = Thread(target=asyncio.run, args=(run_check_in_background(),))
        check_thread.daemon = True
        check_thread.start()
        return "Проверка новых видео запущена!", 200

    run_flask(flask_app)
    
    # Основной поток запускает бота
    asyncio.run(main_bot())
