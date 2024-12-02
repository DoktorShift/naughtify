import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from dotenv import load_dotenv
import requests
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
import threading
import time
import qrcode
import io
import base64
import json

# --------------------- Configuration and Setup ---------------------

# Load environment variables from .env file
load_dotenv()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

# LNbits Configuration
LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

# Overwatch Configuration
OVERWATCH_URL = os.getenv("OVERWATCH_URL")

# Donation Parameters
LNURLP_ID = os.getenv("LNURLP_ID")

# Notification Settings
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "1000"))  # Default: 1000 sats
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))    # Default: 21 transactions

# Scheduler Intervals (in seconds)
WALLET_INFO_UPDATE_INTERVAL = int(os.getenv("WALLET_INFO_UPDATE_INTERVAL", "60"))          # Default: 60 seconds
WALLET_BALANCE_NOTIFICATION_INTERVAL = int(os.getenv("WALLET_BALANCE_NOTIFICATION_INTERVAL", "86400"))  # Default: 24 hours
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "86400"))                # Default: 24 hours

# Flask Server Configuration
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")    # Default: localhost
APP_PORT = int(os.getenv("APP_PORT", "5009"))   # Default: port 5009

# File Paths
PROCESSED_PAYMENTS_FILE = "processed_payments.txt"
CURRENT_BALANCE_FILE = "current-balance.txt"

# Validate essential environment variables
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL,
    "OVERWATCH_URL": OVERWATCH_URL,
    "LNURLP_ID": LNURLP_ID
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise EnvironmentError(f"Required environment variables are missing: {', '.join(missing_vars)}")

# Initialize Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# --------------------- Logging Configuration ---------------------
logger = logging.getLogger("lnbits_logger")
logger.setLevel(logging.DEBUG)

