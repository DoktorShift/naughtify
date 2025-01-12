#!/usr/bin/python3
import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, flash, make_response
from flask_cors import CORS
from telegram import Bot, ParseMode, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from dotenv import load_dotenv, set_key
import requests
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import threading
import qrcode
import io
import base64
import json
from urllib.parse import urlparse
import re
import uuid
from functools import wraps

# --------------------- Configuration and Setup ---------------------

load_dotenv()

# Essential Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

# (Optional) multiple LNbits read-only API keys
MULTI_LNBITS_API_KEYS = os.getenv("MULTI_LNBITS_API_KEYS", "").strip()

# Optional URLs
OVERWATCH_URL = os.getenv("OVERWATCH_URL")
DONATIONS_URL = os.getenv("DONATIONS_URL")
INFORMATION_URL = os.getenv("INFORMATION_URL")

# LNURLP ID (Required if Donations are enabled)
LNURLP_ID = os.getenv("LNURLP_ID") if DONATIONS_URL else None

# Files
FORBIDDEN_WORDS_FILE = os.getenv("FORBIDDEN_WORDS_FILE", "forbidden_words.txt")
PROCESSED_PAYMENTS_FILE = os.getenv("PROCESSED_PAYMENTS_FILE", "processed_payments.txt")
CURRENT_BALANCE_FILE = os.getenv("CURRENT_BALANCE_FILE", "current-balance.txt")
DONATIONS_FILE = os.getenv("DONATIONS_FILE", "donations.json")

# Thresholds and Intervals
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "1"))
HIGHLIGHT_THRESHOLD = int(os.getenv("HIGHLIGHT_THRESHOLD", "2100"))
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "60"))

# Server Configuration
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "5009"))

# Secret Key for Flask Sessions
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24))

# Validate Essential Variables
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL
}
missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise EnvironmentError(f"Essential environment variables missing: {', '.join(missing_vars)}")

if DONATIONS_URL and not LNURLP_ID:
    raise EnvironmentError("LNURLP_ID must be set when DONATIONS_URL is provided.")

# Parse LNbits domain
parsed_url = urlparse(LNBITS_URL)
LNBITS_DOMAIN = parsed_url.netloc
if not LNBITS_DOMAIN:
    raise ValueError("Invalid LNBITS_URL provided. Cannot parse domain.")

# Initialize Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# --------------------- Logging Configuration ---------------------

logger = logging.getLogger("lnbits_logger")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

info_handler = RotatingFileHandler("app.log", maxBytes=2 * 1024 * 1024, backupCount=3)
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(formatter)

debug_handler = RotatingFileHandler("debug.log", maxBytes=5 * 1024 * 1024, backupCount=3)
debug_handler.setLevel(logging.DEBUG)
debug_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger.addHandler(info_handler)
logger.addHandler(debug_handler)
logger.addHandler(console_handler)

logging.getLogger('apscheduler').setLevel(logging.WARNING)

# --------------------- Flask App Initialization ---------------------

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app)

# --------------------- Global Variables ---------------------

processed_payments = set()
donations = []
total_donations = 0
last_update = datetime.utcnow()

# We'll track the "latest_balance" for the MAIN wallet
latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

# We'll hold the last known transactions (MAIN wallet) for /status route
latest_payments = []

# --------------------- Helper Functions ---------------------

def get_main_inline_keyboard():
    balance_button = InlineKeyboardButton("💰 Balance", callback_data='balance')
    latest_transactions_button = InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline')
    
    if DONATIONS_URL:
        live_ticker_button = InlineKeyboardButton("📡 Live Ticker", url=DONATIONS_URL)
    else:
        live_ticker_button = InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline')
    
    if OVERWATCH_URL:
        overwatch_button = InlineKeyboardButton("📊 Overwatch", url=OVERWATCH_URL)
    else:
        overwatch_button = InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline')
    
    if LNBITS_URL:
        lnbits_button = InlineKeyboardButton("⚡ LNBits", url=LNBITS_URL)
    else:
        lnbits_button = InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')

    inline_keyboard = [
        [balance_button],
        [latest_transactions_button, live_ticker_button],
        [overwatch_button, lnbits_button]
    ]
    logger.debug("Main inline keyboard created.")
    return InlineKeyboardMarkup(inline_keyboard)

def get_main_keyboard():
    balance_button = ["💰 Balance"]
    main_options_row_1 = ["📊 Overwatch", "📡 Live Ticker"]
    main_options_row_2 = ["📜 Latest Transactions", "⚡ LNBits"]

    keyboard = [
        balance_button,
        main_options_row_1,
        main_options_row_2
    ]

    logger.debug("Main reply keyboard created.")
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def load_forbidden_words(file_path):
    forbidden = set()
    try:
        with open(file_path, 'r') as f:
            for line in f:
                word = line.strip()
                if word:
                    forbidden.add(word)
        logger.debug(f"{len(forbidden)} forbidden words loaded from {file_path}.")
    except FileNotFoundError:
        logger.error(f"Forbidden words file not found: {file_path}.")
    except Exception as e:
        logger.error(f"Error loading forbidden words from {file_path}: {e}")
        logger.debug(traceback.format_exc())
    return forbidden

FORBIDDEN_WORDS = load_forbidden_words(FORBIDDEN_WORDS_FILE)

def sanitize_memo(memo):
    if not memo:
        logger.debug("No memo provided to sanitize.")
        return "No memo provided."

    def replace_match(match):
        word = match.group()
        logger.debug(f"Sanitizing word: {word}")
        return '*' * len(word)

    if not FORBIDDEN_WORDS:
        logger.debug("No forbidden words to sanitize.")
        return memo
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, FORBIDDEN_WORDS)) + r')\b', re.IGNORECASE)
    sanitized_memo = pattern.sub(replace_match, memo)
    logger.debug(f"Sanitized memo: Original: '{memo}' -> Sanitized: '{sanitized_memo}'")
    return sanitized_memo

def load_processed_payments():
    processed = set()
    if os.path.exists(PROCESSED_PAYMENTS_FILE):
        try:
            with open(PROCESSED_PAYMENTS_FILE, 'r') as f:
                for line in f:
                    processed.add(line.strip())
            logger.debug(f"{len(processed)} processed payment hashes loaded.")
        except Exception as e:
            logger.error(f"Error loading processed payments: {e}")
            logger.debug(traceback.format_exc())
    else:
        logger.info("Processed payments file does not exist. Starting fresh.")
    return processed

def add_processed_payment(payment_hash_with_key):
    """
    We store payment hashes with a wallet identifier to avoid collisions if two wallets share the same hash.
    E.g., 'KEY1_paymenthash'.
    """
    try:
        with open(PROCESSED_PAYMENTS_FILE, 'a') as f:
            f.write(f"{payment_hash_with_key}\n")
        logger.debug(f"Payment {payment_hash_with_key} added to processed list.")
    except Exception as e:
        logger.error(f"Error adding processed payment: {e}")
        logger.debug(traceback.format_exc())

