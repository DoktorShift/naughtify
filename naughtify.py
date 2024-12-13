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
DONATIONS_URL = os.getenv("DONATIONS_URL")  # Optional
LNURLP_ID = os.getenv("LNURLP_ID") if DONATIONS_URL else None  # Only required if DONATIONS_URL is set

# Forbidden Words Configuration
FORBIDDEN_WORDS_FILE = os.getenv("FORBIDDEN_WORDS_FILE", "forbidden_words.txt")

# Notification Settings
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "10"))  # Default: 10 sats
HIGHLIGHT_THRESHOLD = int(os.getenv("HIGHLIGHT_THRESHOLD", "2100"))  # Default: 2100 sats
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))  # Default: 21 transactions

# Scheduler Intervals (in seconds)
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "60"))  # Default: 60 seconds (1 minute)

# Flask Server Configuration
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")  # Default: localhost
APP_PORT = int(os.getenv("APP_PORT", "5009"))  # Default: Port 5009

# File Paths
PROCESSED_PAYMENTS_FILE = os.getenv("PROCESSED_PAYMENTS_FILE", "processed_payments.txt")
CURRENT_BALANCE_FILE = os.getenv("CURRENT_BALANCE_FILE", "current-balance.txt")
DONATIONS_FILE = os.getenv("DONATIONS_FILE", "donations.json")

# Information URL Configuration
INFORMATION_URL = os.getenv("INFORMATION_URL")  # Optional

# Validate essential environment variables
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise EnvironmentError(f"Essential environment variables missing: {', '.join(missing_vars)}")

# If DONATIONS_URL is provided, LNURLP_ID must also be set
if DONATIONS_URL and not LNURLP_ID:
    raise EnvironmentError("LNURLP_ID must be set when DONATIONS_URL is provided.")

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

def get_main_inline_keyboard():
    """
    Creates the main inline keyboard with three rows:
    1. Balance button.
    2. Latest Transactions and Live Ticker buttons.
    3. Overwatch and LNBits buttons.

    Returns:
        InlineKeyboardMarkup: The configured inline keyboard.
    """
    # First Row: Balance Button
    balance_button = InlineKeyboardButton("💰 Balance", callback_data='balance')

    # Second Row: Latest Transactions and Live Ticker
    latest_transactions_button = InlineKeyboardButton("📜 Latest Transactions", callback_data='transactions_inline')
    if DONATIONS_URL:
        live_ticker_button = InlineKeyboardButton("📡 Live Ticker", url=DONATIONS_URL)
    else:
        live_ticker_button = InlineKeyboardButton("📡 Live Ticker", callback_data='liveticker_inline')

    # Third Row: Overwatch and LNBits
    if OVERWATCH_URL:
        overwatch_button = InlineKeyboardButton("📊 Overwatch", url=OVERWATCH_URL)
    else:
        overwatch_button = InlineKeyboardButton("📊 Overwatch", callback_data='overwatch_inline')

    if LNBITS_URL:
        lnbits_button = InlineKeyboardButton("⚡ LNBits", url=LNBITS_URL)
    else:
        lnbits_button = InlineKeyboardButton("⚡ LNBits", callback_data='lnbits_inline')

    # Assemble the keyboard
    inline_keyboard = [
        [balance_button],  # First row
        [latest_transactions_button, live_ticker_button],  # Second row
        [overwatch_button, lnbits_button]  # Third row
    ]

    return InlineKeyboardMarkup(inline_keyboard)

