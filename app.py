from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ConversationHandler
)
import logging
import sqlite3
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

# Loglama ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- YAPILANDIRMA AYARLARI ---
BOT_TOKEN = "7660064921:AAHAl0-wL7q5eGgHFlyPCMgW6ow1u4cS1f4"
ADMIN_USER_ID = 8143084360 # Kendi Telegram kullanıcı ID'nizi buraya girin!
# -----------------------------

# APScheduler başlat
scheduler = AsyncIOScheduler()

# --- Veritabanı İşlemleri ---
DATABASE_NAME = 'settings.db'

def init_db():
    """Veritabanını başlatır ve tabloları oluşturur."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            telegram_id INTEGER NOT NULL UNIQUE,
            link TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            job_id TEXT PRIMARY KEY,
            post_text TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    """Veritabanından bir ayar değerini alır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else default

def set_setting(key, value):
    """Veritabanına bir ayar değerini kaydeder veya günceller."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_channels():
    """Veritabanından tüm sponsor kanallarını alır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, telegram_id, link FROM channels ORDER BY name")
    channels = [{"ad": row[0], "id": row[1], "link": row[2]} for row in cursor.fetchall()]
    conn.close()
    return channels

def add_channel_to_db(name, telegram_id, link):
    """Veritabanına yeni bir sponsor kanalı ekler."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO channels (name, telegram_id, link) VALUES (?, ?, ?)", (name, telegram_id, link))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Kanal ID {telegram_id} zaten mevcut.")
        return False
    finally:
        conn.close()

def remove_channel_from_db(telegram_id):
    """Veritabanından bir sponsor kanalı kaldırır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def add_user_to_db(user_id):
    """Yeni kullanıcıyı veritabanına ekler."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        logger.info(f"Yeni kullanıcı eklendi: {user_id}")
    except sqlite3.IntegrityError:
        pass # Kullanıcı zaten varsa bir şey yapma
    finally:
        conn.close()

def get_all_users():
    """Tüm kayıtlı kullanıcı ID'lerini alır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def add_scheduled_post_to_db(job_id, post_text, interval_minutes):
    """Zamanlanmış gönderiyi veritabanına kaydeder."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    created_at = datetime.now().isoformat()
    cursor.execute("INSERT INTO scheduled_posts (job_id, post_text, interval_minutes, created_at) VALUES (?, ?, ?, ?)",
                   (job_id, post_text, interval_minutes, created_at))
    conn.commit()
    conn.close()

def remove_scheduled_post_from_db(job_id):
    """Veritabanından zamanlanmış gönderiyi kaldırır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scheduled_posts WHERE job_id = ?", (job_id,))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def get_scheduled_posts():
    """Veritabanından tüm zamanlanmış gönderileri alır."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT job_id, post_text, interval_minutes, created_at FROM scheduled_posts")
    posts = [{"job_id": row[0], "text": row[1], "interval": row[2], "created_at": row[3]} for row in cursor.fetchall()]
    conn.close()
    return posts

# --- Bot Komutları ve İşleyicileri ---

# Konuşma durumları (Kanal Ekleme için)
CHANNEL_NAME, CHANNEL_ID, CHANNEL_LINK = range(3)
REMOVE_CHANNEL_CONFIRM = range(1)

# Konuşma durumları (Auto Poster için)
AUTOPOST_TEXT, AUTOPOST_INTERVAL = range(2)

async def start(update: Update, context):
    """Kullanıcı /start komutunu kullandığında çalışır ve kullanıcı ID'sini kaydeder."""
    add_user_to_db(update.effective_user.id) # Kullanıcı ID'sini kaydet

    sponsor_kanallar = get_channels()
    verilecek_kod = get_setting("vpn_code", "Henüz bir kod ayarlanmadı.")

    keyboard_buttons = []
    mesaj = "Merhaba! Bu kodu almak için lütfen aşağıdaki kanallara abone olun:\n\n"

    if not sponsor_kanallar:
        mesaj += "Şu anda sponsor kanal bulunmamaktadır. Lütfen adminin kanalları ayarlamasını bekleyin."
    else:
        for kanal in sponsor_kanallar:
            mesaj += f"- **{kanal['ad']}**: {kanal['link']}\n"

        keyboard_buttons.append([InlineKeyboardButton("Abone Oldum, Kodu Ver!", callback_data='check_subscription')])
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        mesaj += "\nAbone olduktan sonra aşağıdaki butona basın:"
        await update.message.reply_text(
            mesaj,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text(mesaj, parse_mode='Markdown')


async def check_subscription(update: Update, context):
    """Kullanıcı 'Abone Oldum' butonuna bastığında abonelikleri kontrol eder."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    all_subscribed = True
    missing_channels = []
    sponsor_kanallar = get_channels()
    verilecek_kod = get_setting("vpn_code", "Henüz bir kod ayarlanmadı.")

    if not sponsor_kanallar:
        await query.edit_message_text("Şu anda kontrol edilecek sponsor kanal bulunmamaktadır.")
        return

    for kanal in sponsor_kanallar:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=kanal['id'], user_id=user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                all_subscribed = False
                missing_channels.append(kanal)
        except Exception as e:
            logger.error(f"Kanal {kanal['ad']} için abonelik kontrol hatası: {e}")
            all_subscribed = False
            missing_channels.append(kanal)

    if all_subscribed:
        await query.edit_message_text(f"GUTLAÝAS 🎉 SİZ HEMME KANALLARA AGZA BOLDUŇYZ !.\n\n{verilecek_kod}")
    else:
        mesaj = "Ýalňyşlyk! Heniz Siz Hemme kanallara agza bolmansyňyz ! ýa - da agzalygyňyz baralnyp bilmedi.\nTäzeden aşaky kanallara agza bolandygyňyzy barlaň:\n\n"
        for kanal in missing_channels:
            mesaj += f"- **{kanal['ad']}**: {kanal['link']}\n"
        mesaj += "\nAgza bolanyňyzdan soňra täzeden 'Agza Boldum, Kodu ber!' Düwmesine basyň."

        keyboard_buttons = [[InlineKeyboardButton("✅ AGZA BOLDUM ✅", callback_data='check_subscription')]]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)

        await query.edit_message_text(
            mesaj,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# --- Admin Komutları ---

async def is_admin(update: Update):
    """Kullanıcının admin olup olmadığını kontrol eder."""
    return update.effective_user.id == ADMIN_USER_ID

async def admin_only(update: Update, context):
    """Sadece adminlerin erişebileceği komutlar için yetkilendirme kontrolü."""
    if not await is_admin(update):
        await update.message.reply_text("Bu komutu kullanmaya yetkiniz yok.")
        return False
    return True

async def set_vpn_code(update: Update, context):
    """Adminin VPN kodunu ayarlamasını sağlar."""
    if not await admin_only(update, context):
        return

    if not context.args:
        await update.message.reply_text("Lütfen yeni VPN kodunu belirtin. Örnek: `/setvpn YENIKOD123`")
        return

    new_code = " ".join(context.args)
    set_setting("vpn_code", new_code)
    await update.message.reply_text(f"VPN kodu başarıyla güncellendi: `{new_code}`")

async def show_channels(update: Update, context):
    """Adminin mevcut sponsor kanallarını listelemesini sağlar."""
    if not await admin_only(update, context):
        return

    channels = get_channels()
    if not channels:
        await update.message.reply_text("Henüz kayıtlı sponsor kanal bulunmamaktadır.")
        return

    mesaj = "Mevcut Sponsor Kanalları:\n\n"
    for i, kanal in enumerate(channels):
        mesaj += f"{i+1}. Ad: {kanal['ad']}\n   ID: `{kanal['id']}`\n   Link: {kanal['link']}\n\n"
    await update.message.reply_text(mesaj, parse_mode='Markdown')

# --- Kanal Ekleme Konuşması ---
async def add_channel_start(update: Update, context):
    """Kanal ekleme konuşmasını başlatır."""
    if not await admin_only(update, context):
        return ConversationHandler.END

    await update.message.reply_text("Lütfen eklenecek kanalın adını girin (örn: 'Resmi Kanal'):")
    return CHANNEL_NAME

async def add_channel_name(update: Update, context):
    """Kanal adını alır ve ID'yi ister."""
    context.user_data['new_channel_name'] = update.message.text
    await update.message.reply_text(f"Kanal adı: '{update.message.text}'.\nŞimdi lütfen kanalın Telegram ID'sini girin (örn: -1001234567890):")
    return CHANNEL_ID

async def add_channel_id(update: Update, context):
    """Kanal ID'sini alır ve linki ister."""
    try:
        channel_id = int(update.message.text)
        context.user_data['new_channel_id'] = channel_id
        await update.message.reply_text(f"Kanal ID: `{channel_id}`.\nSon olarak, lütfen kanalın davet linkini girin (örn: https://t.me/kanal_adiniz):")
        return CHANNEL_LINK
    except ValueError:
        await update.message.reply_text("Geçersiz Kanal ID'si. Lütfen sadece sayısal bir değer girin (örn: -1001234567890).")
        return CHANNEL_ID

async def add_channel_link(update: Update, context):
    """Kanal linkini alır ve kanalı veritabanına ekler."""
    context.user_data['new_channel_link'] = update.message.text

    name = context.user_data['new_channel_name']
    telegram_id = context.user_data['new_channel_id']
    link = context.user_data['new_channel_link']

    if add_channel_to_db(name, telegram_id, link):
        await update.message.reply_text(f"Kanal '{name}' başarıyla eklendi.")
    else:
        await update.message.reply_text(f"Hata: Kanal ID `{telegram_id}` zaten mevcut veya bir hata oluştu.")

    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context):
    #Konuşmayı iptal eder."""
    await update.message.reply_text("İşlem iptal edildi.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Kanal Kaldırma Konuşması ---
async def remove_channel_start(update: Update, context):
    #Kanal kaldırma konuşmasını başlatır."""
    if not await admin_only(update, context):
        return ConversationHandler.END

    channels = get_channels()
    if not channels:
        await update.message.reply_text("Kaldırılabilecek bir kanal bulunmamaktadır.")
        return ConversationHandler.END

    mesaj = "Kaldırmak istediğiniz kanalın Telegram ID'sini girin:\n\n"
    for i, kanal in enumerate(channels):
        mesaj += f"{i+1}. Ad: {kanal['ad']}, ID: `{kanal['id']}`\n"
    mesaj += "\n(Örnek: -1001234567890)"

    await update.message.reply_text(mesaj, parse_mode='Markdown')
    return REMOVE_CHANNEL_CONFIRM

async def remove_channel_confirm(update: Update, context):
    #Kanal ID'sini alır ve kanalı kaldırır."""
    try:
        channel_id_to_remove = int(update.message.text)
        if remove_channel_from_db(channel_id_to_remove):
            await update.message.reply_text(f"Kanal ID `{channel_id_to_remove}` başarıyla kaldırıldı.")
        else:
            await update.message.reply_text(f"Kanal ID `{channel_id_to_remove}` bulunamadı veya bir hata oluştu.")
    except ValueError:
        await update.message.reply_text("Geçersiz Kanal ID'si. Lütfen sadece sayısal bir değer girin.")
    finally:
        context.user_data.clear()
        return ConversationHandler.END


# --- Auto Poster Fonksiyonları ---

async def send_auto_post(context, post_text):
    """Tüm kayıtlı kullanıcılara otomatik gönderi mesajını gönderir."""
    users = get_all_users()
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=post_text)
            logger.info(f"Otomatik gönderi kullanıcıya gönderildi: {user_id}")
        except Exception as e:
            logger.error(f"Kullanıcı {user_id} için otomatik gönderi hatası: {e}")
            # Hata durumunda kullanıcıyı veritabanından kaldırmayı düşünebilirsiniz
            # (örneğin, botu engellemişse)

