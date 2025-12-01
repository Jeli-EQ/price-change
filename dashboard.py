import logging
import math
from flask import Flask, render_template, jsonify, send_file, request, session, redirect, url_for
from functools import wraps
import json
import os
import time
import pandas as pd
import io
import mplfinance as mpf
from binance.client import Client

# --- CONFIG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CHARTS_DIR = os.path.join(DATA_DIR, "charts")

# Data klasörü kontrolü (SuperScanner.py ile uyumlu olmalı)
if not os.path.exists(DATA_DIR) or not os.access(DATA_DIR, os.W_OK):
    # Eğer data klasörü yoksa veya yazma izni yoksa ana dizini kullan (Fallback)
    DATA_DIR = BASE_DIR

# Dosya Yolları
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
AO_TRACKER_FILE = os.path.join(DATA_DIR, "ao_tracker.json")
STOCH_HISTORY_FILE = os.path.join(DATA_DIR, "stoch_history.json")
BREAKOUTS_FILE = os.path.join(DATA_DIR, "breakouts.json")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")
FAVORITES_DATA_FILE = os.path.join(DATA_DIR, "favorites_data.json")
PRICE_CHANGE_HISTORY_FILE = os.path.join(DATA_DIR, "price_change_history.json")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_jeli_key_123") # Session için gerekli

# KULLANICI BİLGİLERİ
# Güvenlik için bu bilgileri Dokploy Environment kısmından çekiyoruz.
# Varsayılan olarak 'admin' atanır, lütfen Dokploy'dan değiştirin.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")

# --- LOGIN DECORATOR ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Hatalı Kullanıcı Adı veya Şifre!")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# Environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8197611031:AAEtIFwMfpG9vwWamFeSTBloYwXRpp2K0SA")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "O8zJEiEePz4FPKzKk8PGHogOPTAa9uP4Rmc91R139vLXrBzbzcw1CYNWJeiVu1NM")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "X4sGUyhpjCfC37xBfmo5uj9f5STUMVgbcXsIYEhKfkie1XDvIfNQ4YGV2MDtS6DT")

client = None
initialization_error = None
logger = logging.getLogger(__name__)

def initialize_binance_client():
    global client, initialization_error
    try:
        # recvWindow parametresini buradan kaldırdım, metodlara ekleyeceğiz
        client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
        # Test connection
        client.get_server_time()
        logger.info("Binance client initialized successfully")
        initialization_error = None
    except Exception as e:
        logger.error(f"Binance connection error: {e}")
        client = None
        initialization_error = str(e)

# Initial setup
initialize_binance_client()

BO_COINS = {
    "AAVEUSD", "ADAUSD", "AIXBTUSD", "ALGOUSD", "APTUSD", "ARBUSD", "ASTERUSD", 
    "ATOMUSD", "AVAXUSD", "BCHUSD", "BNBUSD", "BONKUSD", "BTCUSD", "CRVUSD", 
    "DOGEUSD", "DOTUSD", "ETCUSD", "ETHUSD", "FARTCOINUSD", "FILUSD", "FLOKIUSD", 
    "GRASSUSD", "HBARUSD", "HYPEUSD", "INJUSD", "IPUSD", "JTOUSD", "JUPUSD", 
    "KAITOUSD", "LDOUSD", "LINKUSD", "LTCUSD", "MOODENG", "NEARUSD", "ONDOUSD", 
    "OPUSD", "ORDIUSD", "PENGUUSD", "PEPEUSD", "PNUTUSD", "POLUSD", "POPCATUSD", 
    "PUMPUSD", "RENDERUSD", "SUSD", "SHIBUSD", "SOLUSD", "STXUSD", "SUIUSD", 
    "TAOUSD", "TIAUSD", "TONUSD", "TRUMPUSD", "TRXUSD", "UNIUSD", "VIRTUALUSD", 
    "WIFUSD", "WLDUSD", "XPLUSD", "XRPUSD"
}

@app.route('/')
@login_required
def index():
    return render_template('index.html')