def get_main_keyboard():
    """
    Creates the main reply keyboard with buttons arranged in 1:2:2 layout.

    Returns:
        ReplyKeyboardMarkup: The configured reply keyboard.
    """
    # Buttons for the keyboard
    balance_button = ["💰 Balance"]  # First row: One button

    # The main options arranged in two rows of two buttons each
    main_options_row_1 = [
        "📊 Overwatch",
        "📡 Live Ticker"
    ]

    main_options_row_2 = [
        "📜 Latest Transactions",
        "⚡ LNBits"
    ]

    # Assemble the keyboard
    keyboard = [
        balance_button,        # First row: Single button
        main_options_row_1,    # Second row: Two buttons
        main_options_row_2     # Third row: Two buttons
    ]

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

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
                    forbidden.add(word)
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
    Sanitize a memo by replacing forbidden words with asterisks.

    Args:
        memo (str): The original memo text.

    Returns:
        str: The sanitized memo text.
    """
    if not memo:
        return "No memo provided."

    # Function to replace the matched word with asterisks
    def replace_match(match):
        word = match.group()
        return '*' * len(word)

    # Create a regex pattern that matches any forbidden word (case-insensitive)
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

def load_donations():
    """
    Load donations from the donations file into the donations list and set the total donations.
    """
    global donations, total_donations
    if os.path.exists(DONATIONS_FILE) and DONATIONS_URL and LNURLP_ID:
        try:
            with open(DONATIONS_FILE, 'r') as f:
                data = json.load(f)
                donations = data.get("donations", [])
                total_donations = data.get("total_donations", 0)

                # Ensure each donation has id, likes, and dislikes
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
    """
    Save donations to the donations file.
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

# Load existing donations at startup if donations are enabled
load_donations()

def sanitize_donations():
    """
    Sanitize all existing donations to replace forbidden words in memos.
    """
    global donations
    try:
        for donation in donations:
            donation['memo'] = sanitize_memo(donation.get('memo', ''))

        # Save sanitized donations to file
        save_donations()
        logger.info("Donations sanitized and saved.")

        # Notify frontend about the update if necessary
        # This depends on how your frontend fetches updates. If it polls the /status endpoint, it's already covered.
    except Exception as e:
        logger.error(f"Error sanitizing donations: {e}")
        logger.debug(traceback.format_exc())

def handle_ticker_ban(update, context):
    """
    Handle the /ticker_ban command to add words to the forbidden words list.
    Args:
        update (telegram.Update): The update object.
        context (telegram.ext.CallbackContext): The callback context object.
    """
    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        bot.send_message(chat_id, text="❌ Please provide at least one word to ban. Example: /ticker_ban badword")
        return

    words_to_ban = [word.strip() for word in context.args if word.strip()]
    if not words_to_ban:
        bot.send_message(chat_id, text="❌ No valid words provided.")
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
                    FORBIDDEN_WORDS.add(word)  # Update the in-memory set
                    added_words.append(word)

        if added_words:
            logger.info(f"Added words to forbidden list: {added_words}")
            # Sanitize existing donations and save changes
            sanitize_donations()
            if len(added_words) == 1:
                success_message = f"✅ Successfully added '{added_words[0]}' to the banned list. It will be banned from Live-Ticker immediately."
            else:
                words_formatted = "', '".join(added_words)
                success_message = f"✅ Successfully added '{words_formatted}' to the banned list. They will be banned from Live-Ticker immediately."
            bot.send_message(chat_id, text=success_message)
        if duplicate_words:
            if len(duplicate_words) == 1:
                duplicate_message = f"⚠️ The word '{duplicate_words[0]}' is already in the banned list."
            else:
                words_formatted = "', '".join(duplicate_words)
                duplicate_message = f"⚠️ The words '{words_formatted}' are already in the banned list."
            bot.send_message(chat_id, text=duplicate_message)
    except Exception as e:
        logger.error(f"Error adding words to forbidden list: {e}")
        bot.send_message(chat_id, text="❌ An error occurred while banning words. Please try again.")

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
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations are not enabled. Skipping fetch_pay_links.")
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
    Fetch LNURLp information for a given lnurlp_id.

    Args:
        lnurlp_id (str): The LNURLp ID.

    Returns:
        dict or None: The pay link information, or None if not found.
    """
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations are not enabled. Skipping get_lnurlp_info.")
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
    Fetch LNURLp information and integrate Lightning Address and LNURL into donation details.

    Returns:
        dict: A dictionary containing total donations, donation list, Lightning Address, and LNURL.
    """
    if not DONATIONS_URL or not LNURLP_ID:
        logger.debug("Donations are not enabled. Skipping fetch_donation_details.")
        return {
            "total_donations": total_donations,
            "donations": donations,
            "lightning_address": "Not available",
            "lnurl": "Not available"
        }

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

