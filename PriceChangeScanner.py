import logging
import asyncio
import json
import os
import time
import pandas as pd
import mplfinance as mpf
import matplotlib
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from binance.client import Client
from binance.exceptions import BinanceAPIException
from concurrent.futures import ThreadPoolExecutor

matplotlib.use('Agg')

# --- KULLANICI BİLGİLERİ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8426613857:AAGgeV1z34AqU35EkOQ6MbnPXHKYw47weDQ")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "O8zJEiEePz4FPKzKk8PGHogOPTAa9uP4Rmc91R139vLXrBzbzcw1CYNWJeiVu1NM")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "X4sGUyhpjCfC37xBfmo5uj9f5STUMVgbcXsIYEhKfkie1XDvIfNQ4YGV2MDtS6DT")

# --- BOT AYARLARI ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global Client
binance_client = None

# --- CONFIG & PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

try:
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
except Exception as e:
    logger.error(f"Data klasörü hatası ({e}), ana dizin kullanılıyor.")
    DATA_DIR = BASE_DIR

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PRICE_CHANGE_HISTORY_FILE = os.path.join(DATA_DIR, "price_change_history.json")
CHARTS_DIR = os.path.join(DATA_DIR, "charts")

if not os.path.exists(CHARTS_DIR):
    os.makedirs(CHARTS_DIR)

# Global State
price_change_history = {}
notified_signals = {} # {(symbol, timestamp): True}

# --- DOSYA YÖNETİMİ ---