def read_json_safe(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_json_safe(filepath, data):
    """Atomic write to prevent file corruption"""
    temp_file = filepath + ".tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        # Atomic rename
        os.replace(temp_file, filepath)
    except Exception as e:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise e

@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def handle_config():
    if request.method == 'POST':
        try:
            new_config = request.json
            # Mevcut configi oku ve güncelle (böylece eksik alan kalmaz)
            current_config = read_json_safe(CONFIG_FILE)
            current_config.update(new_config)
            
            save_json_safe(CONFIG_FILE, current_config)
            return jsonify({"status": "success", "config": current_config})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        # GET
        return jsonify(read_json_safe(CONFIG_FILE))

@app.route('/api/delete/tracker', methods=['POST'])
@login_required
def delete_tracker():
    try:
        symbols = request.json.get('symbols', [])
        data = read_json_safe(AO_TRACKER_FILE)
        for s in symbols:
            if s in data:
                if 'chart_file' in data[s]:
                    try:
                        os.remove(os.path.join(CHARTS_DIR, data[s]['chart_file']))
                    except: pass
                del data[s]
                
        save_json_safe(AO_TRACKER_FILE, data)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- FAVORITES ENDPOINTS ---

FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")
FAVORITES_DATA_FILE = os.path.join(DATA_DIR, "favorites_data.json")

@app.route('/api/favorites', methods=['GET', 'POST', 'DELETE'])
@login_required
def handle_favorites():
    try:
        current_favs = []
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r') as f:
                current_favs = json.load(f)
        
        if request.method == 'GET':
            return jsonify(current_favs)
            
        elif request.method == 'POST':
            data = request.json
            symbol = data.get('symbol')
            source = data.get('source', 'Manuel')
            
            # Check if symbol exists (handle both string and dict formats)
            exists = False
            for item in current_favs:
                if isinstance(item, dict):
                    if item.get('symbol') == symbol: exists = True
                elif item == symbol: exists = True
            
            if symbol and not exists:
                # Store as object
                current_favs.append({"symbol": symbol, "source": source})
                save_json_safe(FAVORITES_FILE, current_favs)
                
            return jsonify({"status": "success", "favorites": current_favs})
            
        elif request.method == 'DELETE':
            symbol = request.json.get('symbol')
            
            # Filter out the deleted symbol
            new_favs = []
            for item in current_favs:
                s = item.get('symbol') if isinstance(item, dict) else item
                if s != symbol:
                    new_favs.append(item)
            
            if len(new_favs) != len(current_favs):
                save_json_safe(FAVORITES_FILE, new_favs)
                
            return jsonify({"status": "success", "favorites": new_favs})
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/favorites_data', methods=['GET'])
@login_required
def get_favorites_data():
    return jsonify(read_json_safe(FAVORITES_DATA_FILE))

@app.route('/api/ao_tracker')
@login_required
def get_ao_tracker():
    data = read_json_safe(AO_TRACKER_FILE)
    for symbol in data:
        check_symbol = symbol.replace("USDT", "USD")
        data[symbol]['is_bo'] = check_symbol in BO_COINS or symbol in BO_COINS
    return jsonify(data)

@app.route('/api/stoch_history')
@login_required
def get_stoch_history():
    data = read_json_safe(STOCH_HISTORY_FILE)
    return jsonify(data)

@app.route('/api/breakouts')
@login_required
def get_breakouts():
    data = read_json_safe(BREAKOUTS_FILE)
    return jsonify(data)

@app.route('/api/price_change_history')
@login_required
def get_price_change_history():
    data = read_json_safe(PRICE_CHANGE_HISTORY_FILE)
    return jsonify(data)

@app.route('/api/chart_image/<filename>')
@login_required
def get_chart_image(filename):
    try:
        return send_file(os.path.join(CHARTS_DIR, filename), mimetype='image/png')
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@app.route('/api/chart/<symbol>')
@login_required
def get_chart(symbol):
    if not client:
        return f"Binance client not initialized: {initialization_error}", 500
        
    try:
        # Get interval
        interval = "15m"
        if os.path.exists(CONFIG_FILE):
             with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                interval = cfg.get("interval", "15m")

        limit = 200
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit, recvWindow=60000)
        if not klines:
            return "No data found", 404

        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        cols = ['open', 'high', 'low', 'close', 'volume']
        df[cols] = df[cols].apply(pd.to_numeric)
        
        # Check if it's in AO Tracker to draw lines
        ao_data = read_json_safe(AO_TRACKER_FILE).get(symbol, {})
        box_price = ao_data.get('box_price')
        signal_type = ao_data.get('signal')
        coords_raw = ao_data.get('coords')
        
        title_text = f"{symbol} ({interval})"
        
        alines_config = None
        if coords_raw and len(coords_raw) == 2:
            try:
                t1 = pd.to_datetime(coords_raw[0][0])
                p1 = coords_raw[0][1]
                t2 = pd.to_datetime(coords_raw[1][0])
                p2 = coords_raw[1][1]
                alines_config = dict(alines=[(t1, p1), (t2, p2)], colors=['green' if signal_type == "BULLISH" else 'red'], linewidths=2)
            except: pass

        s = mpf.make_mpf_style(base_mpf_style='binance', rc={'font.size': 8})
        
        plot_args = {
            'type': 'candle',
            'style': s,
            'title': title_text,
            'ylabel': 'Price',
            'savefig': dict(format='png', bbox_inches='tight')
        }
        
        if box_price:
            plot_args['hlines'] = dict(hlines=[box_price], colors=['purple'], linewidths=1.5, linestyle='--')
        if alines_config:
            plot_args['alines'] = alines_config
            
        buf = io.BytesIO()
        plot_args['savefig'] = buf
        
        mpf.plot(df.iloc[-100:], **plot_args)
        
        buf.seek(0)
        return send_file(buf, mimetype='image/png')
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- TRADING HELPER FUNCTIONS ---
def get_symbol_precision(symbol):
    """Get quantity precision (step size) for a symbol"""
    try:
        # Ensure client is initialized before use
        if not client:
            initialize_binance_client() # Attempt to initialize if not already
            if not client:
                logger.error("Binance client not initialized for trading operations.")
                return None

        info = client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == symbol:
                for f in s['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        return float(f['stepSize'])
    except Exception as e:
        logger.error(f"Error getting symbol precision for {symbol}: {e}")
        return None
    return None

from decimal import Decimal, ROUND_DOWN

def round_step_size(quantity, step_size):
    """Round quantity to valid step size using Decimal"""
    if step_size is None: return quantity
    try:
        qty = Decimal(str(quantity))
        step = Decimal(str(step_size))
        
        if step == 0: return quantity
        
        # Quantize to step size (Round Down to be safe for sell orders)
        rounded_qty = qty.quantize(step, rounding=ROUND_DOWN)
        return float(rounded_qty)
    except Exception as e:
        logger.error(f"Rounding error: {e}")
        return quantity

@app.route('/api/trade', methods=['POST'])
@login_required
def place_trade():
    if not client:
        initialize_binance_client() # Attempt to initialize if not already
        if not client:
            return jsonify({"error": f"Binance client not initialized: {initialization_error}"}), 500

    try:
        data = request.json
        symbol = data.get('symbol')
        amount_usdt = float(data.get('amount', 10)) # Cost (Margin)
        side = data.get('side') # BUY or SELL
        
        if not symbol or not side:
            return jsonify({"error": "Missing 'symbol' or 'side' in request."}), 400
        
        # Get config for leverage
        config = read_json_safe(CONFIG_FILE)
        leverage = int(config.get('leverage', 10))
        
        # 1. Set Leverage
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage, recvWindow=60000)
            logger.info(f"Set leverage for {symbol} to {leverage}")
        except Exception as e:
            logger.warning(f"Could not set leverage for {symbol} to {leverage}: {e}")
            # Eğer kaldıraç hatası alırsak, coinin max kaldıracını bulup onu ayarlamayı deneyebiliriz
            # Şimdilik varsayılan (mevcut) kaldıraçla devam ediyoruz ama logluyoruz.
            # İleride buraya otomatik max kaldıraç ayarı eklenebilir.

        # 2. Set Margin Type (Isolated)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED', recvWindow=60000)
            logger.info(f"Set margin type for {symbol} to ISOLATED")
        except Exception as e:
            # Usually fails if already isolated, which is fine
            pass

        # 3. Get Current Price
        ticker = client.futures_symbol_ticker(symbol=symbol, recvWindow=60000)
        price = float(ticker['price'])
        
        # 4. Calculate Quantity
        # Position Size = Margin * Leverage
        # Quantity = Position Size / Price
        position_size = amount_usdt * leverage
        raw_quantity = position_size / price
        
        # 5. Adjust Precision
        step_size = get_symbol_precision(symbol)
        logger.info(f"Symbol: {symbol}, Step Size: {step_size}") # DEBUG LOG
        
        if step_size is None:
             # Step size bulunamazsa işlem yapma, hata dön
             return jsonify({"error": f"Could not determine step size for {symbol}"}), 400

        quantity = round_step_size(raw_quantity, step_size)
        
        logger.info(f"Trade Calc: Symbol={symbol}, RawQty={raw_quantity}, StepSize={step_size}, FinalQty={quantity}")
        
        if quantity <= 0:
            return jsonify({"error": "Calculated quantity is zero or negative, cannot place order."}), 400

        # 6. Place Order
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity,
            recvWindow=60000
        )
        logger.info(f"Placed {side} order for {quantity} {symbol} at market price. Order ID: {order.get('orderId')}")
        
        return jsonify({"status": "success", "order": order, "quantity": quantity, "leverage": leverage})

    except Exception as e:
        logger.error(f"Trade error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/balance', methods=['GET'])
@login_required
def get_balance():
    if not client:
        initialize_binance_client()
        if not client:
            return jsonify({"error": f"Binance client not initialized: {initialization_error}"}), 500
    
    try:
        balances = client.futures_account_balance(recvWindow=60000)
        for b in balances:
            if b['asset'] == 'USDT':
                # withdrawAvailable yoksa availableBalance dene, o da yoksa balance kullan
                available = float(b.get('withdrawAvailable', b.get('availableBalance', b.get('balance'))))
                return jsonify({
                    "balance": float(b['balance']),
                    "available": available
                })
        return jsonify({"balance": 0, "available": 0})
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return jsonify({"error": f"Balance Error: {str(e)}"}), 500

@app.route('/api/positions', methods=['GET'])
@login_required
def get_positions():
    if not client:
        initialize_binance_client()
        if not client:
            return jsonify({"error": f"Binance client not initialized: {initialization_error}"}), 500
            
    try:
        # Get all positions
        positions = client.futures_position_information(recvWindow=60000)
        # Filter only active positions (amount != 0)
        active_positions = []
        for p in positions:
            amt = float(p['positionAmt'])
            if amt != 0:
                active_positions.append({
                    "symbol": p['symbol'],
                    "amount": amt,
                    "entryPrice": float(p['entryPrice']),
                    "markPrice": float(p.get('markPrice', 0)),
                    "unRealizedProfit": float(p['unRealizedProfit']),
                    "leverage": int(p.get('leverage', 1)),
                    "marginType": p.get('marginType', 'ISOLATED'),
                    "side": "LONG" if amt > 0 else "SHORT"
                })
        return jsonify(active_positions)
    except Exception as e:
        logger.error(f"Positions error: {e}")
        return jsonify({"error": f"Positions Error: {str(e)}"}), 500

@app.route('/api/close_position', methods=['POST'])
@login_required
def close_position():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    
    try:
        data = request.json
        symbol = data.get('symbol')
        
        # Get current position info to know amount and side
        positions = client.futures_position_information(symbol=symbol)
        target_pos = None
        for p in positions:
            if float(p['positionAmt']) != 0:
                target_pos = p
                break
        
        if not target_pos:
            return jsonify({"error": "No open position found for this symbol"}), 400
            
        amt = float(target_pos['positionAmt'])
        side = "SELL" if amt > 0 else "BUY" # Close LONG with SELL, SHORT with BUY
        quantity = abs(amt)
        
        # Place Market Order to Close
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity,
            reduceOnly=True # Important: Ensure it only closes position
        )
        
        return jsonify({"status": "success", "order": order})
        
    except Exception as e:
        logger.error(f"Close position error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