def handle_vote_command(donation_id, vote_type):
    """
    Handle a vote (like or dislike) for a specific donation.

    Parameters:
        donation_id (str): The unique ID of the donation.
        vote_type (str): Either 'like' or 'dislike'.

    Returns:
        tuple: (dict, int) Result of the vote operation and HTTP status code.
    """
    try:
        # Find the corresponding donation
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
    """
    Parse the time field from payment data.

    Args:
        time_input (str or int or float): The time value to parse.

    Returns:
        datetime: The parsed datetime object.
    """
    if not time_input:
        logger.warning("No 'time' field found in payment, using current time as fallback.")
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
    """
    Fetch the latest payments and send notifications via Telegram.
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

    # Sort payments by 'time' descending
    sorted_payments = sorted(payments, key=lambda x: x.get("time", ""), reverse=True)
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
        memo = sanitize_memo(payment.get("memo", "No memo provided."))
        status = payment.get("status", "completed")
        time_str = payment.get("time", None)

        # Parse the 'time' field
        date = parse_time(time_str)
        formatted_date = date.strftime("%b %d, %Y %H:%M")  # Improved readability

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
                "date": formatted_date
            })
        elif amount_msat < 0:
            outgoing_payments.append({
                "amount": amount_sats,
                "memo": memo,
                "date": formatted_date
            })

        # Check for donation via LNURLp ID
        if DONATIONS_URL and LNURLP_ID:
            extra_data = payment.get("extra", {})
            lnurlp_id_payment = extra_data.get("link")
            if lnurlp_id_payment == LNURLP_ID:
                # It's a donation
                donation_memo = sanitize_memo(extra_data.get("comment", "No memo provided."))
                # Ensure 'extra' is a numeric value and in msats
                try:
                    donation_amount_msat = int(extra_data.get("extra", 0))
                    donation_amount_sats = donation_amount_msat / 1000  # Convert msats to sats
                except (ValueError, TypeError):
                    donation_amount_sats = amount_sats  # Fallback if 'extra' is not numeric
                donation = {
                    "id": str(uuid.uuid4()),  # Unique ID
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
                })  # Update donations with details

        # Mark payment as processed
        processed_payments.add(payment_hash)
        new_processed_hashes.append(payment_hash)
        add_processed_payment(payment_hash)

    # Send individual notifications for new incoming payments
    for payment in incoming_payments:
        notify_transaction(payment, "incoming")

    # Send individual notifications for new outgoing payments
    for payment in outgoing_payments:
        notify_transaction(payment, "outgoing")

    if not incoming_payments and not outgoing_payments:
        logger.info("No new incoming or outgoing payments to notify.")
        return

    # Removed summary notifications as per new design

def notify_transaction(payment, direction):
    """
    Send a notification message for a single transaction.

    Args:
        payment (dict): The payment details.
        direction (str): 'incoming' or 'outgoing'.
    """
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

        # Send the transaction notification message without inline keyboard
        bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN
            # No reply_markup to exclude buttons
        )
        logger.info(f"Notification for {transaction_type} sent successfully.")
    except Exception as e:
        logger.error(f"Error sending transaction notification: {e}")
        logger.debug(traceback.format_exc())

def send_main_inline_keyboard():
    """
    Send an inline keyboard with a long Balance button and other specified buttons arranged in rows.
    """
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
        logger.info("Main inline keyboard with updated welcome message successfully sent.")
    except Exception as telegram_error:
        logger.error(f"Error sending the main inline keyboard: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_start_message(update, context):
    """
    Handler for the /start command.
    Sends a welcome message along with the Reply-Keyboard.
    """
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
        logger.info(f"Start message and Reply-Keyboard sent to chat_id {chat_id}.")
    except Exception as e:
        logger.error(f"Error sending the start message: {e}")
        logger.debug(traceback.format_exc())

def send_balance_message(chat_id):
    """
    Fetch the current balance and send it to the specified chat.

    Args:
        chat_id (int): The chat ID to send the balance to.
    """
    logger.info(f"Fetching balance for chat_id: {chat_id}")
    wallet_info = fetch_api("wallet")
    if wallet_info is None:
        bot.send_message(chat_id, text="❌ Unable to fetch the balance at the moment. Please try again later.")
        return

    current_balance_msat = wallet_info.get("balance", 0)
    current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

    # Message with the balance
    balance_text = f"💰 *Current Balance:* {int(current_balance_sats)} sats"

    try:
        bot.send_message(
            chat_id=chat_id,
            text=balance_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()  # Include the main reply keyboard
        )
        logger.info(f"Balance message sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending balance message to chat_id {chat_id}: {telegram_error}")
        logger.debug(traceback.format_exc())

def send_transactions_message(chat_id, page=1, message_id=None):
    """
    Fetch and send a page of transactions to the specified chat.

    Args:
        chat_id (int): The chat ID to send the transactions to.
        page (int, optional): The page number to send. Defaults to 1.
        message_id (int, optional): The ID of the message to edit. If None, send a new message.
    """
    logger.info(f"Fetching transactions for chat_id: {chat_id}, page: {page}")
    payments = fetch_api("payments")
    if payments is None:
        bot.send_message(chat_id, text="❌ Unable to fetch transactions at the moment.")
        return

    # Filter out pending transactions
    filtered_payments = [p for p in payments if p.get("status", "").lower() != "pending"]

    # Sort transactions by 'time' descending
    sorted_payments = sorted(filtered_payments, key=lambda x: x.get("time", ""), reverse=True)

    total_transactions = len(sorted_payments)
    transactions_per_page = 13
    total_pages = (total_transactions + transactions_per_page - 1) // transactions_per_page  # Round up

    if total_pages == 0:
        total_pages = 1  # Ensure at least one page

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
        status = payment.get("status", "completed")
        time_str = payment.get("time", None)

        # Parse the 'time' field
        date = parse_time(time_str)
        formatted_date = date.strftime("%b %d, %Y %H:%M")  # Improved readability

        try:
            amount_sats = int(abs(amount_msat) / 1000)
        except ValueError:
            amount_sats = 0

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

    try:
        if message_id:
            # Edit the existing message
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=full_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=inline_reply_markup  # Only pagination buttons
            )
            logger.info(f"Transactions page {page} successfully edited for chat_id: {chat_id}")
        else:
            # Send a new message
            bot.send_message(
                chat_id=chat_id,
                text=full_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=inline_reply_markup  # Only pagination buttons
            )
            logger.info(f"Transactions page {page} successfully sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending or editing transactions message: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_prev_page(update, context):
    """
    Handle the '⬅️ Previous' button click for transactions.
    """
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
    """
    Handle the '➡️ Next' button click for transactions.
    """
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
    """
    Handle the 'balance' callback query.

    Sends the balance message without buttons.
    """
    try:
        chat_id = query.message.chat.id
        send_balance_message(chat_id)
    except Exception as e:
        logger.error(f"Error handling balance callback: {e}")
        logger.debug(traceback.format_exc())

def handle_transactions_inline_callback(query):
    """
    Handle the 'transactions_inline' callback query.

    Sends the transactions message.
    """
    try:
        chat_id = query.message.chat.id
        send_transactions_message(chat_id, page=1, message_id=query.message.message_id)
    except Exception as e:
        logger.error(f"Error handling transactions_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_donations_inline_callback(query):
    """
    Handle the 'overwatch_inline', 'liveticker_inline', and 'lnbits_inline' callbacks.

    Sends the respective URLs or feedback if not configured.
    """
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
                text="❌ URL is not configured. Please contact the administrator."
            )
    except Exception as e:
        logger.error(f"Error handling donations_inline callback: {e}")
        logger.debug(traceback.format_exc())

def handle_other_inline_callbacks(data, query):
    """
    Handle other inline button callbacks that might require custom handling.

    Args:
        data (str): The callback data.
        query (CallbackQuery): The callback query object.
    """
    # Placeholder for handling other inline callbacks if needed
    bot.answer_callback_query(callback_query_id=query.id, text="❓ Unknown action.")

def handle_transactions_callback(update, context):
    """
    General handler for transaction-related callbacks.

    Args:
        update (Update): The update object.
        context (CallbackContext): The context object.
    """
    query = update.callback_query
    data = query.data
    logger.debug(f"Handling callback data: {data}")

    if data == 'balance':
        handle_balance_callback(query)
    elif data == 'transactions_inline':
        handle_transactions_inline_callback(query)
    elif data.startswith('prev_') or data.startswith('next_'):
        # Pagination
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
        # Unknown callback_data
        handle_other_inline_callbacks(data, query)

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
            reply_markup=get_main_keyboard()  # Include the main reply keyboard
        )
        logger.info(f"Info message sent to chat_id: {chat_id}")
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
        f"Here are the commands you can use:\n"
        f"• /balance – View your current balance.\n"
        f"• /transactions – View your latest transactions.\n"
        f"• /info – View information about the bot and settings.\n"
        f"• /help – Show this help message.\n"
        f"• /ticker_ban – Add forbidden words to ban from Live-Ticker (you can enter multiple words separated by spaces).\n\n"
        f"You can also use the buttons below to navigate through the bot's features."
    )

    try:
        bot.send_message(
            chat_id=chat_id,
            text=help_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard()  # Include the main reply keyboard
        )
        logger.info(f"Help message sent to chat_id: {chat_id}")
    except Exception as telegram_error:
        logger.error(f"Error sending /help message to Telegram: {telegram_error}")
        logger.debug(traceback.format_exc())

def handle_balance(update, context):
    """
    Handle the '💰 Balance' button from the Reply Keyboard.
    """
    chat_id = update.effective_chat.id
    send_balance_message(chat_id)

def handle_latest_transactions(update, context):
    """
    Handle the '📜 Latest Transactions' button from the Reply Keyboard.
    """
    chat_id = update.effective_chat.id
    send_transactions_message(chat_id, page=1)

def handle_live_ticker(update, context):
    """
    Handle the '📡 Live Ticker' button from the Reply Keyboard.
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
        bot.send_message(chat_id=chat_id, text="❌ Live Ticker URL is not configured. Please contact the administrator.")