def load_donations():
    global donations, total_donations
    if os.path.exists(DONATIONS_FILE) and DONATIONS_URL and LNURLP_ID:
        try:
            with open(DONATIONS_FILE, 'r') as f:
                data = json.load(f)
                donations = data.get("donations", [])
                total_donations = data.get("total_donations", 0)
                for donation in donations:
                    if "id" not in donation:
                        donation["id"] = str(uuid.uuid4())
                    if "likes" not in donation:
                        donation["likes"] = 0
                    if "dislikes" not in donation:
                        donation["dislikes"] = 0
            logger.debug(f"{len(donations)} donations loaded from file.")
        except Exception as e:
            logger.error(f"Error loading donations: {e}")
            logger.debug(traceback.format_exc())
    else:
        logger.info("Donations file does not exist or donations not enabled.")

def save_donations():
    if DONATIONS_URL and LNURLP_ID:
        try:
            with open(DONATIONS_FILE, 'w') as f:
                json.dump({
                    "total_donations": total_donations,
                    "donations": donations
                }, f, indent=4)
            logger.debug("Donation data successfully saved.")
        except Exception as e:
            logger.error(f"Error saving donations: {e}")
            logger.debug(traceback.format_exc())

processed_payments = load_processed_payments()
load_donations()

def sanitize_donations():
    global donations
    try:
        for donation in donations:
            donation['memo'] = sanitize_memo(donation.get('memo', ''))
        save_donations()
        logger.info("Donations sanitized and saved.")
    except Exception as e:
        logger.error(f"Error sanitizing donations: {e}")
        logger.debug(traceback.format_exc())

def handle_ticker_ban(update, context):
    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        bot.send_message(chat_id, text="❌ Please provide at least one word to ban. Example: /ticker_ban badword")
        logger.debug("Ticker ban command received without arguments.")
        return

    words_to_ban = [word.strip() for word in context.args if word.strip()]
    if not words_to_ban:
        bot.send_message(chat_id, text="❌ No valid words provided.")
        logger.debug("Ticker ban command received with no valid words.")
        return

    added_words = []
    duplicate_words = []

    try:
        with open(FORBIDDEN_WORDS_FILE, 'a') as f:
            for word in words_to_ban:
                if word.lower() in (fw.lower() for fw in FORBIDDEN_WORDS):
                    duplicate_words.append(word)
                else:
                    f.write(word + '\n')
                    FORBIDDEN_WORDS.add(word)
                    added_words.append(word)
        logger.debug(f"Words to ban processed: Added {added_words}, Duplicates {duplicate_words}.")

        sanitize_donations()
        global last_update
        last_update = datetime.utcnow()

        if added_words:
            if len(added_words) == 1:
                success_message = f"✅ Great! I've successfully added the word '{added_words[0]}' to the banned list. The Live Ticker will update shortly!"
            else:
                words_formatted = "', '".join(added_words)
                success_message = f"✅ Great! I've added these words to the banned list: '{words_formatted}'. The Live Ticker will update shortly!"
            bot.send_message(chat_id, text=success_message)
            logger.info(f"Added forbidden words: {added_words}")
        if duplicate_words:
            if len(duplicate_words) == 1:
                duplicate_message = f"⚠️ The word '{duplicate_words[0]}' was already banned."
            else:
                words_formatted = "', '".join(duplicate_words)
                duplicate_message = f"⚠️ The following words were already banned: '{words_formatted}'."
            bot.send_message(chat_id, text=duplicate_message)
            logger.info(f"Duplicate forbidden words attempted to add: {duplicate_words}")
    except Exception as e:
        logger.error(f"Error adding words to forbidden list: {e}")
        bot.send_message(chat_id, text="❌ An error occurred while banning words. Please try again.")
        logger.debug(traceback.format_exc())

def fetch_api(endpoint, custom_api_key=None):
    """
    Generic function to fetch LNbits endpoints.
    If 'custom_api_key' is provided, that key is used instead of the global LNBITS_READONLY_API_KEY.
    """
    if custom_api_key is None:
        custom_api_key = LNBITS_READONLY_API_KEY

    url = f"{LNBITS_URL}/api/v1/{endpoint}"
    headers = {"X-Api-Key": custom_api_key}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Data fetched from {endpoint} with key {custom_api_key[:5]}...: {data}")
            return data
        else:
            logger.error(f"Error fetching {endpoint}. Status Code: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching {endpoint}: {e}")
        logger.debug(traceback.format_exc())
        return None