async def start_auto_post(update: Update, context):
    #Auto Poster konuşmasını başlatır."""
    if not await admin_only(update, context):
        return ConversationHandler.END

    await update.message.reply_text("Lütfen otomatik gönderilecek mesajın metnini girin:")
    return AUTOPOST_TEXT

async def get_autopost_text(update: Update, context):
    #Otomatik gönderi metnini alır ve aralığı ister."""
    context.user_data['autopost_text'] = update.message.text
    await update.message.reply_text("Mesaj metni alındı.\nŞimdi lütfen kaç dakikada bir gönderileceğini girin (sadece sayı, örn: 60):")
    return AUTOPOST_INTERVAL

async def get_autopost_interval(update: Update, context):
    #Otomatik gönderi aralığını alır ve planlamayı yapar."""
    try:
        interval_minutes = int(update.message.text)
        if interval_minutes <= 0:
            await update.message.reply_text("Geçersiz dakika değeri. Lütfen 0'dan büyük bir sayı girin.")
            return AUTOPOST_INTERVAL

        post_text = context.user_data['autopost_text']

        # APScheduler'a görevi ekle
        job_id = f"autopost_{datetime.now().timestamp()}" # Benzersiz bir job ID oluştur
        scheduler.add_job(send_auto_post, 'interval', minutes=interval_minutes, args=[context, post_text], id=job_id)

        # Görevi veritabanına kaydet
        add_scheduled_post_to_db(job_id, post_text, interval_minutes)

        await update.message.reply_text(
            f"Otomatik gönderi başarıyla planlandı!\n"
            f"Metin: `{post_text}`\n"
            f"Her {interval_minutes} dakikada bir gönderilecek.\n"
            f"Bu gönderiyi durdurmak için `/autoposter_stop {job_id}` komutunu kullanabilirsiniz."
        )
        logger.info(f"Otomatik gönderi planlandı: {job_id}")

    except ValueError:
        await update.message.reply_text("Geçersiz dakika değeri. Lütfen sadece sayısal bir değer girin (örn: 60).")
        return AUTOPOST_INTERVAL
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def stop_auto_post(update: Update, context):
    #Otomatik gönderiyi durdurur."""
    if not await admin_only(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "Lütfen durdurulacak otomatik gönderinin ID'sini belirtin. "
            "Tümünü listelemek için `/autoposter_list` kullanın. "
            "Örnek: `/autoposter_stop autopost_1700000000.0`"
        )
        return

    job_id_to_stop = context.args[0]
    try:
        scheduler.remove_job(job_id_to_stop)
        if remove_scheduled_post_from_db(job_id_to_stop):
            await update.message.reply_text(f"Otomatik gönderi `{job_id_to_stop}` başarıyla durduruldu ve kaldırıldı.")
            logger.info(f"Otomatik gönderi durduruldu: {job_id_to_stop}")
        else:
            await update.message.reply_text(f"Otomatik gönderi `{job_id_to_stop}` bulunamadı veya veritabanından kaldırılamadı.")
    except Exception as e:
        await update.message.reply_text(f"Otomatik gönderiyi durdururken bir hata oluştu: {e}")
        logger.error(f"Otomatik gönderi durdurma hatası: {e}")


