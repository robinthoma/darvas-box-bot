import os
from dotenv import load_dotenv

load_dotenv()

# Fyers credentials
FYERS_APP_ID = os.getenv("FYERS_APP_ID")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI")

# Telegram credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Trading constants
CAPITAL_PER_TRADE = 10000
MAX_POSITIONS = 5
BREAKOUT_BUFFER = 0.0015  # 0.15% above box top
EVAL_TIME = "15:45"       # IST

# File paths
TOKEN_FILE = "token.json"
DB_PATH = "darvas_bot.db"