def fetch_pay_links():
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations not enabled. Skipping fetch_pay_links.")
        return None
    url = f"{LNBITS_URL}/lnurlp/api/v1/links"
    headers = {"X-Api-Key": LNBITS_READONLY_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Pay Links fetched: {data}")
            return data
        else:
            logger.error(f"Error fetching Pay Links. Status Code: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching Pay Links: {e}")
        logger.debug(traceback.format_exc())
        return None

def get_lnurlp_info(lnurlp_id):
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations not enabled. Skipping get_lnurlp_info.")
        return None

    pay_links = fetch_pay_links()
    if pay_links is None:
        logger.error("Could not fetch Pay Links.")
        return None

    for pay_link in pay_links:
        if pay_link.get("id") == lnurlp_id:
            logger.debug(f"Matching Pay Link found: {pay_link}")
            return pay_link
    logger.error(f"No Pay Link found with ID {lnurlp_id}.")
    return None

def fetch_donation_details():
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations not enabled. Returning basic data.")
        return {
            "total_donations": total_donations,
            "donations": donations,
            "lightning_address": "Unavailable",
            "lnurl": "Unavailable",
            "highlight_threshold": HIGHLIGHT_THRESHOLD
        }
    lnurlp_info = get_lnurlp_info(LNURLP_ID)
    if lnurlp_info is None:
        logger.error("No LNURLp info found.")
        return {
            "total_donations": total_donations,
            "donations": donations,
            "lightning_address": "Unavailable",
            "lnurl": "Unavailable",
            "highlight_threshold": HIGHLIGHT_THRESHOLD
        }

    username = lnurlp_info.get('username', 'Unknown')
    lightning_address = f"{username}@{LNBITS_DOMAIN}"
    lnurl = lnurlp_info.get('lnurl', '')

    logger.debug(f"Donation details fetched: Lightning Address: {lightning_address}, LNURL: {lnurl}")
    return {
        "total_donations": total_donations,
        "donations": donations,
        "lightning_address": lightning_address,
        "lnurl": lnurl,
        "highlight_threshold": HIGHLIGHT_THRESHOLD
    }

def update_donations_with_details(data):
    donation_details = fetch_donation_details()
    data.update({
        "lightning_address": donation_details.get("lightning_address"),
        "lnurl": donation_details.get("lnurl")
    })
    logger.debug("Donation details updated with additional information.")
    return data

def updateDonations(data):
    updated_data = update_donations_with_details(data)
    if updated_data["donations"]:
        latestDonation = updated_data["donations"][-1]
        logger.info(f'Latest donation: {latestDonation["amount"]} sats - "{latestDonation["memo"]}"')
    else:
        logger.info('Latest donation: None yet.')
    save_donations()

def parse_time(time_input):
    if not time_input:
        logger.warning("No 'time' field found, using current time.")
        return datetime.utcnow()
    if isinstance(time_input, str):
        try:
            date = datetime.strptime(time_input, "%Y-%m-%dT%H:%M:%S.%fZ")
            logger.debug(f"Parsed time string: {time_input} -> {date}")
        except ValueError:
            try:
                date = datetime.strptime(time_input, "%Y-%m-%dT%H:%M:%SZ")
                logger.debug(f"Parsed time string: {time_input} -> {date}")
            except ValueError:
                logger.error(f"Unable to parse time string: {time_input}. Using current time.")
                date = datetime.utcnow()
    elif isinstance(time_input, (int, float)):
        try:
            date = datetime.fromtimestamp(time_input)
            logger.debug(f"Parsed timestamp: {time_input} -> {date}")
        except Exception as e:
            logger.error(f"Unable to parse timestamp: {time_input}, error: {e}. Using current time.")
            date = datetime.utcnow()
    else:
        logger.error(f"Unsupported time format: {time_input}. Using current time.")
        date = datetime.utcnow()
    return date

def notify_transaction(payment, direction, wallet_name=""):
    """
    Sends a Telegram notification for an incoming or outgoing payment.
    wallet_name is used to indicate which wallet triggered the transaction.
    """
    try:
        amount = payment["amount"]
        memo = payment["memo"]
        date = payment["date"]
        emoji = "🟢" if direction == "incoming" else "🔴"
        sign = "+" if direction == "incoming" else "-"
        transaction_type = "Incoming Payment" if direction == "incoming" else "Outgoing Payment"

        name_str = f" (Wallet: {wallet_name})" if wallet_name else ""
        message = (
            f"{emoji} *{transaction_type}{name_str}*\n"
            f"💰 Amount: {sign}{amount} sats\n"
            f"✉️ Memo: {memo}"
        )

        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Notification for {transaction_type} from {wallet_name} sent successfully.")
    except Exception as e:
        logger.error(f"Error sending transaction notification: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Single-Wallet Payment Checker (Legacy for Donations) ---------------------
def send_latest_payments_singlewallet():
    """
    For the MAIN wallet (LNBITS_READONLY_API_KEY), fetch transactions
    and do the donation logic, plus single "latest_payments" update.
    """
    global total_donations, donations, last_update, latest_balance, latest_payments
    logger.info("Fetching latest payments for the main (default) wallet...")

    payments = fetch_api("payments", custom_api_key=LNBITS_READONLY_API_KEY)
    if payments is None or not isinstance(payments, list):
        logger.warning("No payments fetched (or invalid format) for main wallet.")
        return

    sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
    latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]
    latest_payments = latest.copy()  # For /status

    if not latest:
        logger.info("No payments found for main wallet.")
        return

    incoming_payments = []
    outgoing_payments = []

    for payment in latest:
        payment_hash = payment.get("payment_hash")
        # Store processed key as "MAIN_<hash>"
        payment_hash_with_key = f"MAIN_{payment_hash}"

        if payment_hash_with_key in processed_payments:
            logger.debug(f"Payment {payment_hash_with_key} already processed. Skipping.")
            continue

        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo provided."))
        status = payment.get("status", "completed")
        time_str = payment.get("time", None)
        date = parse_time(time_str)
        formatted_date = date.isoformat()

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0
            logger.warning(f"Invalid amount_msat value: {amount_msat}")

        if status.lower() == "pending":
            logger.debug(f"Payment {payment_hash_with_key} is pending. Skipping.")
            continue

        if amount_msat > 0:
            incoming_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})
        elif amount_msat < 0:
            outgoing_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})

        # Donation logic (only for main wallet)
        if DONATIONS_URL and LNURLP_ID:
            extra_data = payment.get("extra", {})
            lnurlp_id_payment = extra_data.get("link")
            if lnurlp_id_payment == LNURLP_ID:
                donation_memo = sanitize_memo(extra_data.get("comment", "No memo provided."))
                try:
                    donation_amount_msat = int(extra_data.get("extra", 0))
                    donation_amount_sats = donation_amount_msat / 1000
                except (ValueError, TypeError):
                    donation_amount_sats = amount_sats
                    logger.warning(f"Invalid donation amount_msat: {extra_data.get('extra', 0)}. Using amount_sats: {amount_sats}")
                donation = {
                    "id": str(uuid.uuid4()),
                    "date": formatted_date,
                    "memo": donation_memo,
                    "amount": donation_amount_sats,
                    "likes": 0,
                    "dislikes": 0
                }
                donations.append(donation)
                total_donations += donation_amount_sats
                last_update = datetime.utcnow()
                logger.info(f"New donation detected: {donation_amount_sats} sats - {donation_memo}")
                updateDonations({"total_donations": total_donations, "donations": donations})

        processed_payments.add(payment_hash_with_key)
        add_processed_payment(payment_hash_with_key)
        logger.debug(f"Payment {payment_hash_with_key} processed and added to processed payments.")

    # Update latest_balance for the main wallet
    wallet_info = fetch_api("wallet", custom_api_key=LNBITS_READONLY_API_KEY)
    if wallet_info:
        current_balance_msat = wallet_info.get("balance", 0)
        current_balance_sats = current_balance_msat / 1000
        latest_balance = {
            "balance_sats": int(current_balance_sats),
            "last_change": datetime.utcnow().isoformat(),
            "memo": "Latest balance fetched."
        }
        logger.debug(f"Updated latest_balance: {latest_balance}")

    # Send notifications for main wallet transactions
    for payment in incoming_payments:
        notify_transaction(payment, "incoming", wallet_name="MainWallet")
    for payment in outgoing_payments:
        notify_transaction(payment, "outgoing", wallet_name="MainWallet")