def load_config():
    default_config = {
        "interval": "5", # Default 5 minutes
        "telegram_chat_id": None, 
        "price_change_threshold": 5.0,
        "price_change_interval": "5" # Legacy key
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "price_change_interval" in cfg:
                    cfg["interval"] = cfg["price_change_interval"].replace("m", "")
                for k, v in default_config.items():
                    cfg.setdefault(k, v)
                return cfg
        except Exception as e:
            logger.error(f"Config okunamadı: {e}")
    save_config(default_config)
    return default_config

def save_config(config: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Config kaydedilemedi: {e}")

def save_history():
    try:
        with open(PRICE_CHANGE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(price_change_history, f, indent=2)
    except Exception as e:
        logger.error(f"History save error: {e}")

def cleanup_old_charts():
    try:
        now = time.time()
        for f in os.listdir(CHARTS_DIR):
            fpath = os.path.join(CHARTS_DIR, f)
            if os.path.isfile(fpath):
                if now - os.path.getmtime(fpath) > 86400: # 24 saat
                    os.remove(fpath)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# --- DATA FETCHING ---

def fetch_data(client, symbol, limit=100):
    """
    Synchronous fetch of 1m candles.
    """
    try:
        klines = client.futures_klines(symbol=symbol, interval='1m', limit=limit)
        if not klines or len(klines) < limit:
            return None

        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        cols = ['open', 'high', 'low', 'close', 'volume']
        df[cols] = df[cols].apply(pd.to_numeric)
        
        return df
    except Exception as e:
        logger.error(f"{symbol} veri çekme hatası: {e}")
        return None

# --- CHART GENERATION ---

def generate_chart_image(symbol, df, change_percent, interval_minutes):
    filename = f"{symbol}_PC_{int(time.time())}.png"
    filepath = os.path.join(CHARTS_DIR, filename)
    
    direction = "YÜKSELİŞ" if change_percent > 0 else "DÜŞÜŞ"
    emoji = "🚀" if change_percent > 0 else "🔻"
    
    title_text = f"{symbol} {interval_minutes}m {direction} %{change_percent:.2f}"
    caption_text = (
        f"{emoji} <b>{symbol}</b> Ani {direction}!\n"
        f"Son {interval_minutes} dakika içinde <b>%{change_percent:.2f}</b> değişim."
    )

    # Custom TradingView Style
    mc = mpf.make_marketcolors(
        up='#089981', down='#f23645',
        edge={'up': '#089981', 'down': '#f23645'},
        wick={'up': '#089981', 'down': '#f23645'},
        volume={'up': '#089981', 'down': '#f23645'},
        ohlc='i'
    )
    
    s = mpf.make_mpf_style(
        base_mpf_style='nightclouds',
        marketcolors=mc,
        facecolor='#131722',
        edgecolor='#2a2e39',
        figcolor='#131722',
        gridcolor='#2a2e39',
        gridstyle='--',
        rc={'axes.labelcolor': '#d1d4dc', 'xtick.color': '#d1d4dc', 'ytick.color': '#d1d4dc', 'axes.edgecolor': '#2a2e39'}
    )
    
    plot_args = {
        'type': 'candle',
        'style': s,
        'title': dict(title=title_text, color='#d1d4dc', fontsize=12),
        'ylabel': 'Price',
        'savefig': dict(fname=filepath, facecolor='#131722', bbox_inches='tight'),
        'volume': True,
        'datetime_format': '%H:%M',
        'xrotation': 0,
        'tight_layout': True
    }
    
    mpf.plot(df.iloc[-60:], **plot_args)
    
    return filename, caption_text

async def send_chart(chat_id, bot, filename, caption):
    filepath = os.path.join(CHARTS_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            await bot.send_photo(
                chat_id=chat_id, 
                photo=f, 
                caption=caption,
                parse_mode="HTML"
            )

# --- SCANNER ---

def process_symbol_sync(client, symbol, interval_minutes, threshold):
    """
    Synchronous processing function to be run in a thread.
    Returns (symbol, filename, caption, change_percent, current_price, timestamp) if alert triggered, else None.
    """
    try:
        df = fetch_data(client, symbol, limit=100)
        if df is None or len(df) < interval_minutes + 1:
            return None

        past_candle = df.iloc[-interval_minutes]
        current_candle = df.iloc[-1]
        
        open_price = float(past_candle['open'])
        current_price = float(current_candle['close'])
        
        if open_price == 0: return None
        
        change_percent = ((current_price - open_price) / open_price) * 100
        
        if abs(change_percent) >= threshold:
            now = time.time()
            # Generate chart here (CPU bound, but okay in thread)
            filename, caption = generate_chart_image(symbol, df, change_percent, interval_minutes)
            
            return {
                "symbol": symbol,
                "filename": filename,
                "caption": caption,
                "change": change_percent,
                "price": current_price,
                "timestamp": str(current_candle.name),
                "epoch": now
            }
            
    except Exception as e:
        logger.error(f"Error processing {symbol}: {e}")
    
    return None

async def scanner(application: Application):
    global binance_client
    if binance_client is None:
        try:
            # Configure requests session with larger pool
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
            session.mount('https://', adapter)
            
            binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
            binance_client.session = session
            logger.info("Binance Client (Sync) başlatıldı (Custom Session).")
        except Exception as e:
            logger.error(f"Binance client başlatılamadı: {e}")
            return

    config = load_config()
    chat_id = config.get("telegram_chat_id")
    
    try:
        interval_str = str(config.get("interval", "5")).replace("m", "")
        interval_minutes = int(interval_str)
    except:
        interval_minutes = 5
        
    threshold = float(config.get("price_change_threshold", 5.0))
    
    if not chat_id:
        return

    try:
        # Fetch symbols (Sync)
        exchange_info = binance_client.futures_exchange_info()
        symbols = [s["symbol"] for s in exchange_info["symbols"] if s["symbol"].endswith("USDT") and "BTCST" not in s["symbol"]]
        
        logger.info(f"Tarama Başlıyor... ({len(symbols)} coin, {interval_minutes}m Rolling, Limit: %{threshold})")

        # Reduce max_workers to 5 to avoid API rate limits
        loop = asyncio.get_running_loop()
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                loop.run_in_executor(
                    executor, 
                    process_symbol_sync, 
                    binance_client, 
                    symbol, 
                    interval_minutes, 
                    threshold
                )
                for symbol in symbols
            ]
            
            results = await asyncio.gather(*futures)

        # Process results
        for res in results:
            if res:
                symbol = res["symbol"]
                now = res["epoch"]
                
                last_notify_time = notified_signals.get(symbol, 0)
                if (now - last_notify_time) > 300:
                    logger.info(f"Sinyal: {symbol} %{res['change']:.2f}")
                    
                    await send_chart(chat_id, application.bot, res["filename"], res["caption"])
                    
                    notified_signals[symbol] = now
                    
                    price_change_history[symbol] = {
                        "symbol": symbol,
                        "change": res["change"],
                        "price": res["price"],
                        "timestamp": res["timestamp"],
                        "timestamp_epoch": now,
                        "chart_file": res["filename"]
                    }
                    save_history()

        cleanup_old_charts()
        logger.info("Tarama döngüsü tamamlandı.")

    except Exception as e:
        logger.error(f"Tarama döngüsü hatası: {e}")

async def background_scanner(application: Application):
    while True:
        await scanner(application)
        await asyncio.sleep(20) # Wait 20 seconds between scans to respect API limits

# --- TELEGRAM COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    config = load_config()
    config["telegram_chat_id"] = chat_id
    save_config(config)
    await update.message.reply_text(f"PriceChange Scanner Başladı! Chat ID: {chat_id}")

async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        new_threshold = float(context.args[0])
        config = load_config()
        config["price_change_threshold"] = new_threshold
        save_config(config)
        await update.message.reply_text(f"Eşik ayarlandı: %{new_threshold}")
    except:
        await update.message.reply_text("Kullanım: /threshold 5")

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        new_interval = context.args[0].replace("m", "")
        if not new_interval.isdigit():
             await update.message.reply_text("Lütfen dakika cinsinden bir sayı girin. Örnek: 5")
             return
             
        config = load_config()
        config["interval"] = new_interval
        config["price_change_interval"] = new_interval
        save_config(config)
        await update.message.reply_text(f"Zaman aralığı ayarlandı: {new_interval} dakika (Rolling Window)")
    except:
        await update.message.reply_text("Kullanım: /interval 5")

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("threshold", set_threshold))
    application.add_handler(CommandHandler("interval", set_interval))
    
    # Start background scanner
    loop = asyncio.get_event_loop()
    loop.create_task(background_scanner(application))

    application.run_polling()

if __name__ == "__main__":
    main()
