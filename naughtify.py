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

# These environment variables are mandatory:
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

# Optional: Additional LNbits read-only API keys for multi-wallet
MULTI_LNBITS_API_KEYS = os.getenv("MULTI_LNBITS_API_KEYS", "").strip()

# Optional: Overwatch / Donations / Info
OVERWATCH_URL = os.getenv("OVERWATCH_URL")
DONATIONS_URL = os.getenv("DONATIONS_URL")
INFORMATION_URL = os.getenv("INFORMATION_URL")

# LNURLP ID (Required if Donations are enabled)
LNURLP_ID = os.getenv("LNURLP_ID") if DONATIONS_URL else None

# File settings
FORBIDDEN_WORDS_FILE = os.getenv("FORBIDDEN_WORDS_FILE", "forbidden_words.txt")
PROCESSED_PAYMENTS_FILE = os.getenv("PROCESSED_PAYMENTS_FILE", "processed_payments.txt")
CURRENT_BALANCE_FILE = os.getenv("CURRENT_BALANCE_FILE", "current-balance.txt")
DONATIONS_FILE = os.getenv("DONATIONS_FILE", "donations.json")

# Thresholds, intervals, and other settings
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "1"))
HIGHLIGHT_THRESHOLD = int(os.getenv("HIGHLIGHT_THRESHOLD", "2100"))
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "60"))

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "5009"))

# Flask secret key
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24))

# Check mandatory environment variables
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL
}
missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise EnvironmentError(f"Essential environment variables missing: {', '.join(missing_vars)}")

# If donations are enabled, LNURLP_ID must be set
if DONATIONS_URL and not LNURLP_ID:
    raise EnvironmentError("LNURLP_ID must be set when DONATIONS_URL is provided.")

# Parse LNbits domain for convenience
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

# We'll track the "latest_balance" for the MAIN wallet (for /status)
latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

# We'll store the last known transactions from the main wallet
latest_payments = []

# --------------------- Helper Functions ---------------------

def load_forbidden_words(file_path):
    """
    Loads forbidden words from a text file, one word per line.
    """
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
    """
    Replaces any forbidden word inside the 'memo' with asterisks. 
    """
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
    """
    Loads processed payment hashes from file so we don't resend notifications for old payments.
    """
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
    Appends the processed payment hash to the file so it won't be notified again.
    For multi-wallet usage, we store something like "walletName_paymentHash".
    """
    try:
        with open(PROCESSED_PAYMENTS_FILE, 'a') as f:
            f.write(f"{payment_hash_with_key}\n")
        logger.debug(f"Payment {payment_hash_with_key} added to processed list.")
    except Exception as e:
        logger.error(f"Error adding processed payment: {e}")
        logger.debug(traceback.format_exc())

def load_donations():
    """
    Loads the donation data from the JSON file if donations are enabled.
    """
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
    """
    Saves the current state of donations (if donations are enabled).
    """
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
    """
    Goes through each donation and sanitizes the memo field. 
    """
    global donations
    try:
        for donation in donations:
            donation['memo'] = sanitize_memo(donation.get('memo', ''))
        save_donations()
        logger.info("Donations sanitized and saved.")
    except Exception as e:
        logger.error(f"Error sanitizing donations: {e}")
        logger.debug(traceback.format_exc())

def fetch_api(endpoint, custom_api_key=None):
    """
    Generic function to fetch an LNbits API endpoint.
    If 'custom_api_key' is given, that key is used. Otherwise, use the main LNbits read-only key.
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

def parse_time(time_input):
    """
    Attempts to parse the LNbits date/time field into a Python datetime.
    Falls back to 'now' if it cannot parse.
    """
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

def handle_ticker_ban(update, context):
    """
    /ticker_ban <words>
    Bans the given words from donation memos.
    """
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

# --------------------- Donation-related / LNURLp fetchers ---------------------

def fetch_pay_links():
    """
    Fetch LNURLp links if donations are enabled.
    """
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
    """
    Finds a specific LNURLp link by ID.
    """
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
    """
    Returns donation-related info, including LNURL or lightning_address.
    """
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
    """
    Merges the LNURL / lightning address info into the donation object.
    """
    donation_details = fetch_donation_details()
    data.update({
        "lightning_address": donation_details.get("lightning_address"),
        "lnurl": donation_details.get("lnurl")
    })
    logger.debug("Donation details updated with additional information.")
    return data