# --------------------- Multi-Wallet Payment Checker ---------------------
def send_latest_payments_multiwallet():
    """
    For each LNbits API key in MULTI_LNBITS_API_KEYS, fetch recent payments
    and notify Telegram for new incoming/outgoing payments. 
    DOES NOT handle donation logic; that remains on the main wallet above.
    """
    if not MULTI_LNBITS_API_KEYS:
        logger.debug("MULTI_LNBITS_API_KEYS is empty; skipping multiwallet payments.")
        return

    keys = [k.strip() for k in MULTI_LNBITS_API_KEYS.split(",") if k.strip()]
    if not keys:
        logger.debug("No valid keys in MULTI_LNBITS_API_KEYS.")
        return

    for key in keys:
        # Try to retrieve the wallet name
        w_info = fetch_api("wallet", custom_api_key=key)
        wallet_name = w_info.get("name", "UnknownWallet") if w_info else "UnknownWallet"

        logger.info(f"Fetching latest payments for wallet '{wallet_name}'...")

        payments = fetch_api("payments", custom_api_key=key)
        if payments is None or not isinstance(payments, list):
            logger.warning(f"No payments or invalid format for wallet '{wallet_name}'.")
            continue

        sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
        relevant_payments = sorted_payments[:LATEST_TRANSACTIONS_COUNT]

        if not relevant_payments:
            logger.info(f"No recent payments found for wallet '{wallet_name}'.")
            continue

        incoming_payments = []
        outgoing_payments = []

        for payment in relevant_payments:
            payment_hash = payment.get("payment_hash")
            # Tag the payment hash with the wallet key or name to keep them unique
            payment_hash_with_key = f"{wallet_name}_{payment_hash}"

            if payment_hash_with_key in processed_payments:
                logger.debug(f"Payment {payment_hash_with_key} already processed. Skipping.")
                continue

            amount_msat = payment.get("amount", 0)
            memo = sanitize_memo(payment.get("memo", "No memo provided."))
            status = payment.get("status", "completed")
            time_str = payment.get("time", None)
            date = parse_time(time_str)
            formatted_date = date.isoformat()

            try:
                amount_sats = int(abs(amount_msat) / 1000)
            except ValueError:
                amount_sats = 0
                logger.warning(f"Invalid amount_msat value: {amount_msat} for wallet '{wallet_name}'")

            if status.lower() == "pending":
                logger.debug(f"Payment {payment_hash_with_key} is pending. Skipping.")
                continue

            if amount_msat > 0:
                incoming_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})
            elif amount_msat < 0:
                outgoing_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})

            processed_payments.add(payment_hash_with_key)
            add_processed_payment(payment_hash_with_key)
            logger.debug(f"Payment {payment_hash_with_key} processed for wallet '{wallet_name}'.")

        # Send notifications for multi-wallet
        for inc in incoming_payments:
            notify_transaction(inc, "incoming", wallet_name=wallet_name)
        for out in outgoing_payments:
            notify_transaction(out, "outgoing", wallet_name=wallet_name)

# --------------------- Multi-Wallet Balance Checker ---------------------
def check_multi_wallet_balances():
    """
    Periodically checks the balances of all LNbits wallets listed in MULTI_LNBITS_API_KEYS
    + the main wallet as well, in case we want direct balance-change notifications 
    for each wallet. 
    """
    # 1) Check main wallet
    try:
        logger.debug("Checking main wallet balance for changes...")
        main_wallet_info = fetch_api("wallet", custom_api_key=LNBITS_READONLY_API_KEY)
        if main_wallet_info:
            current_balance_msat = main_wallet_info.get("balance", 0)
            name = main_wallet_info.get("name", "MainWallet")

            # We store last balance in a separate file
            tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            main_file_path = os.path.join(tmp_dir, f"lnbits_wallet_{name.replace(' ', '_')}.txt")

            if not os.path.exists(main_file_path):
                last_balance_msat = 0
                with open(main_file_path, 'w') as f:
                    f.write(str(current_balance_msat))
            else:
                with open(main_file_path, 'r') as f:
                    content = f.read().strip()
                if content == '':
                    last_balance_msat = 0
                else:
                    try:
                        last_balance_msat = float(content)
                    except ValueError:
                        last_balance_msat = 0

            if current_balance_msat != last_balance_msat:
                diff = current_balance_msat - last_balance_msat
                with open(main_file_path, 'w') as f:
                    f.write(str(current_balance_msat))

                diff_sat = f"{diff / 1000:,.3f}"
                old_sat = f"{last_balance_msat / 1000:,.3f}" if last_balance_msat != 0 else "0"
                new_sat = f"{current_balance_msat / 1000:,.3f}"

                msg = (
                    f"₿💰₿ ➽ Balance change for *{name}* (main wallet)!\n"
                    f"Difference: {diff_sat} sats – from {old_sat} to {new_sat}."
                )
                bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Balance change for main wallet '{name}' was notified.")
            else:
                logger.debug(f"No balance change for main wallet '{name}'.")
        else:
            logger.warning("Could not fetch main wallet info for balance check.")
    except Exception as e:
        logger.error(f"Error checking main wallet balance: {e}")
        logger.debug(traceback.format_exc())

    # 2) Check additional wallets from MULTI_LNBITS_API_KEYS
    if not MULTI_LNBITS_API_KEYS:
        logger.debug("MULTI_LNBITS_API_KEYS is empty; no additional balances to check.")
        return

    keys = [k.strip() for k in MULTI_LNBITS_API_KEYS.split(",") if k.strip()]
    if not keys:
        logger.debug("No valid keys in MULTI_LNBITS_API_KEYS for balance checks.")
        return

    for key in keys:
        try:
            w_info = fetch_api("wallet", custom_api_key=key)
            if not w_info:
                logger.warning(f"Could not fetch wallet info for key: {key[:5]}...")
                continue

            balance_msat = w_info.get("balance", 0)
            wallet_name = w_info.get("name", "UnknownWallet")

            tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            file_path = os.path.join(tmp_dir, f"lnbits_wallet_{wallet_name.replace(' ', '_')}.txt")

            if not os.path.exists(file_path):
                last_balance_msat = 0
                with open(file_path, 'w') as f:
                    f.write(str(balance_msat))
            else:
                with open(file_path, 'r') as f:
                    content = f.read().strip()
                if content == '':
                    last_balance_msat = 0
                else:
                    try:
                        last_balance_msat = float(content)
                    except ValueError:
                        last_balance_msat = 0

            if balance_msat != last_balance_msat:
                difference = balance_msat - last_balance_msat
                with open(file_path, 'w') as f:
                    f.write(str(balance_msat))

                diff_sat = f"{difference / 1000:,.3f}"
                old_sat = f"{last_balance_msat / 1000:,.3f}" if last_balance_msat != 0 else "0"
                new_sat = f"{balance_msat / 1000:,.3f}"

                message = (
                    f"₿💰₿ ➽ Yippee, wallet *{wallet_name}* changed!\n"
                    f"Balance shifted by {diff_sat} sats – from {old_sat} to {new_sat}."
                )
                bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Balance change for wallet '{wallet_name}' was notified.")
            else:
                logger.debug(f"No balance change for wallet '{wallet_name}'.")
        except Exception as e:
            logger.error(f"Error checking balance for multi wallet key {key[:5]}: {e}")
            logger.debug(traceback.format_exc())

# --------------------- Commands and Handlers ---------------------