async def list_auto_posts(update: Update, context):
    """Planlanmış otomatik gönderileri listeler."""
    if not await admin_only(update, context):
        return

    posts = get_scheduled_posts()
    if not posts:
        await update.message.reply_text("Şu anda planlanmış otomatik gönderi bulunmamaktadır.")
        return

    mesaj = "Planlanmış Otomatik Gönderiler:\n\n"
    for post in posts:
        mesaj += (
            f"**ID**: `{post['job_id']}`\n"
            f"**Metin**: `{post['text'][:50]}...` (ilk 50 karakter)\n"
            f"**Aralık**: Her {post['interval']} dakika\n"
            f"**Oluşturulma**: {post['created_at']}\n"
            f"Durdurmak için: `/autoposter_stop {post['job_id']}`\n\n"
        )
    await update.message.reply_text(mesaj, parse_mode='Markdown')


async def unknown(update: Update, context):
    #Bilinmeyen komutlara cevap verir."""
    await update.message.reply_text("Üzgünüm, bu komutu anlamadım. Lütfen '/start' yazarak başlayın.")

def main():
    #Botu ve zamanlayıcıyı çalıştırır."""
    init_db() # Veritabanını başlat

    application = Application.builder().token(BOT_TOKEN).build()

    # Kullanıcı komutları
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_subscription, pattern='check_subscription'))

    # Admin komutları
    application.add_handler(CommandHandler("setvpn", set_vpn_code))
    application.add_handler(CommandHandler("showchannels", show_channels))

    # Kanal ekleme konuşması
    add_channel_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('addchannel', add_channel_start)],
        states={
            CHANNEL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_name)],
            CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_id)],
            CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_link)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(add_channel_conv_handler)

    # Kanal kaldırma konuşması
    remove_channel_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('removechannel', remove_channel_start)],
        states={
            REMOVE_CHANNEL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_channel_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(remove_channel_conv_handler)

    # Auto Poster konuşması
    autoposter_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('autoposter_add', start_auto_post)],
        states={
            AUTOPOST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_autopost_text)],
            AUTOPOST_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_autopost_interval)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(autoposter_conv_handler)

    # Auto Poster yönetim komutları
    application.add_handler(CommandHandler("autoposter_stop", stop_auto_post))
    application.add_handler(CommandHandler("autoposter_list", list_auto_posts))

    # Bilinmeyen komutlar için işleyici (en sona eklenmeli)
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Scheduler'ı başlat
    scheduler.start()
    logger.info("APScheduler başlatıldı.")

    # Botu çalıştırmaya başla
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    # Uygulama durduğunda scheduler'ı kapat
    scheduler.shutdown()
    logger.info("APScheduler kapatıldı.")


if __name__ == '__main__':
    main()