def handle_overwatch(update, context):
    """
    Handle the '📊 Overwatch' button from the Reply Keyboard.
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
        bot.send_message(chat_id=chat_id, text="❌ Overwatch URL is not configured. Please contact the administrator.")

def handle_lnbits(update, context):
    """
    Handle the '⚡ LNBits' button from the Reply Keyboard.
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
        bot.send_message(chat_id=chat_id, text="❌ LNBits URL is not configured. Please contact the administrator.")

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
                # Handle Reply Keyboard Buttons
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
                        text="❓ Unknown command or option. Please use /help to see available commands."
                    )
        elif 'callback_query' in update:
            # The CallbackQueryHandler will handle this
            pass
        else:
            logger.info("Update contains neither a message nor a callback query. Ignored.")
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        logger.debug(traceback.format_exc())

def start_scheduler():
    """
    Start the scheduler for periodic tasks using BackgroundScheduler.
    """
    scheduler = BackgroundScheduler(timezone='UTC')

    if PAYMENTS_FETCH_INTERVAL > 0:
        scheduler.add_job(
            send_latest_payments,
            'interval',
            seconds=PAYMENTS_FETCH_INTERVAL,
            id='latest_payments_fetch',
            next_run_time=datetime.utcnow() + timedelta(seconds=1)
        )
        logger.info(f"Latest Payments Fetch scheduled every {PAYMENTS_FETCH_INTERVAL / 60:.1f} minutes.")
    else:
        logger.info("Latest Payments Fetch Notification disabled (PAYMENTS_FETCH_INTERVAL set to 0).")

    # Start the scheduler
    scheduler.start()
    logger.info("Scheduler successfully started.")

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
        "donations": donation_details["donations"],  # Each donation includes id, likes, dislikes
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
    if not DONATIONS_URL or not LNURLP_ID:
        return "Donations are not enabled.", 404

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
    if DONATIONS_URL and LNURLP_ID:
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
    else:
        return jsonify({
            "error": "Donations are not enabled."
        }), 404