def updateDonations(data):
    """
    Called whenever we detect a new donation. Saves the donation file.
    """
    updated_data = update_donations_with_details(data)
    if updated_data["donations"]:
        latestDonation = updated_data["donations"][-1]
        logger.info(f'Latest donation: {latestDonation["amount"]} sats - "{latestDonation["memo"]}"')
    else:
        logger.info('Latest donation: None yet.')
    save_donations()

# --------------------- Telegram Notification Helpers ---------------------

def notify_transaction(payment, direction, wallet_name=""):
    """
    Sends a Telegram message about an incoming or outgoing transaction.
    Includes an optional wallet_name to distinguish multiple wallets.
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

# --------------------- MAIN Wallet Payment Checker ---------------------

def send_latest_payments_singlewallet():
    """
    Fetches the most recent transactions from the MAIN wallet (LNBITS_READONLY_API_KEY)
    to detect incoming/outgoing payments. Also handles donation logic for LNURLp.
    """
    global total_donations, donations, last_update, latest_balance, latest_payments
    logger.info("Fetching latest payments for the main (default) wallet...")

    payments = fetch_api("payments", custom_api_key=LNBITS_READONLY_API_KEY)
    if payments is None or not isinstance(payments, list):
        logger.warning("No payments fetched (or invalid format) for main wallet.")
        return

    # Sort descending by time
    sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
    latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]
    latest_payments = latest.copy()  # For /status route

    if not latest:
        logger.info("No payments found for main wallet.")
        return

    incoming_payments = []
    outgoing_payments = []

    for payment in latest:
        payment_hash = payment.get("payment_hash")
        # Tag with "MAIN_" so we don't mix them with multi-wallet payments
        payment_hash_with_key = f"MAIN_{payment_hash}"

        if payment_hash_with_key in processed_payments:
            logger.debug(f"Payment {payment_hash_with_key} already processed. Skipping.")
            continue

        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo provided."))
        status = payment.get("status", "completed")
        time_str = payment.get("time", None)
        date_obj = parse_time(time_str)
        formatted_date = date_obj.isoformat()

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0
            logger.warning(f"Invalid amount_msat value: {amount_msat}")

        if status.lower() == "pending":
            logger.debug(f"Payment {payment_hash_with_key} is pending. Skipping.")
            continue

        # Determine direction
        if amount_msat > 0:
            incoming_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})
        elif amount_msat < 0:
            outgoing_payments.append({"amount": amount_sats, "memo": memo, "date": formatted_date})

        # Donations logic (only on the main wallet)
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

    # Update the main wallet's balance in global state
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

    # Telegram notifications for new incoming/outgoing (main wallet)
    for inc in incoming_payments:
        notify_transaction(inc, "incoming", wallet_name="MainWallet")
    for out in outgoing_payments:
        notify_transaction(out, "outgoing", wallet_name="MainWallet")

# --------------------- MULTI-Wallet Payment Checker ---------------------

def send_latest_payments_multiwallet():
    """
    For each LNbits API key in MULTI_LNBITS_API_KEYS, fetch recent payments 
    and send Telegram notifications for new incoming/outgoing transactions.
    """
    if not MULTI_LNBITS_API_KEYS:
        logger.debug("MULTI_LNBITS_API_KEYS is empty; skipping multiwallet payments.")
        return

    mw_keys = [k.strip() for k in MULTI_LNBITS_API_KEYS.split(",") if k.strip()]
    if not mw_keys:
        logger.debug("No valid multiwallet keys found. Skipping.")
        return

    for key in mw_keys:
        # Attempt to fetch the wallet's name for better logs
        w_info = fetch_api("wallet", custom_api_key=key)
        wallet_name = w_info.get("name", "UnknownWallet") if w_info else "UnknownWallet"

        logger.info(f"Fetching latest payments for wallet '{wallet_name}'...")

        payments = fetch_api("payments", custom_api_key=key)
        if payments is None or not isinstance(payments, list):
            logger.warning(f"No payments or invalid data for wallet '{wallet_name}'.")
            continue

        # Sort descending
        sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
        relevant = sorted_payments[:LATEST_TRANSACTIONS_COUNT]

        if not relevant:
            logger.info(f"No recent payments found for wallet '{wallet_name}'.")
            continue

        incoming_payments = []
        outgoing_payments = []

        for payment in relevant:
            p_hash = payment.get("payment_hash")
            # Use walletName as an identifier
            payment_hash_with_key = f"{wallet_name}_{p_hash}"

            if payment_hash_with_key in processed_payments:
                logger.debug(f"Payment {payment_hash_with_key} already processed. Skipping.")
                continue

            amount_msat = payment.get("amount", 0)
            memo = sanitize_memo(payment.get("memo", "No memo provided."))
            status = payment.get("status", "completed")
            t_str = payment.get("time", None)
            dt_obj = parse_time(t_str)
            f_date = dt_obj.isoformat()

            try:
                amount_sats = int(abs(amount_msat) / 1000)
            except ValueError:
                amount_sats = 0
                logger.warning(f"Invalid amount_msat: {amount_msat} for '{wallet_name}'")

            if status.lower() == "pending":
                logger.debug(f"Payment {payment_hash_with_key} is pending. Skipping.")
                continue

            if amount_msat > 0:
                incoming_payments.append({"amount": amount_sats, "memo": memo, "date": f_date})
            elif amount_msat < 0:
                outgoing_payments.append({"amount": amount_sats, "memo": memo, "date": f_date})

            processed_payments.add(payment_hash_with_key)
            add_processed_payment(payment_hash_with_key)
            logger.debug(f"Payment {payment_hash_with_key} processed for '{wallet_name}'.")

        # Notify Telegram
        for inc in incoming_payments:
            notify_transaction(inc, "incoming", wallet_name=wallet_name)
        for out in outgoing_payments:
            notify_transaction(out, "outgoing", wallet_name=wallet_name)

# --------------------- MULTI-Wallet Balance Checker ---------------------

def check_multi_wallet_balances():
    """
    Periodically checks the balance of both the main wallet and the multi-wallets, 
    notifying Telegram if the balance changed since last time.
    """
    # 1) Check the main wallet
    try:
        logger.debug("Checking main wallet balance for changes...")
        main_info = fetch_api("wallet", custom_api_key=LNBITS_READONLY_API_KEY)
        if main_info:
            c_balance_msat = main_info.get("balance", 0)
            name = main_info.get("name", "MainWallet")

            tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            main_file_path = os.path.join(tmp_dir, f"lnbits_wallet_{name.replace(' ', '_')}.txt")

            if not os.path.exists(main_file_path):
                last_balance_msat = 0
                with open(main_file_path, 'w') as f:
                    f.write(str(c_balance_msat))
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

            if c_balance_msat != last_balance_msat:
                diff = c_balance_msat - last_balance_msat
                with open(main_file_path, 'w') as f:
                    f.write(str(c_balance_msat))

                diff_sat = f"{diff / 1000:,.3f}"
                old_sat = f"{last_balance_msat / 1000:,.3f}" if last_balance_msat != 0 else "0"
                new_sat = f"{c_balance_msat / 1000:,.3f}"

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

    # 2) Check additional multi-wallets
    if not MULTI_LNBITS_API_KEYS:
        logger.debug("MULTI_LNBITS_API_KEYS is empty; skipping additional balances.")
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
            w_name = w_info.get("name", "UnknownWallet")

            tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            file_path = os.path.join(tmp_dir, f"lnbits_wallet_{w_name.replace(' ', '_')}.txt")

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
                diff = balance_msat - last_balance_msat
                with open(file_path, 'w') as f:
                    f.write(str(balance_msat))

                diff_sat = f"{diff / 1000:,.3f}"
                old_sat = f"{last_balance_msat / 1000:,.3f}" if last_balance_msat != 0 else "0"
                new_sat = f"{balance_msat / 1000:,.3f}"

                message = (
                    f"₿💰₿ ➽ Yippee, wallet *{w_name}* changed!\n"
                    f"Balance shifted by {diff_sat} sats – from {old_sat} to {new_sat}."
                )
                bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Balance change for wallet '{w_name}' was notified.")
            else:
                logger.debug(f"No balance change for wallet '{w_name}'.")
        except Exception as e:
            logger.error(f"Error checking balance for multi wallet key {key[:5]}: {e}")
            logger.debug(traceback.format_exc())

# --------------------- Telegram Bot UI: Keyboards & Callbacks ---------------------

def get_main_inline_keyboard():
    """
    The main inline keyboard shown to the user, including multi-wallet additions.
    """
    balance_button = InlineKeyboardButton("💰 Balance", callback_data='balance')
    transactions_button = InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline')

    # Overwatch, Live Ticker, LNbits
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

    # NEW: Buttons for multi-wallet actions
    mw_balance_button = InlineKeyboardButton("🔀 MW Balance", callback_data='multiwallet_balance')
    mw_transactions_button = InlineKeyboardButton("🔀 MW Transactions", callback_data='multiwallet_transactions')

    inline_keyboard = [
        [balance_button],
        [transactions_button, live_ticker_button],
        [overwatch_button, lnbits_button],
        # Below row has the new multi-wallet buttons
        [mw_balance_button, mw_transactions_button]
    ]
    logger.debug("Main inline keyboard created with multi-wallet buttons.")
    return InlineKeyboardMarkup(inline_keyboard)

def get_main_keyboard():
    """
    Reply keyboard with a few textual buttons. (User sees them in Telegram.)
    """
    balance_button = ["💰 Balance"]
    row1 = ["📊 Overwatch", "📡 Live Ticker"]
    row2 = ["📜 Latest Transactions", "⚡ LNBits"]

    keyboard = [
        balance_button,
        row1,
        row2
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# 1) Show a list of all multi-wallets after user clicks "🔀 MW Balance" or "🔀 MW Transactions"
def show_multiwallet_list(query, action):
    """
    Presents the user with an inline keyboard listing all multi-wallets by name.
    'action' can be 'balance' or 'transactions' to decide the callback approach.
    """
    chat_id = query.message.chat.id

    mw_keys_str = MULTI_LNBITS_API_KEYS
    if not mw_keys_str:
        bot.send_message(chat_id, text="No multiwallet API keys configured.")
        return

    mw_keys = [k.strip() for k in mw_keys_str.split(",") if k.strip()]
    if not mw_keys:
        bot.send_message(chat_id, text="No valid multiwallet API keys found.")
        return

    # For each key, fetch the wallet name quickly
    buttons = []
    for key in mw_keys:
        w_info = fetch_api("wallet", custom_api_key=key)
        if w_info:
            wallet_name = w_info.get("name", "UnnamedWallet")
        else:
            wallet_name = "UnknownWallet"

        if action == 'balance':
            callback_data = f"mw_balance_{key}"
        else:
            callback_data = f"mw_tx_{key}"

        # Each row is a single button with the wallet's name
        buttons.append([InlineKeyboardButton(wallet_name, callback_data=callback_data)])

    reply_markup = InlineKeyboardMarkup(buttons)

    if action == 'balance':
        text_msg = "Select a multi-wallet to see its balance:"
    else:
        text_msg = "Select a multi-wallet to see its latest transactions:"

    try:
        bot.send_message(chat_id=chat_id, text=text_msg, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error showing multiwallet list: {e}")
        logger.debug(traceback.format_exc())

def show_balance_for_wallet(query, wallet_api_key):
    """
    Retrieves and shows the balance for a specific multi-wallet.
    """
    chat_id = query.message.chat.id
    w_info = fetch_api("wallet", custom_api_key=wallet_api_key)
    if not w_info:
        bot.send_message(chat_id, text="❌ Failed to fetch this wallet's info.")
        return

    name = w_info.get("name", "UnnamedWallet")
    balance_msat = w_info.get("balance", 0)
    balance_sats = balance_msat // 1000
    msg = f"💰 *Balance for {name}:* {balance_sats} sats"

    try:
        bot.send_message(chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error sending multiwallet balance: {e}")
        logger.debug(traceback.format_exc())

def show_transactions_for_wallet(query, wallet_api_key, page=1):
    """
    Retrieves and shows the latest transactions for a specific multi-wallet.
    For brevity, no advanced pagination is implemented here. 
    You could replicate your main wallet approach if you want multiple pages.
    """
    chat_id = query.message.chat.id
    payments = fetch_api("payments", custom_api_key=wallet_api_key)
    if payments is None or not isinstance(payments, list):
        bot.send_message(chat_id, text="❌ Unable to fetch transactions for this wallet.")
        return

    # Filter out pending
    filtered = [p for p in payments if p.get("status", "").lower() != "pending"]
    # Sort descending
    sorted_payments = sorted(filtered, key=lambda x: x.get("time", ""), reverse=True)

    # We'll show up to 13 transactions for example, just like the main approach
    transactions_per_page = 13
    total_transactions = len(sorted_payments)
    total_pages = (total_transactions + transactions_per_page - 1) // transactions_per_page
    if total_pages == 0:
        total_pages = 1
    if page < 1 or page > total_pages:
        bot.send_message(chat_id, text="❌ Invalid page number.")
        return

    start_idx = (page - 1) * transactions_per_page
    end_idx = start_idx + transactions_per_page
    page_transactions = sorted_payments[start_idx:end_idx]

    w_info = fetch_api("wallet", custom_api_key=wallet_api_key)
    wallet_name = w_info.get("name", "UnnamedWallet") if w_info else "UnknownWallet"

    lines = [f"📜 *Latest Transactions for {wallet_name} - Page {page}/{total_pages}*"]
    for pay in page_transactions:
        msat = pay.get("amount", 0)
        memo = sanitize_memo(pay.get("memo", "No memo"))
        dt = parse_time(pay.get("time", None))
        dt_str = dt.strftime("%b %d, %Y %H:%M")
        try:
            sats = abs(msat) // 1000
        except:
            sats = 0
        sign = "+" if msat > 0 else "-"
        emoji = "🟢" if msat > 0 else "🔴"
        lines.append(f"\n{emoji} {dt_str} {sign}{sats} sats")
        lines.append(f"✉️ Memo: {memo}")

    msg_full = "\n".join(lines)

    # For simplicity, no pagination callback is implemented. 
    # We either edit the same message or send a new one:
    try:
        # Attempt to edit the same message
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=query.message.message_id,
            text=msg_full,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        # If editing fails (e.g., message is older or can't be edited), just send a new one.
        bot.send_message(chat_id, text=msg_full, parse_mode=ParseMode.MARKDOWN)

# 2) The main callback query handler
def handle_transactions_callback(update, context):
    """
    This processes the user's taps on the inline keyboards.
    """
    query = update.callback_query
    data = query.data
    logger.debug(f"Handling callback data: {data}")

    if data == 'balance':
        # Show the main wallet balance
        handle_balance_callback(query)

    elif data == 'transactions_inline':
        # Show the main wallet transactions
        handle_transactions_inline_callback(query)

    elif data.startswith('prev_'):
        handle_prev_page(update, context)

    elif data.startswith('next_'):
        handle_next_page(update, context)

    elif data in ['overwatch_inline', 'liveticker_inline', 'lnbits_inline']:
        handle_donations_inline_callback(query)

    # NEW: handle multiwallet selection
    elif data == 'multiwallet_balance':
        # Step 1: Show the user a list of wallets to pick from
        show_multiwallet_list(query, action='balance')

    elif data == 'multiwallet_transactions':
        # Step 1: Show the user a list of wallets to pick from
        show_multiwallet_list(query, action='transactions')

    elif data.startswith("mw_balance_"):
        # Step 2: The user picked a specific wallet for balance
        key = data.replace("mw_balance_", "")
        show_balance_for_wallet(query, key)

    elif data.startswith("mw_tx_"):
        # Step 2: The user picked a specific wallet for transactions
        key = data.replace("mw_tx_", "")
        show_transactions_for_wallet(query, key, page=1)

    else:
        handle_other_inline_callbacks(data, query)
        logger.warning(f"Unhandled callback data: {data}")

    query.answer()

def handle_balance_callback(query):
    """
    Called when the user taps the '💰 Balance' button for the main wallet.
    """
    try:
        chat_id = query.message.chat.id
        send_balance_message(chat_id)
        logger.debug("Handled balance callback.")
    except Exception as e:
        logger.error(f"Error handling balance callback: {e}")
        logger.debug(traceback.format_exc())

def handle_transactions_inline_callback(query):
    """
    Called when the user taps the '📜 Latest Transactions' button for the main wallet.
    """
    try:
        chat_id = query.message.chat.id
        send_transactions_message(chat_id, page=1, message_id=query.message.message_id)
        logger.debug("Handled transactions_inline callback.")
    except Exception as e:
        logger.error(f"Error handling transactions_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_other_inline_callbacks(data, query):
    """
    Fallback for unknown callbacks.
    """
    bot.answer_callback_query(callback_query_id=query.id, text="❓ Unknown action.")
    logger.warning(f"Unknown callback data received: {data}")

# 3) The main wallet: show balance or transactions upon user commands
def send_balance_message(chat_id):
    """
    Sends the main wallet's balance to the user.
    """
    logger.info(f"Fetching MAIN wallet balance for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet", custom_api_key=LNBITS_READONLY_API_KEY)
    if wallet_info is None:
        bot.send_message(chat_id, text="❌ Unable to fetch balance at the moment. Please try again.")
        logger.error("Failed to fetch main wallet balance.")
        return
    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat // 1000
    msg = f"💰 *Current Balance (Main Wallet):* {current_balance_sats} sats"

    try:
        bot.send_message(chat_id, text=msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard())
        logger.info(f"Balance message (main wallet) sent to chat_id: {chat_id}")
    except Exception as e:
        logger.error(f"Error sending balance message: {e}")
        logger.debug(traceback.format_exc())

def send_transactions_message(chat_id, page=1, message_id=None):
    """
    Sends the main wallet's transactions (with pagination) to the user.
    """
    logger.info(f"Fetching transactions for MAIN wallet, page: {page}")
    payments = fetch_api("payments", custom_api_key=LNBITS_READONLY_API_KEY)
    if payments is None:
        bot.send_message(chat_id, text="❌ Unable to fetch transactions right now.")
        logger.error("Failed to fetch transactions for MAIN wallet.")
        return

    filtered = [p for p in payments if p.get("status", "").lower() != "pending"]
    sorted_pays = sorted(filtered, key=lambda x: x.get("time", ""), reverse=True)
    total_transactions = len(sorted_pays)
    transactions_per_page = 13
    total_pages = (total_transactions + transactions_per_page - 1) // transactions_per_page
    if total_pages == 0:
        total_pages = 1
    if page < 1 or page > total_pages:
        bot.send_message(chat_id, text="❌ Invalid page number.")
        logger.warning(f"Invalid page number requested: {page}")
        return

    start_i = (page - 1) * transactions_per_page
    end_i = start_i + transactions_per_page
    page_transactions = sorted_pays[start_i:end_i]
    if not page_transactions:
        bot.send_message(chat_id, text="❌ No transactions found on this page.")
        logger.info(f"No transactions found on page {page}.")
        return

    lines = [f"📜 *Latest Transactions (Main Wallet) - Page {page}/{total_pages}* 📜\n"]
    for pay in page_transactions:
        amount_msat = pay.get("amount", 0)
        memo = sanitize_memo(pay.get("memo", "No memo provided."))
        time_str = pay.get("time", None)
        dt_obj = parse_time(time_str)
        date_str = dt_obj.strftime("%b %d, %Y %H:%M")
        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0
            logger.warning(f"Invalid amount_msat value in transaction: {amount_msat}")
        sign = "+" if amount_msat > 0 else "-"
        emoji = "🟢" if amount_msat > 0 else "🔴"
        lines.append(f"{emoji} {date_str} {sign}{amount_sats} sats")
        lines.append(f"✉️ Memo: {memo}")

    full_message = "\n".join(lines)
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
    except Exception as e:
        logger.error(f"Error sending/editing transactions: {e}")
        logger.debug(traceback.format_exc())

def handle_prev_page(update, context):
    """
    Callback for '⬅️ Previous' in main wallet pagination.
    """
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
    """
    Callback for '➡️ Next' in main wallet pagination.
    """
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

# --------------------- Standard Bot Commands ---------------------

def handle_info_command(update, context):
    """
    /info command shows the user some current settings.
    """
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
    except Exception as e:
        logger.error(f"Error sending /info message: {e}")
        logger.debug(traceback.format_exc())

def handle_help_command(update, context):
    """
    /help command to show user what commands are available.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling /help command for chat_id: {chat_id}")
    help_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Help*\n\n"
        "Hello! Here is what I can do for you:\n\n"
        "- /balance - Show your current LNbits wallet balance (main wallet).\n"
        "- /transactions - Show your latest transactions with pagination (main wallet).\n"
        "- /info - Display current settings and thresholds.\n"
        "- /help - Display this help message.\n"
        "- /ticker_ban <words> - Add forbidden words that will be censored in the Live Ticker.\n\n"
        "For multiple wallets, you can now use the new Inline Keyboard buttons:\n"
        "🔀 MW Balance / 🔀 MW Transactions to see other wallets.\n\n"
        "You can also use the buttons below to quickly navigate through features!"
    )

    try:
        bot.send_message(
            chat_id=chat_id,
            text=help_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Help message sent to chat_id: {chat_id}")
    except Exception as e:
        logger.error(f"Error sending /help message: {e}")
        logger.debug(traceback.format_exc())

def handle_balance(update, context):
    """
    Called when user types /balance. Shows main wallet's balance.
    """
    chat_id = update.effective_chat.id
    logger.debug(f"Handling /balance request for chat_id: {chat_id}")
    send_balance_message(chat_id)

def handle_latest_transactions(update, context):
    """
    Called when user types /transactions. Shows main wallet's transactions.
    """
    chat_id = update.effective_chat.id
    logger.debug(f"Handling /transactions request for chat_id: {chat_id}")
    send_transactions_message(chat_id, page=1)

def handle_live_ticker(update, context):
    """
    For the textual '📡 Live Ticker' button in the reply keyboard. 
    """
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
    """
    For the textual '📊 Overwatch' button in the reply keyboard.
    """
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
    """
    For the textual '⚡ LNBits' button in the reply keyboard.
    """
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
    """
    This function is triggered by /webhook POST calls or by polling. 
    It looks for 'message' or 'callback_query' in 'update'.
    """
    try:
        if 'message' in update:
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()
            logger.debug(f"Received message from chat_id {chat_id}: {text}")

            # Handle known text (from the reply keyboard)
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
                # Unknown
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
        # List all environment variables we allow to be updated.
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
                env_vars_current = {v: request.form.get(v, '') for v in env_vars}
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

# --------------------- Voting for donations (frontend) ---------------------

def handle_vote_command(donation_id, vote_type):
    """
    Increase like or dislike count for a donation by donation_id.
    """
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
    """
    Returns some simple JSON with the main wallet's balance and the latest transactions, 
    plus donation info if enabled.
    """
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
    """
    If you set a webhook for the Telegram Bot, updates go here.
    We spin off a thread so we don't block returning "OK".
    """
    update = request.get_json()
    if not update:
        logger.warning("Empty update received in webhook.")
        return "No update", 400

    logger.debug(f"Update received in webhook: {update}")
    threading.Thread(target=process_update, args=(update,)).start()
    return "OK", 200

@app.route('/donations')
def donations_page():
    """
    Renders a donation page if donations are enabled. 
    """
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
    """
    Returns the list of donations as JSON for a web frontend (if enabled).
    """
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
    """
    Endpoint to cast a 'like' or 'dislike' for a donation, 
    sets a cookie so each user can only vote once.
    """
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
    """
    For a frontend to know if donations changed. 
    Returns last_update in JSON if donations are enabled.
    """
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
    """
    Renders a 'cinema mode' page if donations are enabled.
    """
    if not DONATIONS_URL or not LNURLP_ID:
        logger.warning("Donations are not enabled for Cinema Mode.")
        return "Donations are not enabled for Cinema Mode.", 404
    logger.debug("Cinema page accessed.")
    return render_template('cinema.html')

# --------------------- Initialization & Scheduler ---------------------

def initialize_processed_payments():
    """
    Marks all existing main-wallet payments as processed so we don't
    send old notifications on startup. 
    (You could do the same for multi-wallet keys if you want.)
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
    """
    Starts the APScheduler. Schedules:
      - main wallet + multiwallet transaction fetch 
      - multi-wallet balance checks
    """
    scheduler = BackgroundScheduler(timezone='UTC')

    # 1) Periodic job to fetch MAIN + multi-wallet payments
    if PAYMENTS_FETCH_INTERVAL > 0:
        scheduler.add_job(
            func=lambda: [
                send_latest_payments_singlewallet(),
                send_latest_payments_multiwallet()  # Additional wallets
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
    scheduler.add_job(
        check_multi_wallet_balances,
        'interval',
        seconds=60,  # Adjust as you wish
        id='multi_wallet_balance_check',
        next_run_time=datetime.utcnow() + timedelta(seconds=3)
    )
    logger.info("Multi-wallet balance check scheduled every 60 seconds.")

    scheduler.start()
    logger.info("Scheduler started.")

def send_main_inline_keyboard():
    """
    Optionally send the main inline keyboard to the user upon startup 
    (so they see the new multi-wallet buttons).
    """
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
        logger.info("Main inline keyboard successfully sent on startup.")
    except Exception as e:
        logger.error(f"Error sending the main inline keyboard: {e}")
        logger.debug(traceback.format_exc())

def send_start_message(update, context):
    """
    This is triggered by the /start command, 
    sends a textual welcome + main keyboard.
    """
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
    """
    The application's main entry point:
      - Runs Flask in a background thread
      - Initializes processed payments
      - Starts the scheduler
      - Sets up the Telegram bot handlers
      - Sends the main keyboard
      - Waits for idle
    """
    # Start Flask in a thread
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    logger.debug("Flask app thread started.")

    # Mark all main-wallet payments as processed
    initialize_processed_payments()

    # Start the scheduler
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()
    logger.debug("Scheduler thread started.")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Basic commands
    dispatcher.add_handler(CommandHandler('balance', lambda update, context: send_balance_message(update.effective_chat.id)))
    dispatcher.add_handler(CommandHandler('transactions', lambda update, context: send_transactions_message(update.effective_chat.id, page=1)))
    dispatcher.add_handler(CommandHandler('info', handle_info_command))
    dispatcher.add_handler(CommandHandler('help', handle_help_command))
    dispatcher.add_handler(CommandHandler('start', send_start_message))
    dispatcher.add_handler(CommandHandler('ticker_ban', handle_ticker_ban, pass_args=True))

    # Callback query pattern for our inline keyboards
    dispatcher.add_handler(CallbackQueryHandler(
        handle_transactions_callback,
        pattern='^(balance|transactions_inline|prev_\\d+|next_\\d+|overwatch_inline|liveticker_inline|lnbits_inline|multiwallet_balance|multiwallet_transactions|mw_balance_.*|mw_tx_.*)$'
    ))

    # Text message handlers from the reply keyboard
    dispatcher.add_handler(MessageHandler(Filters.regex('^💰 Balance$'), handle_balance))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📜 Latest Transactions$'), handle_latest_transactions))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📡 Live Ticker$'), handle_live_ticker))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📊 Overwatch$'), handle_overwatch))
    dispatcher.add_handler(MessageHandler(Filters.regex('^⚡ LNBits$'), handle_lnbits))

    # Start the bot
    updater.start_polling()
    logger.info("Telegram Bot started.")
    
    # Optionally send the main inline keyboard right now
    send_main_inline_keyboard()

    updater.idle()

def run_flask_app():
    """
    Runs the Flask application, serving on APP_HOST:APP_PORT 
    unless there's an exception.
    """
    try:
        logger.info(f"Starting Flask app on {APP_HOST}:{APP_PORT}")
        app.run(host=APP_HOST, port=APP_PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error running Flask app: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Entry Point ---------------------

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Multi-Wallet Monitor.")
    logger.info(f"🔔 Balance Change Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"🔔 Highlight Threshold: {HIGHLIGHT_THRESHOLD} sats")
    logger.info(f"📊 Fetching the latest {LATEST_TRANSACTIONS_COUNT} transactions.")
    if PAYMENTS_FETCH_INTERVAL > 0:
        logger.info(f"⏲️ Interval: every {PAYMENTS_FETCH_INTERVAL} seconds")
    else:
        logger.info("⏲️ Fetch Interval disabled")

    # Load donations (if enabled) and launch
    load_donations()
    main()