# File handler for detailed logs
file_handler = RotatingFileHandler("app.log", maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setLevel(logging.DEBUG)

# Console handler for general information
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Log format
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to the logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --------------------- Helper Functions ---------------------

def load_processed_payments():
    """
    Load processed payment hashes from the tracking file into a set.
    """
    processed = set()
    if os.path.exists(PROCESSED_PAYMENTS_FILE):
        try:
            with open(PROCESSED_PAYMENTS_FILE, 'r') as f:
                for line in f:
                    processed.add(line.strip())
            logger.debug(f"Loaded {len(processed)} processed payment hashes.")
        except Exception as e:
            logger.error(f"Failed to load processed payments: {e}")
            logger.debug(traceback.format_exc())
    return processed

def add_processed_payment(payment_hash):
    """
    Add a processed payment hash to the tracking file.
    """
    try:
        with open(PROCESSED_PAYMENTS_FILE, 'a') as f:
            f.write(f"{payment_hash}\n")
        logger.debug(f"Added payment hash {payment_hash} to processed payments.")
    except Exception as e:
        logger.error(f"Failed to add processed payment: {e}")
        logger.debug(traceback.format_exc())

def load_last_balance():
    """
    Load the last known balance from the balance file.
    """
    if not os.path.exists(CURRENT_BALANCE_FILE):
        logger.info("Balance file does not exist. Initializing with current balance.")
        return None
    try:
        with open(CURRENT_BALANCE_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                logger.warning("Balance file is empty. Setting last balance to 0.")
                return 0.0
            try:
                balance = float(content)
                logger.debug(f"Loaded last balance: {balance} sats.")
                return balance
            except ValueError:
                logger.error(f"Invalid balance value in file: {content}. Setting last balance to 0.")
                return 0.0
    except Exception as e:
        logger.error(f"Failed to load last balance: {e}")
        logger.debug(traceback.format_exc())
        return 0.0

def save_current_balance(balance):
    """
    Save the current balance to the balance file.
    """
    try:
        with open(CURRENT_BALANCE_FILE, 'w') as f:
            f.write(str(balance))
        logger.debug(f"Saved current balance: {balance} sats.")
    except Exception as e:
        logger.error(f"Failed to save current balance: {e}")
        logger.debug(traceback.format_exc())

# Initialize the set of processed payments
processed_payments = load_processed_payments()

# Initialize Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Global variables to store the latest data
latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

latest_payments = []

# New Data Structures for Donations
donations = []
total_donations = 0

# --------------------- Functions ---------------------

def fetch_api(endpoint):
    """
    Fetch data from the LNbits API.
    """
    url = f"{LNBITS_URL}/api/v1/{endpoint}"
    headers = {"X-Api-Key": LNBITS_READONLY_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Fetched data from {endpoint}: {data}")
            return data
        else:
            logger.error(f"Failed to fetch {endpoint}. Status Code: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching {endpoint}: {e}")
        logger.debug(traceback.format_exc())
        return None

def fetch_pay_links():
    """
    Fetch pay links from the LNbits LNURLp extension API.
    """
    url = f"{LNBITS_URL}/lnurlp/api/v1/links"
    headers = {"X-Api-Key": LNBITS_READONLY_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Fetched pay links: {data}")
            return data
        else:
            logger.error(f"Failed to fetch pay links. Status Code: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching pay links: {e}")
        logger.debug(traceback.format_exc())
        return None

def get_lnurlp_info(lnurlp_id):
    """
    Get LNURLp information for a given lnurlp_id.
    """
    pay_links = fetch_pay_links()
    if pay_links is None:
        logger.error("Could not retrieve pay links.")
        return None

    for pay_link in pay_links:
        if pay_link.get("id") == lnurlp_id:
            logger.debug(f"Found matching pay link: {pay_link}")
            return pay_link

    logger.error(f"No pay link found with ID {lnurlp_id}")
    return None

def send_latest_payments():
    """
    Fetch the latest payments and send a notification via Telegram.
    """
    logger.info("Fetching latest payments...")
    payments = fetch_api("payments")
    if payments is None:
        return

    if not isinstance(payments, list):
        logger.error("Unexpected data format for payments.")
        return

    # Sort payments by creation time descending
    sorted_payments = sorted(payments, key=lambda x: x.get("created_at", ""), reverse=True)
    latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]  # Get the latest n payments

    if not latest:
        logger.info("No payments found.")
        return

    # Initialize lists for different payment types
    incoming_payments = []
    outgoing_payments = []
    pending_payments = []
    new_processed_hashes = []

    for payment in latest:
        payment_hash = payment.get("payment_hash")
        if payment_hash in processed_payments:
            continue  # Skip already processed payments

        amount_msat = payment.get("amount", 0)
        memo = payment.get("memo", "No memo")
        status = payment.get("status", "completed")

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0

        if status.lower() == "pending":
            if amount_msat > 0:
                pending_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })
        else:
            if amount_msat > 0:
                incoming_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })
            elif amount_msat < 0:
                outgoing_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })

        # Mark payment as processed
        processed_payments.add(payment_hash)
        new_processed_hashes.append(payment_hash)
        add_processed_payment(payment_hash)

    if not incoming_payments and not outgoing_payments and not pending_payments:
        logger.info("No new payments to notify.")
        return

    message_lines = [
        f"⚡ *{INSTANCE_NAME}* - *Latest Transactions* ⚡\n"
    ]

    if incoming_payments:
        message_lines.append("🟢 *Incoming Payments:*")
        for idx, payment in enumerate(incoming_payments, 1):
            message_lines.append(
                f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
            )
        message_lines.append("")

    if outgoing_payments:
        message_lines.append("🔴 *Outgoing Payments:*")
        for idx, payment in enumerate(outgoing_payments, 1):
            message_lines.append(
                f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
            )
        message_lines.append("")

    if pending_payments:
        message_lines.append("⏳ *Payments in Progress:*")
        for payment in pending_payments:
            message_lines.append(
                f"   {payment['amount']} sats\n"
                f"   📝 *Memo:* {payment['memo']}\n"
                f"   📅 *Status:* In progress\n"
            )
        message_lines.append("")

    # Append the timestamp
    timestamp_text = f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    message_lines.append(timestamp_text)

    full_message = "\n".join(message_lines)

    # Define the inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)],
        [InlineKeyboardButton("📈 View Transactions", callback_data='view_transactions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the message to Telegram with the inline keyboard
    try:
        bot.send_message(chat_id=CHAT_ID, text=full_message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        logger.info("Latest payments notification sent to Telegram successfully.")
        latest_payments.extend(new_processed_hashes)
    except Exception as telegram_error:
        logger.error(f"Failed to send payments message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def check_balance_change():
    """
    Periodically check the wallet balance and notify if it has changed beyond the threshold.
    """
    logger.info("Checking for balance changes...")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    last_balance = load_last_balance()

    if last_balance is None:
        # First run, initialize the balance file
        save_current_balance(current_balance_sats)
        latest_balance["balance_sats"] = current_balance_sats
        latest_balance["last_change"] = "Initial balance set."
        latest_balance["memo"] = "N/A"
        logger.info(f"Initial balance set to {current_balance_sats:.0f} sats.")
        return

    change_amount = current_balance_sats - last_balance
    if abs(change_amount) < BALANCE_CHANGE_THRESHOLD:
        logger.info(f"Balance change ({abs(change_amount):.0f} sats) below threshold ({BALANCE_CHANGE_THRESHOLD} sats). No notification sent.")
        return

    direction = "increased" if change_amount > 0 else "decreased"
    abs_change = abs(change_amount)

    # Prepare the Telegram message with markdown formatting
    message = (
        f"⚡ *{INSTANCE_NAME}* - *Balance Update* ⚡\n\n"
        f"🔹 *Previous Balance:* `{int(last_balance):,} sats`\n"
        f"🔹 *Change:* `{'+' if change_amount > 0 else '-'}{int(abs_change):,} sats`\n"
        f"🔹 *New Balance:* `{int(current_balance_sats):,} sats`\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)],
        [InlineKeyboardButton("📈 View Transactions", callback_data='view_transactions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send message to Telegram with the inline keyboard
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        logger.info(f"Balance changed from {last_balance:.0f} to {current_balance_sats:.0f} sats. Notification sent.")
        # Update the balance file and latest_balance
        save_current_balance(current_balance_sats)
        latest_balance["balance_sats"] = current_balance_sats
        latest_balance["last_change"] = f"Balance {direction} by {int(abs_change):,} sats."
        latest_balance["memo"] = "N/A"
    except Exception as telegram_error:
        logger.error(f"Failed to send balance change message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_wallet_balance():
    """
    Send the current wallet balance via Telegram in a professional and clear format.
    """
    logger.info("Sending daily wallet balance notification...")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    # Fetch payments to calculate counts and totals
    payments = fetch_api("payments")
    incoming_count = outgoing_count = 0
    incoming_total = outgoing_total = 0
    if payments and isinstance(payments, list):
        for payment in payments:
            amount_msat = payment.get("amount", 0)
            status = payment.get("status", "completed")
            if status.lower() == "pending":
                continue  # Exclude pending for daily balance
            if amount_msat > 0:
                incoming_count += 1
                incoming_total += amount_msat / 1000
            elif amount_msat < 0:
                outgoing_count += 1
                outgoing_total += abs(amount_msat) / 1000

    # Prepare the Telegram message with markdown formatting
    message = (
        f"📊 *{INSTANCE_NAME}* - *Daily Wallet Balance* 📊\n\n"
        f"🔹 *Current Balance:* `{int(current_balance_sats)} sats`\n"
        f"🔹 *Total Incoming:* `{int(incoming_total)} sats` across `{incoming_count}` transactions\n"
        f"🔹 *Total Outgoing:* `{int(outgoing_total)} sats` across `{outgoing_count}` transactions\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)],
        [InlineKeyboardButton("📈 View Transactions", callback_data='view_transactions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send the message to Telegram with the inline keyboard
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        logger.info("Daily wallet balance notification with inline keyboard sent successfully.")
        # Update the latest_balance
        latest_balance["balance_sats"] = current_balance_sats
        latest_balance["last_change"] = "Daily balance report."
        latest_balance["memo"] = "N/A"
        # Save the current balance
        save_current_balance(current_balance_sats)
    except Exception as telegram_error:
        logger.error(f"Failed to send daily wallet balance message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_transactions_command(chat_id):
    """
    Handle the /transactions command sent by the user.
    """
    logger.info(f"Handling /transactions command for chat_id: {chat_id}")
    payments = fetch_api("payments")
    if payments is None:
        bot.send_message(chat_id=chat_id, text="Error fetching transactions.")
        return

    if not isinstance(payments, list):
        bot.send_message(chat_id=chat_id, text="Unexpected data format for transactions.")
        return

    # Sort transactions by creation time descending
    sorted_payments = sorted(payments, key=lambda x: x.get("created_at", ""), reverse=True)
    latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]  # Get the latest n transactions

    if not latest:
        bot.send_message(chat_id=chat_id, text="No transactions found.")
        return

    # Initialize lists for different transaction types
    incoming_payments = []
    outgoing_payments = []
    pending_payments = []

    for payment in latest:
        amount_msat = payment.get("amount", 0)
        memo = payment.get("memo", "No memo")
        status = payment.get("status", "completed")

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0

        if status.lower() == "pending":
            if amount_msat > 0:
                pending_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })
        else:
            if amount_msat > 0:
                incoming_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })
            elif amount_msat < 0:
                outgoing_payments.append({
                    "amount": amount_sats,
                    "memo": memo
                })

    message_lines = [
        f"⚡ *{INSTANCE_NAME}* - *Latest Transactions* ⚡\n"
    ]

    if incoming_payments:
        message_lines.append("🟢 *Incoming Payments:*")
        for idx, payment in enumerate(incoming_payments, 1):
            message_lines.append(
                f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
            )
        message_lines.append("")

    if outgoing_payments:
        message_lines.append("🔴 *Outgoing Payments:*")
        for idx, payment in enumerate(outgoing_payments, 1):
            message_lines.append(
                f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
            )
        message_lines.append("")

    if pending_payments:
        message_lines.append("⏳ *Payments in Progress:*")
        for payment in pending_payments:
            message_lines.append(
                f"   {payment['amount']} sats\n"
                f"   📝 *Memo:* {payment['memo']}\n"
                f"   📅 *Status:* In progress\n"
            )
        message_lines.append("")

    # Append the timestamp
    timestamp_text = f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    message_lines.append(timestamp_text)

    full_message = "\n".join(message_lines)

    # Define the inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        bot.send_message(chat_id=chat_id, text=full_message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Failed to send /transactions message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_info_command(chat_id):
    """
    Handle the /info command sent by the user.
    """
    logger.info(f"Handling /info command for chat_id: {chat_id}")
    # Prepare Interval Information
    interval_info = (
        f"🔔 *Balance Change Threshold:* `{BALANCE_CHANGE_THRESHOLD} sats`\n"
        f"⏲️ *Balance Change Monitoring Interval:* Every `{WALLET_INFO_UPDATE_INTERVAL} seconds`\n"
        f"📊 *Daily Wallet Balance Notification Interval:* Every `{WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds`\n"
        f"🔄 *Latest Payments Fetch Interval:* Every `{PAYMENTS_FETCH_INTERVAL} seconds`"
    )

    info_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Information*\n\n"
        f"{interval_info}\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define the inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)],
        [InlineKeyboardButton("🔧 Manage LNBits Backend", url=LNBITS_URL)],
        [InlineKeyboardButton("📈 View Transactions", callback_data='view_transactions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        bot.send_message(chat_id=chat_id, text=info_message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Failed to send /info message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_balance_command(chat_id):
    """
    Handle the /balance command sent by the user.
    """
    logger.info(f"Handling /balance command for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        bot.send_message(chat_id=chat_id, text="Error fetching wallet balance.")
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    message = (
        f"📊 *{INSTANCE_NAME}* - *Wallet Balance*\n\n"
        f"🔹 *Current Balance:* `{int(current_balance_sats)} sats`\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define the inline keyboard
    keyboard = [
        [InlineKeyboardButton("🔗 View Details", url=OVERWATCH_URL)],
        [InlineKeyboardButton("📈 View Transactions", callback_data='view_transactions')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Failed to send /balance message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_help_command(chat_id):
    """
    Handle the /help command sent by the user.
    """
    logger.info(f"Handling /help command for chat_id: {chat_id}")

    help_message = (
        "🤖 *Naughtify Bot Command Guide* 🤖\n\n"
        "🛠 Commands:\n\n"
        "    /balance: Check your wallet balance in sats.\n"
        "    /transactions: View recent incoming, outgoing, and pending transactions.\n"
        "    /info: See bot settings and LNbits details.\n"
        "    /help: Display this guide.\n\n"
        "🔗 Useful Links:\n\n"
        "    Overwatch Dashboard: Monitor wallet activity.\n"
        "    LNbits Dashboard: Manage wallets, transactions & settings.\n"
        "    Live-Donation Page: Track and share transparent donations.\n\n"
        "💡 *Helpful Tips*\n\n"
        "    • Adjust notification thresholds to receive only relevant updates.\n"
        "    • Use the LNbits interface to maximize the potential of your wallet.\n"
        "    • The Live-Donation Page is perfect for tracking donations in real-time and sharing a public view of donation activity.\n"
    )

    try:
        bot.send_message(
            chat_id=chat_id,
            text=help_message,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        logger.info(f"/help message sent successfully to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Failed to send /help message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def process_update(update):
    """
    Process incoming updates from the Telegram webhook.
    """
    try:
        if 'message' in update:
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()

            if text.startswith('/balance'):
                handle_balance_command(chat_id)
            elif text.startswith('/transactions'):
                handle_transactions_command(chat_id)
            elif text.startswith('/info'):
                handle_info_command(chat_id)
            elif text.startswith('/help'):
                handle_help_command(chat_id)
            else:
                bot.send_message(
                    chat_id=chat_id,
                    text="Unknown command. Available commands: /balance, /transactions, /info, /help"
                )
        elif 'callback_query' in update:
            process_callback_query(update['callback_query'])
        else:
            logger.info("Update does not contain a message or callback_query. Ignoring.")
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        logger.debug(traceback.format_exc())

def process_callback_query(callback_query):
    """
    Process callback queries from inline keyboards in Telegram messages.
    """
    try:
        query_id = callback_query['id']
        data = callback_query.get('data', '')
        chat_id = callback_query['from']['id']

        if data == 'view_transactions':
            handle_transactions_command(chat_id)
            bot.answer_callback_query(callback_query_id=query_id, text="Fetching latest transactions...")
        else:
            bot.answer_callback_query(callback_query_id=query_id, text="Unknown action.")
    except Exception as e:
        logger.error(f"Error processing callback query: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Scheduler Setup ---------------------

def start_scheduler():
    """
    Start the scheduler for periodic tasks using BackgroundScheduler.
    """
    scheduler = BackgroundScheduler(timezone='UTC')

    if WALLET_INFO_UPDATE_INTERVAL > 0:
        scheduler.add_job(
            check_balance_change,
            'interval',
            seconds=WALLET_INFO_UPDATE_INTERVAL,
            id='balance_check',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Balance change monitoring scheduled every {WALLET_INFO_UPDATE_INTERVAL} seconds.")
    else:
        logger.info("Balance change monitoring is disabled (WALLET_INFO_UPDATE_INTERVAL set to 0).")

    if WALLET_BALANCE_NOTIFICATION_INTERVAL > 0:
        scheduler.add_job(
            send_wallet_balance,
            'interval',
            seconds=WALLET_BALANCE_NOTIFICATION_INTERVAL,
            id='wallet_balance_notification',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Daily wallet balance notification scheduled every {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds.")
    else:
        logger.info("Daily wallet balance notification is disabled (WALLET_BALANCE_NOTIFICATION_INTERVAL set to 0).")

    if PAYMENTS_FETCH_INTERVAL > 0:
        scheduler.add_job(
            send_latest_payments,
            'interval',
            seconds=PAYMENTS_FETCH_INTERVAL,
            id='latest_payments_fetch',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Latest payments fetch scheduled every {PAYMENTS_FETCH_INTERVAL} seconds.")
    else:
        logger.info("Latest payments fetch notification is disabled (PAYMENTS_FETCH_INTERVAL set to 0).")

    scheduler.start()
    logger.info("Scheduler started successfully.")

# --------------------- Flask Routes ---------------------

@app.route('/')
def home():
    return "🔍 LNbits Monitor is running."

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "latest_balance": latest_balance,
        "latest_payments": latest_payments
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update:
        logger.warning("Received empty update.")
        return "No update found", 400

    logger.debug(f"Received update: {update}")

    # Process the message in a separate thread to avoid blocking
    threading.Thread(target=process_update, args=(update,)).start()

    return "OK", 200

# Webhook Endpoint for LNbits LNURLp Notifications
@app.route('/lnurlp_webhook', methods=['POST'])
def lnurlp_webhook():
    """
    Handle incoming payment notifications from LNbits LNURLp.
    """
    try:
        data = request.get_json()
        logger.debug(f"Received LNURLp webhook data: {data}")

        # Extract necessary information
        amount_msat = data.get("amount", 0)
        memo = data.get("comment", "No memo")
        payment_hash = data.get("payment_hash")
        timestamp = datetime.utcnow().isoformat()

        if not payment_hash:
            logger.error("No payment_hash found in webhook data.")
            return "Missing payment_hash", 400

        # Check if payment has already been processed
        if payment_hash in processed_payments:
            logger.info(f"Payment {payment_hash} already processed.")
            return "Payment already processed", 200

        # Convert amount to sats
        amount_sats = int(amount_msat) / 1000

        # Update global donations data
        global total_donations, donations
        total_donations += amount_sats
        donation = {
            "date": timestamp,
            "memo": memo,
            "amount": amount_sats
        }
        donations.append(donation)

        # Emit the new donation to all connected clients
        socketio.emit('new_donation', donation)

        # Mark payment as processed
        processed_payments.add(payment_hash)
        add_processed_payment(payment_hash)

        logger.info(f"Processed new donation: {amount_sats} sats - {memo}")
        return "OK", 200

    except Exception as e:
        logger.error(f"Error processing LNURLp webhook: {e}")
        logger.debug(traceback.format_exc())
        return "Error processing webhook", 500

@app.route('/donations')
def donations_page():
    # Get the LNURLP info
    lnurlp_id = LNURLP_ID
    lnurlp_info = get_lnurlp_info(lnurlp_id)
    if lnurlp_info is None:
        return "Error fetching LNURLP info", 500

    # Extract the necessary information
    wallet_name = lnurlp_info.get('description', 'Unknown Wallet')
    lightning_address = lnurlp_info.get('lnurlp', 'Unknown Lightning Address')
    lnurl = lnurlp_info.get('lnurl', '')

    # Generate QR code from LNURL
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(lnurl)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Convert PIL image to base64 string to embed in HTML
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    img_base64 = base64.b64encode(img_io.getvalue()).decode()

    # Render the donations page
    return render_template(
        'donations.html',
        wallet_name=wallet_name,
        lightning_address=lightning_address,
        lnurl=lnurl,
        qr_code_data=img_base64
    )

# API Endpoint to Serve Donation Data
@app.route('/api/donations', methods=['GET'])
def get_donations_data():
    """
    Serve the donations data as JSON for the front-end.
    """
    try:
        data = {
            "total_donations": total_donations,
            "donations": donations
        }
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error fetching donations data: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching donations data"}), 500

# --------------------- Application Entry Point ---------------------

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Balance Monitor.")

    # Log the current configuration
    logger.info(f"🔔 Notification Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"📊 Fetching Latest {LATEST_TRANSACTIONS_COUNT} Transactions for Notifications")
    logger.info(f"⏲️ Scheduler Intervals - Balance Change Monitoring: {WALLET_INFO_UPDATE_INTERVAL} seconds, Daily Wallet Balance Notification: {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds, Latest Payments Fetch: {PAYMENTS_FETCH_INTERVAL} seconds")

    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Start Flask app with SocketIO
    logger.info(f"Flask server running on {APP_HOST}:{APP_PORT}")
    socketio.run(app, host=APP_HOST, port=APP_PORT)
