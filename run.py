import os
import json
import subprocess
import feedparser
import time
import logging
from threading import Thread
import asyncio
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL') # Render предоставляет эту переменную автоматически
CRON_SECRET_KEY = os.environ.get('CRON_SECRET_KEY')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
is_processing = False
current_status_message = ""
# ... (ВСЕ функции до блока Telegram-бота остаются без изменений) ...
def setup_git_repo():
    if os.path.exists(GITHUB_REPO):
        subprocess.run(f"cd {GITHUB_REPO} && git pull", shell=True, check=True)
    else:
        subprocess.run(f"git clone {GIT_REPO_URL}", shell=True, check=True)
def get_video_db():
    db_path = os.path.join(GITHUB_REPO, DB_FILE)
    if os.path.exists(db_path):
        with open(db_path, 'r') as f: return json.load(f)
    return []
def save_and_push_db(db):
    db_path = os.path.join(GITHUB_REPO, DB_FILE)
    with open(db_path, 'w') as f: json.dump(db, f, indent=4)
    try:
        subprocess.run(f'cd {GITHUB_REPO} && git config user.name "Video Assistant Bot" && git config user.email "bot@render.com"', shell=True, check=True)
        subprocess.run(f'cd {GITHUB_REPO} && git add {DB_FILE}', shell=True, check=True)
        result = subprocess.run(f'cd {GITHUB_REPO} && git diff --staged --quiet', shell=True)
        if result.returncode != 0:
            subprocess.run(f'cd {GITHUB_REPO} && git commit -m "Автоматическое обновление базы видео"', shell=True, check=True)
            subprocess.run(f'cd {GITHUB_REPO} && git push', shell=True, check=True)
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

async def check_for_new_videos_async(context: ContextTypes.DEFAULT_TYPE):
    global is_processing
    if is_processing:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Проверка по расписанию пропущена: бот уже занят.")
        return
    logger.info("--- Запуск проверки по расписанию ---"); setup_git_repo()
    db = get_video_db(); existing_ids = {video['id'] for video in db}
    feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
    new_videos = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]
    if not new_videos:
        logger.info("Новых видео не найдено."); return
    logger.info(f"Найдено {len(new_videos)} новых видео. Обрабатываю самое старое.")
    video_to_process = reversed(new_videos).__next__()
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
            asyncio.create_task(process_single_video(video_id, video_info.title, context))
        else:
            await query.edit_message_text("Не удалось найти информацию об этом видео.")

async def web_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытый обработчик для cron-запросов."""
    if update.effective_user.id != ADMIN_ID: return # Безопасность
    await check_for_new_videos_async(context)

async def self_ping():
    """Функция, которая "пинает" сама себя, чтобы сервис не засыпал."""
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL не установлена, самопинг отключен.")
        return
    
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(60 * 14) # Пингуем каждые 14 минут
            try:
                await client.get(RENDER_EXTERNAL_URL)
                logger.info("Самопинг успешен.")
            except Exception as e:
                logger.error(f"Ошибка самопинга: {e}")

async def main():
    if not os.path.exists(TEMP_FOLDER): os.makedirs(TEMP_FOLDER)
    setup_git_repo()
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    # Добавляем секретную команду для cron
    application.add_handler(CommandHandler(CRON_SECRET_KEY, web_handler))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Запускаем самопинг в фоне
    asyncio.create_task(self_ping())
    
    logger.info("Бот готов и запускается...")
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
