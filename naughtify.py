import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Bot, ParseMode, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from dotenv import load_dotenv
import requests
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, render_template, make_response
from datetime import datetime, timedelta
import threading
import qrcode
import io
import base64
import json
from urllib.parse import urlparse
import re
import uuid

# --------------------- Configuration and Setup ---------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

try:
    CHAT_ID = int(CHAT_ID)
except (TypeError, ValueError):
    raise EnvironmentError("CHAT_ID must be an integer.")

LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

parsed_lnbits_url = urlparse(LNBITS_URL)
LNBITS_DOMAIN = parsed_lnbits_url.netloc

OVERWATCH_URL = os.getenv("OVERWATCH_URL")  # Optional

DONATIONS_URL = os.getenv("DONATIONS_URL")  # Optional
LNURLP_ID = os.getenv("LNURLP_ID") if DONATIONS_URL else None

FORBIDDEN_WORDS_FILE = os.getenv("FORBIDDEN_WORDS_FILE", "forbidden_words.txt")

BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "10"))  
HIGHLIGHT_THRESHOLD = int(os.getenv("HIGHLIGHT_THRESHOLD", "2100"))  
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))  

PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "60"))  

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")  
APP_PORT = int(os.getenv("APP_PORT", "5009"))

PROCESSED_PAYMENTS_FILE = os.getenv("PROCESSED_PAYMENTS_FILE", "processed_payments.txt")
CURRENT_BALANCE_FILE = os.getenv("CURRENT_BALANCE_FILE", "current-balance.txt")
DONATIONS_FILE = os.getenv("DONATIONS_FILE", "donations.json")

INFORMATION_URL = os.getenv("INFORMATION_URL")  # Optional

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

bot = Bot(token=TELEGRAM_BOT_TOKEN)

