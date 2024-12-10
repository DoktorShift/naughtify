import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Bot, ReplyKeyboardMarkup, ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from dotenv import load_dotenv
import requests
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, render_template
from datetime import datetime, timedelta
import threading
import qrcode
import io
import base64
import json
from urllib.parse import urlparse
import re

# --------------------- Configuration and Setup ---------------------

# Load environment variables from the .env file
load_dotenv()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Convert CHAT_ID to an integer, if provided
try:
    CHAT_ID = int(CHAT_ID)
except (TypeError, ValueError):
    raise EnvironmentError("CHAT_ID must be an integer.")

# LNbits Configuration
LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

# Extract domain from LNBITS_URL
parsed_lnbits_url = urlparse(LNBITS_URL)
LNBITS_DOMAIN = parsed_lnbits_url.netloc

# Overwatch Configuration
OVERWATCH_URL = os.getenv("OVERWATCH_URL")  # Optional

# Donations Parameter
LNURLP_ID = os.getenv("LNURLP_ID")

# Forbidden Words Configuration
FORBIDDEN_WORDS_FILE = os.getenv("FORBIDDEN_WORDS_FILE", "forbidden_words.txt")

# Notification Settings
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "10"))  # Default: 10 sats
HIGHLIGHT_THRESHOLD = int(os.getenv("HIGHLIGHT_THRESHOLD", "2100"))  # Default: 2100 sats
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))  # Default: 21 transactions

# Scheduler Intervals (in seconds)
WALLET_INFO_UPDATE_INTERVAL = int(os.getenv("WALLET_INFO_UPDATE_INTERVAL", "86400"))  # Default: 86400 seconds (24 hours)
WALLET_BALANCE_NOTIFICATION_INTERVAL = int(os.getenv("WALLET_BALANCE_NOTIFICATION_INTERVAL", "86400"))  # Default: 86400 seconds (24 hours)
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "60"))  # Default: 60 seconds (1 minute)

# Flask Server Configuration
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")  # Default: localhost
APP_PORT = int(os.getenv("APP_PORT", "5009"))  # Default: Port 5009

# File Paths
PROCESSED_PAYMENTS_FILE = os.getenv("PROCESSED_PAYMENTS_FILE", "processed_payments.txt")
CURRENT_BALANCE_FILE = os.getenv("CURRENT_BALANCE_FILE", "current-balance.txt")
DONATIONS_FILE = os.getenv("DONATIONS_FILE", "donations.json")

# Donations Configuration
DONATIONS_URL = os.getenv("DONATIONS_URL")  # Optional

# Information URL Configuration
INFORMATION_URL = os.getenv("INFORMATION_URL")  # New environment variable

# Validate essential environment variables (excluding OVERWATCH_URL and DONATIONS_URL)
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise EnvironmentError(f"Essential environment variables missing: {', '.join(missing_vars)}")

# Initialize the Telegram Bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# --------------------- Logging Configuration ---------------------
logger = logging.getLogger("lnbits_logger")
logger.setLevel(logging.DEBUG)

# File handler for detailed logs
file_handler = RotatingFileHandler("app.log", maxBytes=5 * 1024 * 1024, backupCount=3)
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

def load_forbidden_words(file_path):
    """
    Load forbidden words from a specified file into a set.

    Args:
        file_path (str): Path to the file containing forbidden words.

    Returns:
        set: A set containing all forbidden words.
    """
    forbidden = set()
    try:
        with open(file_path, 'r') as f:
            for line in f:
                word = line.strip()
                if word:  # Avoid empty lines
                    forbidden.add(word.lower())
        logger.debug(f"{len(forbidden)} forbidden words loaded from {file_path}.")
    except FileNotFoundError:
        logger.error(f"Forbidden words file not found: {file_path}.")
    except Exception as e:
        logger.error(f"Error loading forbidden words from {file_path}: {e}")
        logger.debug(traceback.format_exc())
    return forbidden

# Load forbidden words at startup
FORBIDDEN_WORDS = load_forbidden_words(FORBIDDEN_WORDS_FILE)