# API Endpoint to handle voting
@app.route('/api/vote', methods=['POST'])
def vote_donation():
    """
    API endpoint to handle likes and dislikes for a donation.
    Expects JSON with 'donation_id' and 'vote_type' ('like' or 'dislike').
    Utilizes cookies to prevent multiple votes by the same user on the same donation.
    """
    try:
        data = request.get_json()
        donation_id = data.get('donation_id')
        vote_type = data.get('vote_type')

        if not donation_id or not vote_type:
            return jsonify({"error": "donation_id and vote_type are required."}), 400

        if vote_type not in ['like', 'dislike']:
            return jsonify({"error": "vote_type must be 'like' or 'dislike'."}), 400

        # Retrieve existing voted donations from cookies to prevent multiple votes
        voted_donations = request.cookies.get('voted_donations', '')
        voted_set = set(voted_donations.split(',')) if voted_donations else set()

        # Check if user has already voted on this donation
        if donation_id in voted_set:
            return jsonify({"error": "You have already voted on this donation."}), 403

        # Handle the vote
        result, status_code = handle_vote_command(donation_id, vote_type)
        if status_code != 200:
            return jsonify(result), status_code

        # Prepare response with updated likes and dislikes
        response = make_response(jsonify(result), 200)

        # Update the voted_donations cookie
        voted_set.add(donation_id)
        new_voted_donations = ','.join(voted_set)
        response.set_cookie('voted_donations', new_voted_donations, max_age=60*60*24*365)  # 1 year expiration

        return response

    except Exception as e:
        logger.error(f"Error processing vote: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Internal server error."}), 500