logger = logging.getLogger("lnbits_logger")
logger.setLevel(logging.DEBUG)
file_handler = RotatingFileHandler("app.log", maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

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
    return InlineKeyboardMarkup(inline_keyboard)

def get_main_keyboard():
    balance_button = ["💰 Balance"]
    main_options_row_1 = [
        "📊 Overwatch",
        "📡 Live Ticker"
    ]
    main_options_row_2 = [
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    keyboard = [
        balance_button,
        main_options_row_1,
        main_options_row_2
    ]

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
        return "No memo provided."
    def replace_match(match):
        word = match.group()
        return '*' * len(word)
    if not FORBIDDEN_WORDS:
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
    return processed

def add_processed_payment(payment_hash):
    try:
        with open(PROCESSED_PAYMENTS_FILE, 'a') as f:
            f.write(f"{payment_hash}\n")
        logger.debug(f"Payment hash {payment_hash} added to processed list.")
    except Exception as e:
        logger.error(f"Error adding processed payment: {e}")
        logger.debug(traceback.format_exc())

def load_last_balance():
    if not os.path.exists(CURRENT_BALANCE_FILE):
        logger.info("Balance file does not exist. Initializing with current balance.")
        return None
    try:
        with open(CURRENT_BALANCE_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                logger.warning("Balance file is empty. Last balance set to 0.")
                return 0.0
            try:
                balance = float(content)
                logger.debug(f"Last balance loaded: {balance} sats.")
                return balance
            except ValueError:
                logger.error(f"Invalid balance value in file: {content}. Last balance set to 0.")
                return 0.0
    except Exception as e:
        logger.error(f"Error loading last balance: {e}")
        logger.debug(traceback.format_exc())
        return 0.0

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
app = Flask(__name__)

latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

latest_payments = []
donations = []
total_donations = 0
last_update = datetime.utcnow()
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
        bot.send_message(chat_id, text="❌ Bitte geben Sie mindestens ein Wort an. Beispiel: /ticker_ban badword")
        return

    words_to_ban = [word.strip() for word in context.args if word.strip()]
    if not words_to_ban:
        bot.send_message(chat_id, text="❌ Keine gültigen Wörter angegeben.")
        return

    added_words = []
    duplicate_words = []

    try:
        with open(FORBIDDEN_WORDS_FILE, 'a') as f:
            for word in words_to_ban:
                if word in FORBIDDEN_WORDS:
                    duplicate_words.append(word)
                else:
                    f.write(word + '\n')
                    FORBIDDEN_WORDS.add(word)
                    added_words.append(word)

        if added_words:
            logger.info(f"Added words to forbidden list: {added_words}")
            sanitize_donations()
            if len(added_words) == 1:
                success_message = f"✅ '{added_words[0]}' hinzugefügt."
            else:
                words_formatted = "', '".join(added_words)
                success_message = f"✅ '{words_formatted}' hinzugefügt."
            bot.send_message(chat_id, text=success_message)
        if duplicate_words:
            if len(duplicate_words) == 1:
                duplicate_message = f"⚠️ Das Wort '{duplicate_words[0]}' ist bereits gebannt."
            else:
                words_formatted = "', '".join(duplicate_words)
                duplicate_message = f"⚠️ Die Wörter '{words_formatted}' sind bereits gebannt."
            bot.send_message(chat_id, text=duplicate_message)
    except Exception as e:
        logger.error(f"Error adding words to forbidden list: {e}")
        bot.send_message(chat_id, text="❌ Ein Fehler ist aufgetreten.")

def fetch_api(endpoint):
    url = f"{LNBITS_URL}/api/v1/{endpoint}"
    headers = {"X-Api-Key": LNBITS_READONLY_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Data fetched from {endpoint}: {data}")
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
            "lightning_address": "Not available",
            "lnurl": "Not available"
        }

    lnurlp_info = get_lnurlp_info(LNURLP_ID)
    if lnurlp_info is None:
        logger.error("No LNURLp info found.")
        return {
            "total_donations": total_donations,
            "donations": donations,
            "lightning_address": "Not available",
            "lnurl": "Not available"
        }

    username = lnurlp_info.get('username', 'Unknown')
    lightning_address = f"{username}@{LNBITS_DOMAIN}"
    lnurl = lnurlp_info.get('lnurl', '')

    return {
        "total_donations": total_donations,
        "donations": donations,
        "lightning_address": lightning_address,
        "lnurl": lnurl
    }

def update_donations_with_details(data):
    donation_details = fetch_donation_details()
    data.update({
        "lightning_address": donation_details.get("lightning_address"),
        "lnurl": donation_details.get("lnurl")
    })
    return data

def updateDonations(data):
    updated_data = update_donations_with_details(data)
    if updated_data["donations"]:
        latestDonation = updated_data["donations"][-1]
        logger.info(f'Latest donation: {latestDonation["amount"]} sats - "{latestDonation["memo"]}"')
    else:
        logger.info('Latest donation: None yet.')
    save_donations()

def handle_vote_command(donation_id, vote_type):
    try:
        for donation in donations:
            if donation.get("id") == donation_id:
                if vote_type == 'like':
                    donation["likes"] += 1
                elif vote_type == 'dislike':
                    donation["dislikes"] += 1
                else:
                    return {"error": "Invalid vote type."}, 400
                save_donations()
                return {"success": True, "likes": donation["likes"], "dislikes": donation["dislikes"]}, 200
        return {"error": "Donation not found."}, 404
    except Exception as e:
        logger.error(f"Error handling vote: {e}")
        logger.debug(traceback.format_exc())
        return {"error": "Internal server error."}, 500

def parse_time(time_input):
    if not time_input:
        logger.warning("No 'time' field found, using current time.")
        return datetime.utcnow()
    if isinstance(time_input, str):
        try:
            date = datetime.strptime(time_input, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            try:
                date = datetime.strptime(time_input, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                logger.error(f"Unable to parse time string: {time_input}")
                date = datetime.utcnow()
    elif isinstance(time_input, (int, float)):
        try:
            date = datetime.fromtimestamp(time_input)
        except Exception as e:
            logger.error(f"Unable to parse timestamp: {time_input}, error: {e}")
            date = datetime.utcnow()
    else:
        logger.error(f"Unsupported time format: {time_input}")
        date = datetime.utcnow()
    return date

def send_latest_payments():
    global total_donations, donations, last_update
    logger.info("Fetching latest payments...")
    payments = fetch_api("payments")
    if payments is None:
        return
    if not isinstance(payments, list):
        logger.error("Unexpected data format for payments.")
        return

    sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
    latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]

    if not latest:
        logger.info("No payments found.")
        return

    incoming_payments = []
    outgoing_payments = []
    new_processed_hashes = []

    for payment in latest:
        payment_hash = payment.get("payment_hash")
        if payment_hash in processed_payments:
            continue
        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo provided."))
        status = payment.get("status", "completed")
        time_str = payment.get("time", None)
        date = parse_time(time_str)
        formatted_date = date.strftime("%b %d, %Y %H:%M")
        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0

        if status.lower() == "pending":
            continue

        if amount_msat > 0:
            incoming_payments.append({
                "amount": amount_sats,
                "memo": memo,
                "date": formatted_date
            })
        elif amount_msat < 0:
            outgoing_payments.append({
                "amount": amount_sats,
                "memo": memo,
                "date": formatted_date
            })

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
                updateDonations({
                    "total_donations": total_donations,
                    "donations": donations
                })

        processed_payments.add(payment_hash)
        new_processed_hashes.append(payment_hash)
        add_processed_payment(payment_hash)

    for payment in incoming_payments:
        notify_transaction(payment, "incoming")

    for payment in outgoing_payments:
        notify_transaction(payment, "outgoing")

    if not incoming_payments and not outgoing_payments:
        logger.info("No new incoming or outgoing payments to notify.")

def notify_transaction(payment, direction):
    try:
        amount = payment["amount"]
        memo = payment["memo"]
        date = payment["date"]
        emoji = "🟢" if direction == "incoming" else "🔴"
        sign = "+" if direction == "incoming" else "-"
        transaction_type = "Incoming Payment" if direction == "incoming" else "Outgoing Payment"

        message = (
            f"{emoji} *{transaction_type}*\n"
            f"💰 Amount: {sign}{amount} sats\n"
            f"✉️ Memo: {memo}"
        )

        bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Notification for {transaction_type} sent successfully.")
    except Exception as e:
        logger.error(f"Error sending transaction notification: {e}")
        logger.debug(traceback.format_exc())

def send_main_inline_keyboard():
    inline_reply_markup = get_main_inline_keyboard()
    try:
        welcome_message = (
            "😶‍🌫️ Here we go!\n\n"
            "I'm now ready again to assist you with monitoring your LNbits transactions.\n\n"
            "Use the buttons below to navigate through my features."
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
        "👋 Welcome to Naughtify your LNBits Wallet Monitor!\n\n"
        "Use the buttons below to perform various actions."
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

def send_balance_message(chat_id):
    logger.info(f"Fetching balance for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        bot.send_message(chat_id, text="❌ Unable to fetch balance.")
        return
    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000
    balance_text = f"💰 *Current Balance:* {int(current_balance_sats)} sats"
    try:
        bot.send_message(
            chat_id=chat_id,
            text=balance_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Balance message sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending balance message: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_transactions_message(chat_id, page=1, message_id=None):
    logger.info(f"Fetching transactions for chat_id: {chat_id}, page: {page}")
    payments = fetch_api("payments")
    if payments is None:
        bot.send_message(chat_id, text="❌ Unable to fetch transactions.")
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
        return

    start_index = (page - 1) * transactions_per_page
    end_index = start_index + transactions_per_page
    page_transactions = sorted_payments[start_index:end_index]
    if not page_transactions:
        bot.send_message(chat_id, text="❌ No transactions found on this page.")
        return

    message_lines = [
        f"📜 *Latest Transactions - Page {page}/{total_pages}* 📜\n"
    ]
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
        sign = "+" if amount_msat > 0 else "-"
        emoji = "🟢" if amount_msat > 0 else "🔴"
        message_lines.append(f"{emoji} {formatted_date} {sign}{amount_sats} sat")
        message_lines.append(f"✉️ {memo}")

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
        logger.error(f"Error sending/ editing transactions: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_prev_page(update, context):
    query = update.callback_query
    if not query:
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
    query.answer()

def handle_next_page(update, context):
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    data = query.data
    match = re.match(r'next_(\d+)', data)
    if match:
        current_page = int(match.group(1))
        new_page = current_page + 1
        send_transactions_message(chat_id, page=new_page, message_id=message_id)
    query.answer()

def handle_balance_callback(query):
    try:
        chat_id = query.message.chat.id
        send_balance_message(chat_id)
    except Exception as e:
        logger.error(f"Error handling balance callback: {e}")
        logger.debug(traceback.format_exc())

def handle_transactions_inline_callback(query):
    try:
        chat_id = query.message.chat.id
        send_transactions_message(chat_id, page=1, message_id=query.message.message_id)
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
        elif data == 'liveticker_inline' and DONATIONS_URL:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="🔗 *Live Ticker Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open Live Ticker", url=DONATIONS_URL)]
                ])
            )
        elif data == 'lnbits_inline' and LNBITS_URL:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="🔗 *LNBits Details:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Open LNBits", url=LNBITS_URL)]
                ])
            )
        else:
            bot.send_message(
                chat_id=query.message.chat.id,
                text="❌ URL is not configured."
            )
    except Exception as e:
        logger.error(f"Error handling donations_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_other_inline_callbacks(data, query):
    bot.answer_callback_query(callback_query_id=query.id, text="❓ Unknown action.")

def handle_transactions_callback(update, context):
    query = update.callback_query
    data = query.data
    logger.debug(f"Handling callback data: {data}")

    if data == 'balance':
        handle_balance_callback(query)
    elif data == 'transactions_inline':
        handle_transactions_inline_callback(query)
    elif data.startswith('prev_') or data.startswith('next_'):
        if data.startswith('prev_'):
            current_page = int(data.split('_')[1])
            new_page = current_page - 1
        elif data.startswith('next_'):
            current_page = int(data.split('_')[1])
            new_page = current_page + 1
        send_transactions_message(query.message.chat.id, page=new_page, message_id=query.message.message_id)
    elif data in ['overwatch_inline', 'liveticker_inline', 'lnbits_inline']:
        handle_donations_inline_callback(query)
    else:
        handle_other_inline_callbacks(data, query)

    query.answer()

def handle_info_command(update, context):
    chat_id = update.effective_chat.id
    logger.info(f"Handling /info command for chat_id: {chat_id}")
    interval_info = (
        f"🔔 *Balance Change Threshold:* {BALANCE_CHANGE_THRESHOLD} sats\n"
        f"🔔 *Highlight Threshold:* {HIGHLIGHT_THRESHOLD} sats\n"
        f"🔄 *Latest Payments Fetch Interval:* Every {PAYMENTS_FETCH_INTERVAL / 60:.1f} minutes"
    )

    info_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Information*\n\n"
        f"{interval_info}"
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
        f"Commands:\n"
        f"• /balance\n"
        f"• /transactions\n"
        f"• /info\n"
        f"• /help\n"
        f"• /ticker_ban\n\n"
        f"Use buttons below for navigation."
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
    send_balance_message(chat_id)

def handle_latest_transactions(update, context):
    chat_id = update.effective_chat.id
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

def process_update(update):
    try:
        if 'message' in update:
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()

            if text.startswith('/balance'):
                send_balance_message(chat_id)
            elif text.startswith('/transactions'):
                send_transactions_message(chat_id, page=1)
            elif text.startswith('/info'):
                handle_info_command(update, None)
            elif text.startswith('/help'):
                handle_help_command(update, None)
            elif text.startswith('/ticker_ban'):
                handle_ticker_ban(update, None)
            else:
                if text == "💰 Balance":
                    handle_balance(update, None)
                elif text == "📜 Latest Transactions":
                    handle_latest_transactions(update, None)
                elif text == "📡 Live Ticker":
                    handle_live_ticker(update, None)
                elif text == "📊 Overwatch":
                    handle_overwatch(update, None)
                elif text == "⚡ LNBits":
                    handle_lnbits(update, None)
                else:
                    bot.send_message(
                        chat_id=chat_id,
                        text="❓ Unknown command."
                    )
        elif 'callback_query' in update:
            pass
        else:
            logger.info("No message or callback in update.")
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        logger.debug(traceback.format_exc())

def start_scheduler():
    scheduler = BackgroundScheduler(timezone='UTC')
    if PAYMENTS_FETCH_INTERVAL > 0:
        scheduler.add_job(
            send_latest_payments,
            'interval',
            seconds=PAYMENTS_FETCH_INTERVAL,
            id='latest_payments_fetch',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Latest Payments Fetch every {PAYMENTS_FETCH_INTERVAL / 60:.1f} minutes.")
    else:
        logger.info("Latest Payments Fetch disabled.")
    scheduler.start()
    logger.info("Scheduler started.")

@app.route('/')
def home():
    return "🔍 LNbits Monitor is running."

@app.route('/status', methods=['GET'])
def status():
    donation_details = fetch_donation_details()
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
        logger.warning("Empty update.")
        return "No update", 400

    logger.debug(f"Update received: {update}")
    threading.Thread(target=process_update, args=(update,)).start()
    return "OK", 200

@app.route('/donations')
def donations_page():
    if not DONATIONS_URL or not LNURLP_ID:
        return "Donations not enabled.", 404
    lnurlp_id = LNURLP_ID
    lnurlp_info = get_lnurlp_info(lnurlp_id)
    if lnurlp_info is None:
        return "Error fetching LNURLP info", 500

    wallet_name = lnurlp_info.get('description', 'Unknown Wallet')
    lightning_address = lnurlp_info.get('lightning_address', 'Unknown Lightning Address')
    lnurl = lnurlp_info.get('lnurl', '')

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(lnurl)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    img_base64 = base64.b64encode(img_io.getvalue()).decode()

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
            return jsonify({"error": "donation_id and vote_type required."}), 400
        if vote_type not in ['like', 'dislike']:
            return jsonify({"error": "vote_type must be 'like' or 'dislike'."}), 400

        voted_donations = request.cookies.get('voted_donations', '')
        voted_set = set(voted_donations.split(',')) if voted_donations else set()
        if donation_id in voted_set:
            return jsonify({"error": "Already voted on this donation."}), 403

        result, status_code = handle_vote_command(donation_id, vote_type)
        if status_code != 200:
            return jsonify(result), status_code

        response = make_response(jsonify(result), 200)
        voted_set.add(donation_id)
        new_voted_donations = ','.join(voted_set)
        response.set_cookie('voted_donations', new_voted_donations, max_age=60*60*24*365)
        return response
    except Exception as e:
        logger.error(f"Error processing vote: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Internal server error."}), 500

@app.route('/donations_updates', methods=['GET'])
def donations_updates():
    global last_update
    if not DONATIONS_URL or not LNURLP_ID:
        return jsonify({"error": "Donations not enabled."}), 404
    try:
        return jsonify({"last_update": last_update.isoformat()}), 200
    except Exception as e:
        logger.error(f"Error fetching last_update: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching last_update"}), 500