def sanitize_memo(memo):
    """
    Clean the memo field by replacing forbidden words with asterisks.

    Args:
        memo (str): The original memo text.

    Returns:
        str: The sanitized memo text.
    """
    if not memo:
        return "No memo"
    
    # Function to replace the matched word with asterisks
    def replace_match(match):
        word = match.group()
        return '*' * len(word)
    
    # Create a regex pattern that matches any forbidden word
    if not FORBIDDEN_WORDS:
        return memo  # No forbidden words to sanitize
    
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, FORBIDDEN_WORDS)) + r')\b', re.IGNORECASE)
    sanitized_memo = pattern.sub(replace_match, memo)
    logger.debug(f"Sanitized memo: Original: '{memo}' -> Sanitized: '{sanitized_memo}'")
    return sanitized_memo

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
            logger.debug(f"{len(processed)} processed payment hashes loaded.")
        except Exception as e:
            logger.error(f"Error loading processed payments: {e}")
            logger.debug(traceback.format_exc())
    return processed

def add_processed_payment(payment_hash):
    """
    Add a processed payment hash to the tracking file.
    """
    try:
        with open(PROCESSED_PAYMENTS_FILE, 'a') as f:
            f.write(f"{payment_hash}\n")
        logger.debug(f"Payment hash {payment_hash} added to processed list.")
    except Exception as e:
        logger.error(f"Error adding processed payment: {e}")
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

def save_current_balance(balance):
    """
    Save the current balance to the balance file.
    """
    try:
        with open(CURRENT_BALANCE_FILE, 'w') as f:
            f.write(f"{balance}")
        logger.debug(f"Current balance {balance} saved to file.")
    except Exception as e:
        logger.error(f"Error saving current balance: {e}")
        logger.debug(traceback.format_exc())

def load_donations():
    """
    Load donations from the donations file into the donations list and set the total donations.
    """
    global donations, total_donations
    if os.path.exists(DONATIONS_FILE):
        try:
            with open(DONATIONS_FILE, 'r') as f:
                data = json.load(f)
                donations = data.get("donations", [])
                total_donations = data.get("total_donations", 0)
            logger.debug(f"{len(donations)} donations loaded from file.")
        except Exception as e:
            logger.error(f"Error loading donations: {e}")
            logger.debug(traceback.format_exc())

def save_donations():
    """
    Save donations to the donations file.
    """
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

# Initialize processed payments
processed_payments = load_processed_payments()

# Initialize Flask app
app = Flask(__name__)

# Global variables to store the latest data
latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

latest_payments = []

# Data structures for donations
donations = []
total_donations = 0

# Global variable to track the last update time
last_update = datetime.utcnow()

# Load existing donations at startup
load_donations()

# --------------------- Functions ---------------------

def fetch_api(endpoint):
    """
    Fetch data from the LNbits API.

    Args:
        endpoint (str): The API endpoint to fetch.

    Returns:
        dict or None: The JSON data from the API response, or None if an error occurs.
    """
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
    """
    Fetch Pay Links from the LNbits LNURLp Extension API.

    Returns:
        list or None: A list of pay links, or None if an error occurs.
    """
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
    Fetch LNURLp information for a given lnurlp_id.

    Args:
        lnurlp_id (str): The LNURLp ID.

    Returns:
        dict or None: The pay link information, or None if not found.
    """
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
    Fetch LNURLp information and integrate Lightning Address and LNURL into donation details.

    Returns:
        dict: A dictionary containing total donations, donation list, Lightning Address, and LNURL.
    """
    lnurlp_info = get_lnurlp_info(LNURLP_ID)
    if lnurlp_info is None:
        logger.error("Could not fetch LNURLp information for donation details.")
        return {
            "total_donations": total_donations,
            "donations": donations,
            "lightning_address": "Not available",
            "lnurl": "Not available"
        }

    # Extract username and construct Lightning Address
    username = lnurlp_info.get('username')  # Adjust key based on your LNURLp response
    if not username:
        username = "Unknown"
        logger.warning("Username not found in LNURLp info.")

    # Construct full Lightning Address
    lightning_address = f"{username}@{LNBITS_DOMAIN}"

    # Extract LNURL
    lnurl = lnurlp_info.get('lnurl', '')
    if not lnurl:
        logger.warning("LNURL not found in LNURLp info.")

    logger.debug(f"Constructed Lightning Address: {lightning_address}")
    logger.debug(f"Fetched LNURL: {lnurl}")

    return {
        "total_donations": total_donations,
        "donations": donations,
        "lightning_address": lightning_address,
        "lnurl": lnurl
    }

def update_donations_with_details(data):
    """
    Update donation data with additional details like Lightning Address and LNURL.

    Args:
        data (dict): The original donation data.

    Returns:
        dict: Updated donation data with additional details.
    """
    donation_details = fetch_donation_details()
    data.update({
        "lightning_address": donation_details.get("lightning_address"),
        "lnurl": donation_details.get("lnurl")
    })
    return data