def send_balance_message(chat_id):
    logger.info(f"Fetching MAIN wallet balance for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet", custom_api_key=LNBITS_READONLY_API_KEY)
    if wallet_info is None:
        bot.send_message(chat_id, text="❌ Unable to fetch balance at the moment. Please try again.")
        logger.error("Failed to fetch main wallet balance.")
        return
    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = int(current_balance_msat / 1000)
    balance_text = f"💰 *Current Balance (Main Wallet):* {current_balance_sats} sats"

    try:
        bot.send_message(
            chat_id=chat_id,
            text=balance_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Balance message (main wallet) sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending balance message: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_transactions_message(chat_id, page=1, message_id=None):
    logger.info(f"Fetching transactions for MAIN wallet, page: {page}")
    payments = fetch_api("payments", custom_api_key=LNBITS_READONLY_API_KEY)
    if payments is None:
        bot.send_message(chat_id, text="❌ Unable to fetch transactions right now.")
        logger.error("Failed to fetch transactions for MAIN wallet.")
        return

    filtered_payments = [p for p in payments if p.get("status", "").lower() != "pending"]
    sorted_payments = sorted(filtered_payments, key=lambda x: x.get("time", ""), reverse=True)
    total_transactions = len(sorted_payments)
    transactions_per_page = 13
    total_pages = (total_transactions + transactions_per_page - 1) // transactions_per_page
    if total_pages == 0:
        total_pages = 1
    if page < 1 or page > total_pages:
        bot.send_message(chat_id, text="❌ Invalid page number.")
        logger.warning(f"Invalid page number requested: {page}")
        return

    start_index = (page - 1) * transactions_per_page
    end_index = start_index + transactions_per_page
    page_transactions = sorted_payments[start_index:end_index]
    if not page_transactions:
        bot.send_message(chat_id, text="❌ No transactions found on this page.")
        logger.info(f"No transactions found on page {page}.")
        return

    message_lines = [f"📜 *Latest Transactions (Main Wallet) - Page {page}/{total_pages}* 📜\n"]
    for payment in page_transactions:
        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo provided."))
        time_str = payment.get("time", None)
        date = parse_time(time_str)
        formatted_date = date.strftime("%b %d, %Y %H:%M")
        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0
            logger.warning(f"Invalid amount_msat value in transaction: {amount_msat}")
        sign = "+" if amount_msat > 0 else "-"
        emoji = "🟢" if amount_msat > 0 else "🔴"
        message_lines.append(f"{emoji} {formatted_date} {sign}{amount_sats} sats")
        message_lines.append(f"✉️ Memo: {memo}")

    full_message = "\n".join(message_lines)
    inline_keyboard = []
    if total_pages > 1:
        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'prev_{page}'))
        if page < total_pages:
            buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f'next_{page}'))
        inline_keyboard.append(buttons)

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard) if inline_keyboard else None

    try:
        if message_id:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=full_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=inline_reply_markup
            )
            logger.info(f"Transactions page {page} edited for chat_id: {chat_id}")
        else:
            bot.send_message(
                chat_id=chat_id,
                text=full_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=inline_reply_markup
            )
            logger.info(f"Transactions page {page} sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending/editing transactions: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_prev_page(update, context):
    query = update.callback_query
    if not query:
        logger.warning("Callback query not found for previous page.")
        return
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    data = query.data
    match = re.match(r'prev_(\d+)', data)
    if match:
        current_page = int(match.group(1))
        new_page = current_page - 1
        if new_page < 1:
            new_page = 1
        send_transactions_message(chat_id, page=new_page, message_id=message_id)
        logger.debug(f"Navigating to previous page: {new_page}")
    query.answer()

def handle_next_page(update, context):
    query = update.callback_query
    if not query:
        logger.warning("Callback query not found for next page.")
        return
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    data = query.data
    match = re.match(r'next_(\d+)', data)
    if match:
        current_page = int(match.group(1))
        new_page = current_page + 1
        send_transactions_message(chat_id, page=new_page, message_id=message_id)
        logger.debug(f"Navigating to next page: {new_page}")
    query.answer()

def handle_balance_callback(query):
    try:
        chat_id = query.message.chat.id
        send_balance_message(chat_id)
        logger.debug("Handled balance callback.")
    except Exception as e:
        logger.error(f"Error handling balance callback: {e}")
        logger.debug(traceback.format_exc())

def handle_transactions_inline_callback(query):
    try:
        chat_id = query.message.chat.id
        send_transactions_message(chat_id, page=1, message_id=query.message.message_id)
        logger.debug("Handled transactions_inline callback.")
    except Exception as e:
        logger.error(f"Error handling transactions_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_donations_inline_callback(query):
    data = query.data
    try:
        if data == 'overwatch_inline' and OVERWATCH_URL:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="🔗 *Overwatch Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open Overwatch", url=OVERWATCH_URL)]
                ])
            )
            logger.debug("Handled overwatch_inline callback.")
        elif data == 'liveticker_inline' and DONATIONS_URL:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="🔗 *Live Ticker Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open Live Ticker", url=DONATIONS_URL)]
                ])
            )
            logger.debug("Handled liveticker_inline callback.")
        elif data == 'lnbits_inline' and LNBITS_URL:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="🔗 *LNBits Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open LNBits", url=LNBITS_URL)]
                ])
            )
            logger.debug("Handled lnbits_inline callback.")
        else:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="❌ No URL configured."
            )
            logger.warning("No URL configured for the callback data received.")
    except Exception as e:
        logger.error(f"Error handling donations_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_other_inline_callbacks(data, query):
    bot.answer_callback_query(callback_query_id=query.id, text="❓ Unknown action.")
    logger.warning(f"Unknown callback data received: {data}")

def handle_transactions_callback(update, context):
    query = update.callback_query
    data = query.data
    logger.debug(f"Handling callback data: {data}")

    if data == 'balance':
        handle_balance_callback(query)
    elif data == 'transactions_inline':
        handle_transactions_inline_callback(query)
    elif data.startswith('prev_'):
        handle_prev_page(update, context)
    elif data.startswith('next_'):
        handle_next_page(update, context)
    elif data in ['overwatch_inline', 'liveticker_inline', 'lnbits_inline']:
        handle_donations_inline_callback(query)
    else:
        handle_other_inline_callbacks(data, query)
        logger.warning(f"Unhandled callback data: {data}")

    query.answer()

def handle_info_command(update, context):
    chat_id = update.effective_chat.id
    logger.info(f"Handling /info command for chat_id: {chat_id}")
    interval_info = (
        f"🔔 *Balance Change Threshold:* {BALANCE_CHANGE_THRESHOLD} sats\n"
        f"🔔 *Highlight Threshold:* {HIGHLIGHT_THRESHOLD} sats\n"
        f"🔄 *Latest Payments Fetch Interval:* Every {PAYMENTS_FETCH_INTERVAL} seconds"
    )

    info_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Information*\n\n"
        f"Here are some current settings:\n\n"
        f"{interval_info}\n\n"
        f"These settings affect how I notify you and highlight incoming payments."
    )

    try:
        bot.send_message(
            chat_id=chat_id,
            text=info_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Info message sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending /info message: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_help_command(update, context):
    chat_id = update.effective_chat.id
    logger.info(f"Handling /help command for chat_id: {chat_id}")
    help_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Help*\n\n"
        "Hello! Here is what I can do for you:\n\n"
        "- /balance - Show your current LNbits wallet balance (main wallet).\n"
        "- /transactions - Show your latest transactions with pagination (main wallet).\n"
        "- /info - Display current settings and thresholds.\n"
        "- /help - Display this help message.\n"
        "- /ticker_ban words - Add forbidden words that will be censored in the Live Ticker.\n\n"
        "You can also use the buttons below to quickly navigate through features!\n\n"
        "_Note: If MULTI_LNBITS_API_KEYS is set, you will also receive notifications for additional wallets._"
    )

    try:
        bot.send_message(
            chat_id=chat_id,
            text=help_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Help message sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending /help message: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_balance(update, context):
    chat_id = update.effective_chat.id
    logger.debug(f"Handling /balance request for chat_id: {chat_id}")
    send_balance_message(chat_id)

def handle_latest_transactions(update, context):
    chat_id = update.effective_chat.id
    logger.debug(f"Handling /transactions request for chat_id: {chat_id}")
    send_transactions_message(chat_id, page=1)

def handle_live_ticker(update, context):
    chat_id = update.effective_chat.id
    if DONATIONS_URL:
        try:
            bot.send_message(
                chat_id=chat_id,
                text="🔗 *Live Ticker Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open Live Ticker", url=DONATIONS_URL)]
                ])
            )
            logger.info(f"Live Ticker message sent to chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error sending Live Ticker message: {e}")
            logger.debug(traceback.format_exc())
    else:
        bot.send_message(chat_id=chat_id, text="❌ Live Ticker URL not configured.")
        logger.warning("Live Ticker URL not configured.")