# Neue Route für Cinema Mode, ohne Code zu löschen:
@app.route('/cinema')
def cinema_page():
    if not DONATIONS_URL or not LNURLP_ID:
        return "Donations are not enabled for Cinema Mode.", 404
    return render_template('cinema.html')

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('balance', lambda update, context: send_balance_message(update.effective_chat.id)))
    dispatcher.add_handler(CommandHandler('transactions', lambda update, context: send_transactions_message(update.effective_chat.id, page=1)))
    dispatcher.add_handler(CommandHandler('info', handle_info_command))
    dispatcher.add_handler(CommandHandler('help', handle_help_command))
    dispatcher.add_handler(CommandHandler('start', send_start_message))
    dispatcher.add_handler(CommandHandler('ticker_ban', handle_ticker_ban))

    dispatcher.add_handler(CallbackQueryHandler(handle_transactions_callback, pattern='^(balance|transactions_inline|prev_\\d+|next_\\d+|overwatch_inline|liveticker_inline|lnbits_inline)$'))

    dispatcher.add_handler(MessageHandler(Filters.regex('^💰 Balance$'), handle_balance))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📜 Latest Transactions$'), handle_latest_transactions))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📡 Live Ticker$'), handle_live_ticker))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📊 Overwatch$'), handle_overwatch))
    dispatcher.add_handler(MessageHandler(Filters.regex('^⚡ LNBits$'), handle_lnbits))

    updater.start_polling()
    logger.info("Telegram Bot started.")
    send_main_inline_keyboard()
    updater.idle()

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Balance Monitor.")
    logger.info(f"🔔 Balance Change Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"🔔 Highlight Threshold: {HIGHLIGHT_THRESHOLD} sats")
    logger.info(f"📊 Fetching the latest {LATEST_TRANSACTIONS_COUNT} transactions")
    if PAYMENTS_FETCH_INTERVAL > 0:
        logger.info(f"⏲️ Interval: every {PAYMENTS_FETCH_INTERVAL / 60:.1f} min")
    else:
        logger.info("⏲️ Fetch Interval disabled")

    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    flask_thread = threading.Thread(target=lambda: app.run(host=APP_HOST, port=APP_PORT), daemon=True)
    flask_thread.start()
    logger.info(f"Flask Server at {APP_HOST}:{APP_PORT}")
    main()