def updateDonations(data):
    """
    Update donations and related UI elements with new data.

    This function has been expanded to integrate Lightning Address and LNURL into the data sent to the frontend.

    Args:
        data (dict): The data containing total donations and donation list.
    """
    # Integrate additional donation details
    updated_data = update_donations_with_details(data)

    totalDonations = updated_data["total_donations"]
    # Update latest donation
    if updated_data["donations"]:
        latestDonation = updated_data["donations"][-1]
        logger.info(f'Latest donation: {latestDonation["amount"]} sats - "{latestDonation["memo"]}"')
    else:
        logger.info('Latest donation: None yet.')

    # Update Lightning Address and LNURL
    logger.debug(f"Lightning Address: {updated_data.get('lightning_address')}")
    logger.debug(f"LNURL: {updated_data.get('lnurl')}")

    # Save updated donation data
    save_donations()

# Global dictionary to track transaction pages per chat
transaction_pages = {}

def send_latest_payments():
    """
    Fetch the latest payments and send a notification via Telegram.
    Additionally, check payments to determine if they qualify as donations.
    """
    global total_donations, donations, last_update  # Declare global variables
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
    new_processed_hashes = []

    for payment in latest:
        payment_hash = payment.get("payment_hash")
        if payment_hash in processed_payments:
            continue  # Skip already processed payments

        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo"))
        status = payment.get("status", "completed")

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0

        if status.lower() == "pending":
            continue  # Exclude pending transactions

        if amount_msat > 0:
            incoming_payments.append({
                "amount": amount_sats,
                "memo": memo,
                "date": payment.get("created_at")
            })
        elif amount_msat < 0:
            outgoing_payments.append({
                "amount": amount_sats,
                "memo": memo,
                "date": payment.get("created_at")
            })

        # Check for donation via LNURLp ID
        extra_data = payment.get("extra", {})
        lnurlp_id_payment = extra_data.get("link")
        if lnurlp_id_payment == LNURLP_ID:
            # It's a donation
            donation_memo = sanitize_memo(extra_data.get("comment", "No memo"))
            # Ensure 'extra' is a numeric value and in msats
            try:
                donation_amount_msat = int(extra_data.get("extra", 0))
                donation_amount_sats = donation_amount_msat / 1000  # Convert msats to sats
            except (ValueError, TypeError):
                donation_amount_sats = amount_sats  # Fallback if 'extra' is not numeric
            donation = {
                "date": datetime.utcnow().isoformat(),
                "memo": donation_memo,
                "amount": donation_amount_sats
            }
            donations.append(donation)
            total_donations += donation_amount_sats
            last_update = datetime.utcnow()
            logger.info(f"New donation detected: {donation_amount_sats} sats - {donation_memo}")
            updateDonations({
                "total_donations": total_donations,
                "donations": donations
            })  # Update donations with details

        # Mark payment as processed
        processed_payments.add(payment_hash)
        new_processed_hashes.append(payment_hash)
        add_processed_payment(payment_hash)

    if not incoming_payments and not outgoing_payments:
        logger.info("No new incoming or outgoing payments to notify.")
        return

    message_lines = [
        f"💰 *{INSTANCE_NAME}* - *Latest Transactions* 💰\n"
    ]

    for payment in incoming_payments + outgoing_payments:
        emoji = "🟢" if payment in incoming_payments else "🔴"
        date_str = payment.get("date", datetime.utcnow().isoformat())
        try:
            date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                date = datetime.utcnow()
        formatted_date = date.strftime("%b %d, %Y %H:%M")  # Improved readability

        amount = payment["amount"]
        sign = "+" if emoji == "🟢" else "-"
        message_lines.append(f"{emoji} {formatted_date} {sign}{amount} sat")
        message_lines.append(f"✉️ {payment['memo']}")

    # Append timestamp
    timestamp_text = f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    message_lines.append(timestamp_text)

    full_message = "\n".join(message_lines)

    # Define the inline keyboard with a static Balance button on top and smaller buttons below
    inline_keyboard = [
        # Static "Balance" button
        [InlineKeyboardButton("💰 Balance 170 sat", callback_data='balance')],
        # Smaller buttons in a single row
        [
            InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline'),
            InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline'),
            InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline'),
            InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')
        ]
    ]

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

    # Define the Reply Keyboard
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    # Send the transactions message with the inline keyboard
    try:
        bot.send_message(
            chat_id=CHAT_ID,
            text=full_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_reply_markup
        )
        logger.info("Latest payments notification successfully sent to Telegram.")
    except Exception as telegram_error:
        logger.error(f"Error sending transactions message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def check_balance_change():
    """
    Periodically check the wallet balance and notify if it has changed beyond the threshold.
    """
    global last_update
    logger.info("Checking for balance changes...")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    last_balance = load_last_balance()

    if last_balance is None:
        # First run, initialize balance file
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

    # Determine emoji based on direction
    emoji = "🟢" if change_amount > 0 else "🔴"

    # Prepare the message
    message = (
        f"⚡ *{INSTANCE_NAME}* - *Balance Update* ⚡\n\n"
        f"🔹 *Last Balance:* {int(last_balance)} sats\n"
        f"🔹 *Change:* {emoji}{int(abs_change)} sats\n"
        f"🔹 *New Balance:* {int(current_balance_sats)} sats\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define the inline keyboard with a static Balance button on top and smaller buttons below
    inline_keyboard = [
        # Static "Balance" button
        [InlineKeyboardButton("💰 Balance 170 sat", callback_data='balance')],
        # Smaller buttons in a single row
        [
            InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline'),
            InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline'),
            InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline'),
            InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')
        ]
    ]

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

    # Define the Reply Keyboard
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    # Send the balance update message with the inline keyboard
    try:
        bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        logger.info(f"Balance changed from {last_balance:.0f} to {current_balance_sats:.0f} sats. Notification sent.")
        # Update balance file and latest_balance
        save_current_balance(current_balance_sats)
        latest_balance["balance_sats"] = current_balance_sats
        latest_balance["last_change"] = f"Balance {direction} by {int(abs_change)} sats."
        latest_balance["memo"] = "N/A"
    except Exception as telegram_error:
        logger.error(f"Error sending balance update message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_main_inline_keyboard():
    """
    Send an inline keyboard with a static Balance button at the top and smaller buttons below.
    """
    inline_keyboard = [
        # Static "Balance" button
        [InlineKeyboardButton("💰 Balance 170 sat", callback_data='balance')],
        # Smaller buttons in a single row
        [
            InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline'),
            InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline'),
            InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline'),
            InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')
        ]
    ]

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

    try:
        bot.send_message(
            chat_id=CHAT_ID,
            text="",  # Removed "Main Menu" text
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_reply_markup
        )
        logger.info("Main inline keyboard with static Balance button sent successfully.")
    except Exception as telegram_error:
        logger.error(f"Error sending the main inline keyboard: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_balance_command(update, context):
    """
    Handle the /balance command sent by the user.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling /balance command for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        update.message.reply_text("❌ Error fetching balance.")
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    # Message with the balance
    balance_text = f"💰 *Balance:* {int(current_balance_sats)} sats"

    # Define the main reply keyboard with four buttons in a row
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    # Define the reply keyboard
    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    # Send the balance message with the main reply keyboard
    try:
        update.message.reply_text(balance_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Error sending /balance message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_overwatch(update, context):
    """
    Handle the '📊 Overwatch' button click.
    Send an inline keyboard with the Overwatch URL.
    """
    if OVERWATCH_URL:
        inline_keyboard = [
            [InlineKeyboardButton("Open Overwatch", url=OVERWATCH_URL)]
        ]
        inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
        update.message.reply_text("🔗 *Overwatch Details:*", parse_mode=ParseMode.MARKDOWN, reply_markup=inline_reply_markup)
    else:
        update.message.reply_text("❌ Overwatch URL is not configured.")

def handle_live_ticker(update, context):
    """
    Handle the '📡 Live Ticker' button click.
    Send an inline keyboard with the Live Ticker URL.
    """
    if DONATIONS_URL:
        inline_keyboard = [
            [InlineKeyboardButton("Open Live Ticker", url=DONATIONS_URL)]
        ]
        inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
        update.message.reply_text("🔗 *Live Ticker Details:*", parse_mode=ParseMode.MARKDOWN, reply_markup=inline_reply_markup)
    else:
        update.message.reply_text("❌ Live Ticker URL is not configured.")

def handle_latest_transactions(update, context):
    """
    Handle the '📜 Latest Transactions' button click.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling '📜 Latest Transactions' for chat_id: {chat_id}")
    send_transactions_page(chat_id, page=1)

def handle_lnbits(update, context):
    """
    Handle the '⚡ LNBits' button click.
    """
    if LNBITS_URL:
        inline_keyboard = [
            [InlineKeyboardButton("Open LNBits", url=LNBITS_URL)]
        ]
        inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
        update.message.reply_text("🔗 *LNBits Details:*", parse_mode=ParseMode.MARKDOWN, reply_markup=inline_reply_markup)
    else:
        update.message.reply_text("❌ LNBits URL is not configured.")

def handle_transactions_command(update, context):
    """
    Handle the /transactions command sent by the user.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling /transactions command for chat_id: {chat_id}")
    send_transactions_page(chat_id, page=1)

def send_transactions_page(chat_id, page=1):
    """
    Send a page of transactions to the user with only pagination arrows.

    Args:
        chat_id (int): The chat ID to send the message to.
        page (int): The page number to send.
    """
    payments = fetch_api("payments")
    if payments is None:
        bot.send_message(chat_id=chat_id, text="❌ Error fetching transactions.")
        return

    # Filter out pending transactions
    filtered_payments = [p for p in payments if p.get("status", "").lower() != "pending"]

    # Sort transactions by creation time descending
    sorted_payments = sorted(filtered_payments, key=lambda x: x.get("created_at", ""), reverse=True)

    total_transactions = len(sorted_payments)
    transactions_per_page = 13
    total_pages = (total_transactions + transactions_per_page - 1) // transactions_per_page  # Round up

    if page < 1 or page > total_pages:
        bot.send_message(chat_id=chat_id, text="❌ Invalid page.")
        return

    start_index = (page - 1) * transactions_per_page
    end_index = start_index + transactions_per_page
    page_transactions = sorted_payments[start_index:end_index]

    if not page_transactions:
        bot.send_message(chat_id=chat_id, text="❌ No transactions on this page.")
        return

    message_lines = [
        f"📜 *Latest Transactions - Page {page}/{total_pages}* 📜\n"
    ]

    for payment in page_transactions:
        amount_msat = payment.get("amount", 0)
        memo = sanitize_memo(payment.get("memo", "No memo"))
        status = payment.get("status", "completed")
        created_at = payment.get("created_at", datetime.utcnow().isoformat())

        try:
            date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            try:
                date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                date = datetime.utcnow()
        formatted_date = date.strftime("%b %d, %Y %H:%M")  # Improved readability

        amount_sats = int(abs(amount_msat) / 1000)
        sign = "+" if amount_msat > 0 else "-"
        emoji = "🟢" if amount_msat > 0 else "🔴"

        message_lines.append(f"{emoji} {formatted_date} {sign}{amount_sats} sat")
        message_lines.append(f"✉️ {memo}")

    # Combine transaction message
    full_message = "\n".join(message_lines)

    # Define the inline keyboard with only pagination arrows
    inline_keyboard = []

    # Pagination buttons
    if total_pages > 1:
        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'prev_{page}'))
        if page < total_pages:
            buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f'next_{page}'))
        inline_keyboard.append(buttons)

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard) if inline_keyboard else None

    # Define the reply keyboard (optional, keep it if you want users to access other features via keyboard)
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    # Send the transactions message with the inline keyboard containing only pagination arrows
    try:
        bot.send_message(
            chat_id=chat_id,
            text=full_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_reply_markup
        )
        logger.info(f"Latest transactions page {page} sent to Telegram.")
    except Exception as telegram_error:
        logger.error(f"Error sending transactions message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_prev_page(update, context):
    """
    Handle the '⬅️ Previous' button click for transactions.
    """
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat.id
    data = query.data
    match = re.match(r'prev_(\d+)', data)
    if match:
        current_page = int(match.group(1))
        new_page = current_page - 1
        if new_page < 1:
            new_page = 1
        send_transactions_page(chat_id, page=new_page)
    query.answer()

def handle_next_page(update, context):
    """
    Handle the '➡️ Next' button click for transactions.
    """
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat.id
    data = query.data
    match = re.match(r'next_(\d+)', data)
    if match:
        current_page = int(match.group(1))
        new_page = current_page + 1
        send_transactions_page(chat_id, page=new_page)
    query.answer()

def handle_balance_callback(query):
    """
    Handle the 'balance' callback query.

    Sends the balance message.
    """
    try:
        wallet_info = fetch_api("wallet")
        if wallet_info is None:
            query.message.reply_text("❌ Error fetching balance.")
            return
        current_balance_msat = wallet_info.get("balance", 0)
        current_balance_sats = current_balance_msat / 1000  # Convert msats to sats
        balance_message = f"💰 *Balance:* {int(current_balance_sats)} sats"

        # Define the inline keyboard with a static Balance button on top and smaller buttons below
        inline_keyboard = [
            # Static "Balance" button
            [InlineKeyboardButton("💰 Balance 170 sat", callback_data='balance')],
            # Smaller buttons in a single row
            [
                InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline'),
                InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline'),
                InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline'),
                InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')
            ]
        ]

        inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

        # Define the reply keyboard
        main_keyboard = [
            "📊 Overwatch",
            "📡 Live Ticker",
            "📜 Latest Transactions",
            "⚡ LNBits"
        ]

        reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

        try:
            query.message.reply_text(balance_message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            logger.info(f"Balance message sent via callback.")
        except Exception as telegram_error:
            logger.error(f"Error sending balance message via callback: {telegram_error}")
            logger.debug(traceback.format_exc())
    except Exception as e:
        logger.error(f"Error handling balance callback: {e}")
        logger.debug(traceback.format_exc())

def handle_transactions_callback(update, context):
    """
    Handle callback queries for transaction pagination and balance display.
    """
    query = update.callback_query
    if not query:
        return
    data = query.data
    if data.startswith('prev_'):
        page = int(data.split('_')[1]) - 1
        if page < 1:
            page = 1
        send_transactions_page(query.message.chat.id, page=page)
    elif data.startswith('next_'):
        page = int(data.split('_')[1]) + 1
        send_transactions_page(query.message.chat.id, page=page)
    elif data == 'balance':
        # Handle balance display
        handle_balance_callback(query)
    else:
        # For any other inline buttons (if any exist elsewhere), handle accordingly
        bot.answer_callback_query(callback_query_id=query.id, text="❓ Unknown action.")
    query.answer()

def handle_info_command(update, context):
    """
    Handle the /info command sent by the user.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling /info command for chat_id: {chat_id}")
    # Prepare interval information
    interval_info = (
        f"🔔 *Balance Change Threshold:* {BALANCE_CHANGE_THRESHOLD} sats\n"
        f"🔔 *Highlight Threshold:* {HIGHLIGHT_THRESHOLD} sats\n"
        f"⏲️ *Balance Change Monitoring Interval:* Every {WALLET_INFO_UPDATE_INTERVAL} seconds\n"
        f"📊 *Daily Balance Notification Interval:* Every {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds\n"
        f"🔄 *Latest Payments Fetch Interval:* Every {PAYMENTS_FETCH_INTERVAL} seconds"
    )

    info_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Information*\n\n"
        f"{interval_info}"
    )

    # Define the main reply keyboard with four buttons in a row
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    # Define the reply keyboard
    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    try:
        update.message.reply_text(info_message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Error sending /info message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_help_command(update, context):
    """
    Handle the /help command sent by the user.
    """
    chat_id = update.effective_chat.id
    logger.info(f"Handling /help command for chat_id: {chat_id}")
    help_message = (
        f"ℹ️ *{INSTANCE_NAME}* - *Help*\n\n"
        f"Available commands:\n"
        f"• `/balance` – Shows the current balance.\n"
        f"• `/transactions` – Shows the latest transactions.\n"
        f"• `/info` – Provides information about the monitor and current settings.\n"
        f"• `/help` – Shows this help message."
    )

    # Define the main reply keyboard with four buttons in a row
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    # Define the reply keyboard
    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    try:
        update.message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as telegram_error:
        logger.error(f"Error sending /help message to Telegram: {telegram_error}")
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
                handle_balance_command(message, None)
            elif text.startswith('/transactions'):
                handle_transactions_command(message, None)
            elif text.startswith('/info'):
                handle_info_command(message, None)
            elif text.startswith('/help'):
                handle_help_command(message, None)
            else:
                # Handle Custom Keyboard Buttons
                if text == "📊 Overwatch":
                    handle_overwatch(message, None)
                elif text == "📡 Live Ticker":
                    handle_live_ticker(message, None)
                elif text == "📜 Latest Transactions":
                    handle_latest_transactions(message, None)
                elif text == "⚡ LNBits":
                    handle_lnbits(message, None)
                else:
                    bot.send_message(
                        chat_id=chat_id,
                        text="❓ Unknown command or option. Available options: 📊 Overwatch, 📡 Live Ticker, 📜 Latest Transactions, ⚡ LNBits"
                    )
        elif 'callback_query' in update:
            process_callback_query(update['callback_query'])
        else:
            logger.info("Update contains neither a message nor a callback query. Ignored.")
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

        if data.startswith('prev_') or data.startswith('next_') or data == 'balance' or data.endswith('_inline'):
            # Handle pagination or inline buttons
            handle_transactions_callback(callback_query)
        else:
            bot.answer_callback_query(callback_query_id=query_id, text="❓ Unknown action.")
    except Exception as e:
        logger.error(f"Error processing callback query: {e}")
        logger.debug(traceback.format_exc())

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
        logger.info("Balance change monitoring disabled (WALLET_INFO_UPDATE_INTERVAL set to 0).")

    if WALLET_BALANCE_NOTIFICATION_INTERVAL > 0:
        scheduler.add_job(
            send_wallet_balance,
            'interval',
            seconds=WALLET_BALANCE_NOTIFICATION_INTERVAL,
            id='wallet_balance_notification',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Daily balance notification scheduled every {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds.")
    else:
        logger.info("Daily balance notification disabled (WALLET_BALANCE_NOTIFICATION_INTERVAL set to 0).")

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
        logger.info("Latest payments fetch notification disabled (PAYMENTS_FETCH_INTERVAL set to 0).")

    scheduler.start()
    logger.info("Scheduler successfully started.")

def send_wallet_balance():
    """
    Send the current wallet balance daily via Telegram in a professional and clear format.
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
                continue  # Exclude pending transactions from daily balance
            if amount_msat > 0:
                incoming_count += 1
                incoming_total += amount_msat / 1000
            elif amount_msat < 0:
                outgoing_count += 1
                outgoing_total += abs(amount_msat) / 1000

    # Prepare the Telegram message with Markdown formatting
    message = (
        f"📊 *{INSTANCE_NAME}* - *Daily Balance* 📊\n\n"
        f"🔹 *Current Balance:* {int(current_balance_sats)} sats\n"
        f"🔹 *Total Incoming:* {int(incoming_total)} sats over {incoming_count} transactions\n"
        f"🔹 *Total Outgoing:* {int(outgoing_total)} sats over {outgoing_count} transactions\n\n"
        f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    # Define the inline keyboard with a static Balance button on top and smaller buttons below
    inline_keyboard = [
        # Static "Balance" button
        [InlineKeyboardButton("💰 Balance 170 sat", callback_data='balance')],
        # Smaller buttons in a single row
        [
            InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline'),
            InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline'),
            InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline'),
            InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')
        ]
    ]

    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

    # Define the reply keyboard
    main_keyboard = [
        "📊 Overwatch",
        "📡 Live Ticker",
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    reply_markup = ReplyKeyboardMarkup([main_keyboard], resize_keyboard=True, one_time_keyboard=False)

    # Send the message to Telegram with the custom keyboard
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        logger.info("Daily wallet balance notification successfully sent.")
        # Update the latest balance
        latest_balance["balance_sats"] = current_balance_sats
        latest_balance["last_change"] = "Daily balance report."
        latest_balance["memo"] = "N/A"
        # Save the current balance
        save_current_balance(current_balance_sats)
    except Exception as telegram_error:
        logger.error(f"Error sending daily wallet balance message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

# --------------------- Flask Routes ---------------------

@app.route('/')
def home():
    return "🔍 LNbits Monitor is running."

@app.route('/status', methods=['GET'])
def status():
    """
    Returns the application's status, including the latest balance, payments, total donations, donations, Lightning Address, and LNURL.
    """
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
        logger.warning("Empty update received.")
        return "No update found", 400

    logger.debug(f"Update received: {update}")

    # Process the message in a separate thread to avoid blocking
    threading.Thread(target=process_update, args=(update,)).start()

    return "OK", 200

@app.route('/donations')
def donations_page():
    # Fetch LNURLp info
    lnurlp_id = LNURLP_ID
    lnurlp_info = get_lnurlp_info(lnurlp_id)
    if lnurlp_info is None:
        return "Error fetching LNURLP info", 500

    # Extract necessary information
    wallet_name = lnurlp_info.get('description', 'Unknown Wallet')
    lightning_address = lnurlp_info.get('lightning_address', 'Unknown Lightning Address')  # Adjust key based on your data structure
    lnurl = lnurlp_info.get('lnurl', '')

    # Generate QR code from LNURL
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(lnurl)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Convert PIL image to base64 string for embedding in HTML
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    img_base64 = base64.b64encode(img_io.getvalue()).decode()

    # Calculate total donations for this LNURLp
    total_donations_current = sum(donation['amount'] for donation in donations)

    # Pass the donations list and additional details to the template for displaying individual transactions
    return render_template(
        'donations.html',
        wallet_name=wallet_name,
        lightning_address=lightning_address,
        lnurl=lnurl,
        qr_code_data=img_base64,
        donations_url=DONATIONS_URL,  # Pass the donations URL to the template
        information_url=INFORMATION_URL,  # Pass the information URL to the template
        total_donations=total_donations_current,  # Pass the total donations
        donations=donations,  # Pass the donations list
        highlight_threshold=HIGHLIGHT_THRESHOLD  # Pass the highlight threshold
    )

# API Endpoint to provide donation data
@app.route('/api/donations', methods=['GET'])
def get_donations_data():
    """
    Provides donation data as JSON for the frontend, including Lightning Address, LNURL, and Highlight Threshold.
    """
    try:
        donation_details = fetch_donation_details()
        data = {
            "total_donations": donation_details["total_donations"],
            "donations": donation_details["donations"],
            "lightning_address": donation_details["lightning_address"],
            "lnurl": donation_details["lnurl"],
            "highlight_threshold": HIGHLIGHT_THRESHOLD
        }
        logger.debug(f"Donation data with details provided: {data}")
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error fetching donation data: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching donation data"}), 500

# Endpoint for Long-Polling Updates
@app.route('/donations_updates', methods=['GET'])
def donations_updates():
    """
    Endpoint for clients to check the timestamp of the latest donation update.
    """
    global last_update
    try:
        return jsonify({"last_update": last_update.isoformat()}), 200
    except Exception as e:
        logger.error(f"Error fetching last_update: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching last_update"}), 500

# --------------------- Telegram Handler Setup ---------------------

def main():
    """
    Main function to set up Telegram handlers and start the bot.
    """
    # Initialize Updater and Dispatcher
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # CommandHandler for /balance
    dispatcher.add_handler(CommandHandler('balance', handle_balance_command))

    # CommandHandler for /transactions
    dispatcher.add_handler(CommandHandler('transactions', handle_transactions_command))

    # CommandHandler for /info
    dispatcher.add_handler(CommandHandler('info', handle_info_command))

    # CommandHandler for /help
    dispatcher.add_handler(CommandHandler('help', handle_help_command))

    # MessageHandlers for Custom Keyboard Buttons
    dispatcher.add_handler(MessageHandler(Filters.regex('^📊 Overwatch$'), handle_overwatch))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📡 Live Ticker$'), handle_live_ticker))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📜 Latest Transactions$'), handle_latest_transactions))
    dispatcher.add_handler(MessageHandler(Filters.regex('^⚡ LNBits$'), handle_lnbits))

    # CallbackQueryHandler for Inline Buttons (Pagination and Inline Buttons)
    dispatcher.add_handler(CallbackQueryHandler(handle_transactions_callback, pattern='^(prev|next)_\d+$|^balance$|^.*_inline$'))

    # Start the Bot
    updater.start_polling()
    logger.info("Telegram Bot started successfully.")

    # Send the main inline keyboard when the bot starts
    send_main_inline_keyboard()

    # Idle to keep the bot running until interrupted
    updater.idle()

# --------------------- Application Entry Point ---------------------

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Balance Monitor.")

    # Log current configuration
    logger.info(f"🔔 Balance Change Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"🔔 Highlight Threshold: {HIGHLIGHT_THRESHOLD} sats")
    logger.info(f"📊 Fetching the latest {LATEST_TRANSACTIONS_COUNT} transactions for notifications")
    logger.info(f"⏲️ Scheduler Intervals - Balance Change Monitoring: {WALLET_INFO_UPDATE_INTERVAL} seconds, Daily Balance Notification: {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds, Latest Payments Fetch: {PAYMENTS_FETCH_INTERVAL} seconds")

    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Start the Flask app in a separate thread
    flask_thread = threading.Thread(target=lambda: app.run(host=APP_HOST, port=APP_PORT), daemon=True)
    flask_thread.start()
    logger.info(f"Flask Server running at {APP_HOST}:{APP_PORT}")

    # Start the Telegram handlers
    main()