def handle_overwatch(update, context):
    chat_id = update.effective_chat.id
    if OVERWATCH_URL:
        try:
            bot.send_message(
                chat_id=chat_id,
                text="🔗 *Overwatch Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open Overwatch", url=OVERWATCH_URL)]
                ])
            )
            logger.info(f"Overwatch message sent to chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error sending Overwatch message: {e}")
            logger.debug(traceback.format_exc())
    else:
        bot.send_message(chat_id=chat_id, text="❌ Overwatch URL not configured.")
        logger.warning("Overwatch URL not configured.")

def handle_lnbits(update, context):
    chat_id = update.effective_chat.id
    if LNBITS_URL:
        try:
            bot.send_message(
                chat_id=chat_id,
                text="🔗 *LNBits Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open LNBits", url=LNBITS_URL)]
                ])
            )
            logger.info(f"LNBits message sent to chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error sending LNBits message: {e}")
            logger.debug(traceback.format_exc())
    else:
        bot.send_message(chat_id=chat_id, text="❌ LNBits URL not configured.")
        logger.warning("LNBits URL not configured.")

def process_update(update):
    try:
        if 'message' in update:
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()
            logger.debug(f"Received message from chat_id {chat_id}: {text}")

            if text == "💰 Balance":
                handle_balance(None, None)
            elif text == "📜 Latest Transactions":
                handle_latest_transactions(None, None)
            elif text == "📡 Live Ticker":
                handle_live_ticker(None, None)
            elif text == "📊 Overwatch":
                handle_overwatch(None, None)
            elif text == "⚡ LNBits":
                handle_lnbits(None, None)
            else:
                bot.send_message(
                    chat_id=chat_id,
                    text="❓ I didn't recognize that command. Use /help to see what I can do."
                )
                logger.warning(f"Unknown message received from chat_id {chat_id}: {text}")
        elif 'callback_query' in update:
            logger.debug("Received callback_query in update.")
            pass
        else:
            logger.info("No message or callback in update.")
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Authentication & Settings ---------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'logged_in' in session:
        return redirect(url_for('settings'))

    if request.method == 'POST':
        password = request.form.get('password')
        ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

        if not ADMIN_PASSWORD:
            flash('Admin password not set. Please set ADMIN_PASSWORD in your .env file.', 'danger')
            return render_template('login.html')

        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('Successfully logged in!', 'success')
            logger.info("User logged in successfully.")
            return redirect(url_for('settings'))
        else:
            flash('Incorrect password. Please try again.', 'danger')
            logger.warning("User attempted to log in with incorrect password.")

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('logged_in', None)
    flash('Successfully logged out.', 'success')
    logger.info("User logged out successfully.")
    return redirect(url_for('login'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        env_vars = [
            'TELEGRAM_BOT_TOKEN',
            'CHAT_ID',
            'LNBITS_READONLY_API_KEY',
            'LNBITS_URL',
            'INSTANCE_NAME',
            'BALANCE_CHANGE_THRESHOLD',
            'LATEST_TRANSACTIONS_COUNT',
            'PAYMENTS_FETCH_INTERVAL',
            'OVERWATCH_URL',
            'DONATIONS_URL',
            'LNURLP_ID',
            'HIGHLIGHT_THRESHOLD',
            'INFORMATION_URL',
            'APP_HOST',
            'APP_PORT',
            'PROCESSED_PAYMENTS_FILE',
            'CURRENT_BALANCE_FILE',
            'DONATIONS_FILE',
            'FORBIDDEN_WORDS_FILE',
            'ADMIN_PASSWORD',
            'MULTI_LNBITS_API_KEYS'
        ]

        required_fields = [
            'TELEGRAM_BOT_TOKEN',
            'CHAT_ID',
            'LNBITS_READONLY_API_KEY',
            'LNBITS_URL',
            'APP_HOST',
            'APP_PORT',
            'PROCESSED_PAYMENTS_FILE',
            'CURRENT_BALANCE_FILE',
            'DONATIONS_FILE',
            'FORBIDDEN_WORDS_FILE'
        ]

        errors = []
        try:
            for var in required_fields:
                value = request.form.get(var)
                if not value or value.strip() == '':
                    errors.append(f"{var.replace('_', ' ').title()} is required.")

            if errors:
                for error in errors:
                    flash(error, 'danger')
                logger.warning(f"Settings update failed due to missing fields: {errors}")
                env_vars_current = {var: request.form.get(var, '') for var in env_vars}
                return render_template('settings.html', env_vars=env_vars_current)

            for var in env_vars:
                value = request.form.get(var)
                if value is not None:
                    set_key('.env', var, value)
                    os.environ[var] = value

            flash('Settings updated successfully.', 'success')
            logger.info("Settings updated via settings page.")
            return redirect(url_for('settings'))
        except Exception as e:
            flash(f'Error updating settings: {e}', 'danger')
            logger.error(f"Error updating settings: {e}")
            logger.debug("".join(traceback.format_exception(None, e, e.__traceback__)))

    env_vars_current = {
        'TELEGRAM_BOT_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'CHAT_ID': os.getenv('CHAT_ID', ''),
        'LNBITS_READONLY_API_KEY': os.getenv('LNBITS_READONLY_API_KEY', ''),
        'LNBITS_URL': os.getenv('LNBITS_URL', ''),
        'INSTANCE_NAME': os.getenv('INSTANCE_NAME', ''),
        'BALANCE_CHANGE_THRESHOLD': os.getenv('BALANCE_CHANGE_THRESHOLD', ''),
        'LATEST_TRANSACTIONS_COUNT': os.getenv('LATEST_TRANSACTIONS_COUNT', ''),
        'PAYMENTS_FETCH_INTERVAL': os.getenv('PAYMENTS_FETCH_INTERVAL', ''),
        'OVERWATCH_URL': os.getenv('OVERWATCH_URL', ''),
        'DONATIONS_URL': os.getenv('DONATIONS_URL', ''),
        'LNURLP_ID': os.getenv('LNURLP_ID', ''),
        'HIGHLIGHT_THRESHOLD': os.getenv('HIGHLIGHT_THRESHOLD', ''),
        'INFORMATION_URL': os.getenv('INFORMATION_URL', ''),
        'APP_HOST': os.getenv('APP_HOST', ''),
        'APP_PORT': os.getenv('APP_PORT', ''),
        'PROCESSED_PAYMENTS_FILE': os.getenv('PROCESSED_PAYMENTS_FILE', ''),
        'CURRENT_BALANCE_FILE': os.getenv('CURRENT_BALANCE_FILE', ''),
        'DONATIONS_FILE': os.getenv('DONATIONS_FILE', ''),
        'FORBIDDEN_WORDS_FILE': os.getenv('FORBIDDEN_WORDS_FILE', ''),
        'ADMIN_PASSWORD': os.getenv('ADMIN_PASSWORD', ''),
        'MULTI_LNBITS_API_KEYS': os.getenv('MULTI_LNBITS_API_KEYS', '')
    }

    return render_template('settings.html', env_vars=env_vars_current)

def handle_vote_command(donation_id, vote_type):
    try:
        for donation in donations:
            if donation.get("id") == donation_id:
                if vote_type == 'like':
                    donation["likes"] += 1
                elif vote_type == 'dislike':
                    donation["dislikes"] += 1
                else:
                    logger.warning(f"Invalid vote_type received: {vote_type}")
                    return {"error": "Invalid vote type."}, 400
                save_donations()
                logger.info(f"Donation {donation_id} voted: {vote_type}. Total likes: {donation['likes']}, dislikes: {donation['dislikes']}")
                return {"success": True, "likes": donation["likes"], "dislikes": donation["dislikes"]}, 200
        logger.warning(f"Donation {donation_id} not found.")
        return {"error": "Donation not found."}, 404
    except Exception as e:
        logger.error(f"Error handling vote: {e}")
        logger.debug(traceback.format_exc())
        return {"error": "Internal server error."}, 500

# --------------------- Flask Routes ---------------------

@app.route('/')
def home():
    logger.debug("Home route accessed.")
    return "🔍 LNbits Multi-Wallet Monitor is running."

@app.route('/status', methods=['GET'])
def status_route():
    donation_details = fetch_donation_details()
    logger.debug("Status route accessed.")
    return jsonify({
        "latest_balance": latest_balance,
        "latest_payments": latest_payments,
        "total_donations": donation_details["total_donations"],
        "donations": donation_details["donations"],
        "lightning_address": donation_details["lightning_address"],
        "lnurl": donation_details["lnurl"],
        "highlight_threshold": HIGHLIGHT_THRESHOLD
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update:
        logger.warning("Empty update received in webhook.")
        return "No update", 400

    logger.debug(f"Update received in webhook: {update}")
    threading.Thread(target=process_update, args=(update,)).start()
    return "OK", 200

@app.route('/donations')
def donations_page():
    if not DONATIONS_URL or not LNURLP_ID:
        logger.warning("Donations not enabled or LNURLP_ID not set.")
        return "Donations not enabled.", 404
    lnurlp_id = LNURLP_ID
    lnurlp_info = get_lnurlp_info(lnurlp_id)
    if lnurlp_info is None:
        logger.error("Error fetching LNURLP info in donations_page.")
        return "Error fetching LNURLP info", 500

    wallet_name = lnurlp_info.get('description', 'Unknown Wallet')
    lightning_address = lnurlp_info.get('lightning_address', 'Unknown Lightning Address')
    lnurl = lnurlp_info.get('lnurl', '')

    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(lnurl)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        img_base64 = base64.b64encode(img_io.getvalue()).decode()
        logger.debug("QR code generated successfully.")
    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        logger.debug(traceback.format_exc())
        return "Error generating QR code.", 500

    total_donations_current = sum(donation['amount'] for donation in donations)

    return render_template(
        'donations.html',
        wallet_name=wallet_name,
        lightning_address=lightning_address,
        lnurl=lnurl,
        qr_code_data=img_base64,
        donations_url=DONATIONS_URL,
        information_url=INFORMATION_URL,
        total_donations=total_donations_current,
        donations=donations,
        highlight_threshold=HIGHLIGHT_THRESHOLD
    )

@app.route('/api/donations', methods=['GET'])
def get_donations_data():
    if not DONATIONS_URL or not LNURLP_ID:
        logger.warning("Donations not enabled.")
        return jsonify({"error": "Donations not enabled."}), 404
    try:
        donation_details = fetch_donation_details()
        data = {
            "total_donations": donation_details["total_donations"],
            "donations": donation_details["donations"],
            "lightning_address": donation_details["lightning_address"],
            "lnurl": donation_details["lnurl"],
            "highlight_threshold": HIGHLIGHT_THRESHOLD
        }
        logger.debug("Donations data fetched successfully via API.")
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error fetching donation data: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching donation data"}), 500

@app.route('/api/vote', methods=['POST'])
def vote_donation():
    try:
        data = request.get_json()
        donation_id = data.get('donation_id')
        vote_type = data.get('vote_type')

        if not donation_id or not vote_type:
            logger.warning("vote_donation called without donation_id or vote_type.")
            return jsonify({"error": "donation_id and vote_type required."}), 400
        if vote_type not in ['like', 'dislike']:
            logger.warning(f"Invalid vote_type received: {vote_type}")
            return jsonify({"error": "vote_type must be 'like' or 'dislike'."}), 400

        voted_donations = request.cookies.get('voted_donations', '')
        voted_set = set(voted_donations.split(',')) if voted_donations else set()
        if donation_id in voted_set:
            logger.info(f"Donation {donation_id} already voted by user.")
            return jsonify({"error": "Already voted on this donation."}), 403

        result, status_code = handle_vote_command(donation_id, vote_type)
        if status_code != 200:
            logger.warning(f"Vote command failed for donation_id {donation_id}: {result}")
            return jsonify(result), status_code

        response = make_response(jsonify(result), 200)
        voted_set.add(donation_id)
        new_voted_donations = ','.join(voted_set)
        response.set_cookie('voted_donations', new_voted_donations, max_age=60*60*24*365)
        logger.info(f"User voted on donation {donation_id}: {vote_type}")
        return response
    except Exception as e:
        logger.error(f"Error processing vote: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Internal server error."}), 500

@app.route('/donations_updates', methods=['GET'])
def donations_updates():
    global last_update
    if not DONATIONS_URL or not LNURLP_ID:
        logger.warning("Donations not enabled.")
        return jsonify({"error": "Donations not enabled."}), 404
    try:
        logger.debug("Fetching last_update timestamp.")
        return jsonify({"last_update": last_update.isoformat()}), 200
    except Exception as e:
        logger.error(f"Error fetching last_update: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching last_update"}), 500

@app.route('/cinema')
def cinema_page():
    if not DONATIONS_URL or not LNURLP_ID:
        logger.warning("Donations are not enabled for Cinema Mode.")
        return "Donations are not enabled for Cinema Mode.", 404
    logger.debug("Cinema page accessed.")
    return render_template('cinema.html')

# --------------------- Initialization & Scheduler ---------------------

def initialize_processed_payments():
    """
    Mark all existing payments from the MAIN wallet as processed
    so we don't spam old transactions. 
    For multiwallet, we only do this for the main wallet on startup.
    The additional wallets can also be similarly 'initialized' if desired.
    """
    logger.info("Initializing processed payments (main wallet) to prevent old notifications.")
    payments = fetch_api("payments", custom_api_key=LNBITS_READONLY_API_KEY)
    if payments is None or not isinstance(payments, list):
        logger.error("Failed to initialize processed payments: Unable to fetch main wallet payments.")
        return

    for payment in payments:
        payment_hash = payment.get("payment_hash")
        if payment_hash:
            hash_with_key = f"MAIN_{payment_hash}"
            if hash_with_key not in processed_payments:
                processed_payments.add(hash_with_key)
                add_processed_payment(hash_with_key)
                logger.debug(f"Payment {hash_with_key} marked as processed during initialization.")
    logger.info("Initialization of processed payments for main wallet completed.")

def start_scheduler():
    scheduler = BackgroundScheduler(timezone='UTC')

    # 1) Periodic job to fetch main wallet payments + multiwallet payments
    if PAYMENTS_FETCH_INTERVAL > 0:
        scheduler.add_job(
            func=lambda: [
                send_latest_payments_singlewallet(),
                send_latest_payments_multiwallet()  # fetch from all other wallets
            ],
            trigger='interval',
            seconds=PAYMENTS_FETCH_INTERVAL,
            id='all_payments_fetch',
            next_run_time=datetime.utcnow() + timedelta(seconds=2)
        )
        logger.info(f"Payments fetch scheduled every {PAYMENTS_FETCH_INTERVAL} seconds.")
    else:
        logger.info("Payments fetch disabled (PAYMENTS_FETCH_INTERVAL=0).")

    # 2) Periodic job to check each wallet's balance
    #    Adjust the interval as desired (e.g., 120 seconds).
    scheduler.add_job(
        check_multi_wallet_balances,
        'interval',
        seconds=60,
        id='multi_wallet_balance_check',
        next_run_time=datetime.utcnow() + timedelta(seconds=3)
    )
    logger.info("Multi-wallet balance check scheduled every 60 seconds.")

    scheduler.start()
    logger.info("Scheduler started.")

def send_main_inline_keyboard():
    inline_reply_markup = get_main_inline_keyboard()
    try:
        welcome_message = (
            "😶‍🌫️ Here we go!\n\n"
            "I'm ready to assist you with monitoring your LNbits transactions.\n\n"
            "Use the buttons below to explore the features."
        )
        bot.send_message(
            chat_id=CHAT_ID,
            text=welcome_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_reply_markup
        )
        logger.info("Main inline keyboard successfully sent.")
    except Exception as telegram_error:
        logger.error(f"Error sending the main inline keyboard: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_start_message(update, context):
    chat_id = update.effective_chat.id
    welcome_message = (
        "👋 Welcome to your LNbits Multi-Wallet Monitor!\n\n"
        "Use the buttons below for quick access to various features."
    )
    reply_markup = get_main_keyboard()
    
    try:
        bot.send_message(
            chat_id=chat_id,
            text=welcome_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Start message sent to chat_id {chat_id}.")
    except Exception as e:
        logger.error(f"Error sending the start message: {e}")
        logger.debug(traceback.format_exc())

def main():
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    logger.debug("Flask app thread started.")

    # Initialize processed payments for the main wallet
    initialize_processed_payments()

    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()
    logger.debug("Scheduler thread started.")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('balance', lambda update, context: send_balance_message(update.effective_chat.id)))
    dispatcher.add_handler(CommandHandler('transactions', lambda update, context: send_transactions_message(update.effective_chat.id, page=1)))
    dispatcher.add_handler(CommandHandler('info', handle_info_command))
    dispatcher.add_handler(CommandHandler('help', handle_help_command))
    dispatcher.add_handler(CommandHandler('start', send_start_message))
    dispatcher.add_handler(CommandHandler('ticker_ban', handle_ticker_ban, pass_args=True))

    dispatcher.add_handler(CallbackQueryHandler(
        handle_transactions_callback,
        pattern='^(balance|transactions_inline|prev_\\d+|next_\\d+|overwatch_inline|liveticker_inline|lnbits_inline)$'
    ))

    dispatcher.add_handler(MessageHandler(Filters.regex('^💰 Balance$'), handle_balance))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📜 Latest Transactions$'), handle_latest_transactions))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📡 Live Ticker$'), handle_live_ticker))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📊 Overwatch$'), handle_overwatch))
    dispatcher.add_handler(MessageHandler(Filters.regex('^⚡ LNBits$'), handle_lnbits))

    updater.start_polling()
    logger.info("Telegram Bot started.")
    send_main_inline_keyboard()
    updater.idle()

def run_flask_app():
    try:
        logger.info(f"Starting Flask app on {APP_HOST}:{APP_PORT}")
        app.run(host=APP_HOST, port=APP_PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error running Flask app: {e}")
        logger.debug(traceback.format_exc())

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Multi-Wallet Monitor.")
    logger.info(f"🔔 Balance Change Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"🔔 Highlight Threshold: {HIGHLIGHT_THRESHOLD} sats")
    logger.info(f"📊 Fetching the latest {LATEST_TRANSACTIONS_COUNT} transactions.")
    if PAYMENTS_FETCH_INTERVAL > 0:
        logger.info(f"⏲️ Interval: every {PAYMENTS_FETCH_INTERVAL} seconds")
    else:
        logger.info("⏲️ Fetch Interval disabled")

    load_donations()
    main()