# Endpoint for Long-Polling Updates
@app.route('/donations_updates', methods=['GET'])
def donations_updates():
    """
    Endpoint for clients to check the timestamp of the latest donation update.
    """
    global last_update
    if not DONATIONS_URL or not LNURLP_ID:
        return jsonify({"error": "Donations are not enabled."}), 404

    try:
        return jsonify({"last_update": last_update.isoformat()}), 200
    except Exception as e:
        logger.error(f"Error fetching last_update: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"error": "Error fetching last_update"}), 500

# NEW ROUTE FOR CINEMA MODE
@app.route('/cinema')
def cinema_page():
    if not DONATIONS_URL or not LNURLP_ID:
        return "Donations are not enabled for Cinema Mode.", 404
    return render_template('cinema.html')

# --------------------- Telegram Handler Setup ---------------------

def main():
    """
    Main function to set up Telegram handlers and start the bot.
    """
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # CommandHandler for /balance
    dispatcher.add_handler(CommandHandler('balance', lambda update, context: send_balance_message(update.effective_chat.id)))

    # CommandHandler for /transactions
    dispatcher.add_handler(CommandHandler('transactions', lambda update, context: send_transactions_message(update.effective_chat.id, page=1)))

    # CommandHandler for /info
    dispatcher.add_handler(CommandHandler('info', handle_info_command))

    # CommandHandler for /help
    dispatcher.add_handler(CommandHandler('help', handle_help_command))

    # CommandHandler for /start (send Reply Keyboard)
    dispatcher.add_handler(CommandHandler('start', send_start_message))

    # CommandHandler for /ticker_ban
    dispatcher.add_handler(CommandHandler('ticker_ban', handle_ticker_ban))

    # CallbackQueryHandler for Inline Buttons (Balance, Transactions, Overwatch, Live Ticker, LNBits, Pagination)
    dispatcher.add_handler(CallbackQueryHandler(handle_transactions_callback, pattern='^(balance|transactions_inline|prev_\\d+|next_\\d+|overwatch_inline|liveticker_inline|lnbits_inline)$'))

    # MessageHandler for Reply Keyboard Buttons
    dispatcher.add_handler(MessageHandler(Filters.regex('^💰 Balance$'), handle_balance))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📜 Latest Transactions$'), handle_latest_transactions))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📡 Live Ticker$'), handle_live_ticker))
    dispatcher.add_handler(MessageHandler(Filters.regex('^📊 Overwatch$'), handle_overwatch))
    dispatcher.add_handler(MessageHandler(Filters.regex('^⚡ LNBits$'), handle_lnbits))

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
    if PAYMENTS_FETCH_INTERVAL > 0:
        logger.info(f"⏲️ Scheduler Intervals - Latest Payments Fetch: Every {PAYMENTS_FETCH_INTERVAL / 60:.1f} minutes")
    else:
        logger.info("⏲️ Scheduler Intervals - Latest Payments Fetch: Disabled")

    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Start the Flask app in a separate thread
    flask_thread = threading.Thread(target=lambda: app.run(host=APP_HOST, port=APP_PORT), daemon=True)
    flask_thread.start()
    logger.info(f"Flask Server running at {APP_HOST}:{APP_PORT}")

    # Start the Telegram handlers
    main()
