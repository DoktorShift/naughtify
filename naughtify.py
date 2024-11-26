import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Bot
from dotenv import load_dotenv
import requests
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import utc
import threading
import time
from flask import Flask, jsonify
from datetime import datetime, timedelta

# --------------------- Configuration and Setup ---------------------

# Load environment variables from .env file
load_dotenv()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# LNbits Configuration
LNBITS_READONLY_API_KEY = os.getenv("LNBITS_READONLY_API_KEY")
LNBITS_URL = os.getenv("LNBITS_URL")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "LNbits Instance")

# Notification Settings
BALANCE_CHANGE_THRESHOLD = int(os.getenv("BALANCE_CHANGE_THRESHOLD", "1000"))  # Default: 1000 sats
LATEST_TRANSACTIONS_COUNT = int(os.getenv("LATEST_TRANSACTIONS_COUNT", "21"))    # Default: 21 transactions

# Scheduler Intervals (in seconds)
WALLET_INFO_UPDATE_INTERVAL = int(os.getenv("WALLET_INFO_UPDATE_INTERVAL", "60"))          # Default: 60 seconds
WALLET_BALANCE_NOTIFICATION_INTERVAL = int(os.getenv("WALLET_BALANCE_NOTIFICATION_INTERVAL", "86400"))  # Default: 24 hours (86400 seconds)
PAYMENTS_FETCH_INTERVAL = int(os.getenv("PAYMENTS_FETCH_INTERVAL", "86400"))                # Default: 24 hours (86400 seconds)

# Flask Server Configuration
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")    # Default: localhost
APP_PORT = int(os.getenv("APP_PORT", "5009"))   # Default: port 5009

# File Paths
PROCESSED_PAYMENTS_FILE = "processed_payments.txt"
CURRENT_BALANCE_FILE = "current-balance.txt"

# Initialize threading lock for thread-safe file operations
lock = threading.Lock()

# Validate essential environment variables
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "CHAT_ID": CHAT_ID,
    "LNBITS_READONLY_API_KEY": LNBITS_READONLY_API_KEY,
    "LNBITS_URL": LNBITS_URL
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
        with lock:
            with open(PROCESSED_PAYMENTS_FILE, "r") as f:
                for line in f:
                    processed.add(line.strip())
    return processed

def add_processed_payment(payment_hash):
    """
    Add a processed payment hash to the tracking file.
    """
    with lock:
        with open(PROCESSED_PAYMENTS_FILE, "a") as f:
            f.write(f"{payment_hash}\n")

def load_last_balance():
    """
    Load the last known balance from the balance file.
    """
    if not os.path.exists(CURRENT_BALANCE_FILE):
        logger.info("Balance file does not exist. Creating with current balance.")
        return None
    with lock:
        with open(CURRENT_BALANCE_FILE, 'r') as f:
            content = f.read().strip()
            if content == '':
                logger.warning("Balance file is empty. Setting last balance to 0.")
                return 0.0
            try:
                return float(content)
            except ValueError:
                logger.error(f"Invalid balance value in file: {content}. Setting last balance to 0.")
                return 0.0

def save_current_balance(balance):
    """
    Save the current balance to the balance file.
    """
    with lock:
        with open(CURRENT_BALANCE_FILE, 'w') as f:
            f.write(str(balance))

# Initialize the set of processed payments
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

# --------------------- Scheduled Tasks ---------------------

def send_latest_payments():
    """
    Fetch the latest payments and notify Telegram with incoming and outgoing transactions.
    """
    logger.info("Fetching latest payments for notification...")
    try:
        response = requests.get(
            f"{LNBITS_URL}/api/v1/payments",
            headers={"X-Api-Key": LNBITS_READONLY_API_KEY},
            timeout=10
        )
        if response.status_code == 200:
            payments = response.json()
            if not isinstance(payments, list):
                logger.error("Unexpected payments data format.")
                return

            # Sort payments by creation time descending
            sorted_payments = sorted(payments, key=lambda x: x.get("created_at", ""), reverse=True)
            latest = sorted_payments[:LATEST_TRANSACTIONS_COUNT]  # Get the latest transactions

            if not latest:
                logger.info("No payments found to notify.")
                return

            # Initialize lists for incoming and outgoing payments
            incoming_payments = []
            outgoing_payments = []

            new_processed_hashes = []

            for payment in latest:
                payment_hash = payment.get("payment_hash")
                if not payment_hash:
                    # Try to extract 'payment_hash' from 'extra.query' if not present
                    extra = payment.get("extra", {})
                    if isinstance(extra, dict):
                        payment_hash = extra.get("query", None)

                if not payment_hash:
                    logger.warning("Payment without payment_hash found. Skipping.")
                    continue

                if payment_hash in processed_payments:
                    logger.debug(f"Payment hash {payment_hash} already processed. Skipping.")
                    continue  # Skip already processed payments

                # Extract necessary fields
                amount_msat = payment.get("amount", 0)
                memo = payment.get("description", "No memo provided")
                status = payment.get("status", "completed")  # Default to 'completed' if not present

                # Convert amount to integer
                try:
                    amount_msat = int(amount_msat)
                except ValueError:
                    logger.error(f"Invalid amount value in payment: {amount_msat}")
                    amount_msat = 0

                amount_sats = abs(amount_msat) / 1000

                # Categorize payment
                if amount_msat > 0:
                    incoming_payments.append({
                        "amount": int(amount_sats),
                        "memo": memo
                    })
                elif amount_msat < 0:
                    outgoing_payments.append({
                        "amount": int(amount_sats),
                        "memo": memo
                    })
                else:
                    logger.warning("Payment with zero amount found. Skipping.")
                    continue

                # Mark this payment as processed
                processed_payments.add(payment_hash)
                add_processed_payment(payment_hash)
                new_processed_hashes.append(payment_hash)

            if not (incoming_payments or outgoing_payments):
                logger.info("No new incoming or outgoing payments to notify.")
                return

            # Prepare the Telegram message
            message_lines = [
                f"⚡ *{INSTANCE_NAME}* Latest Payments ⚡\n"
            ]

            if incoming_payments:
                message_lines.append("🟢 ⬇️ *Incoming:*")
                for payment in incoming_payments:
                    message_lines.append(f"• *Amount:* {payment['amount']} sats")
                    message_lines.append(f"• *Memo:* {payment['memo']}\n")

            if outgoing_payments:
                message_lines.append("🔴 ⬆️ *Outgoing:*")
                for payment in outgoing_payments:
                    message_lines.append(f"• *Amount:* {payment['amount']} sats")
                    message_lines.append(f"• *Memo:* {payment['memo']}\n")

            # Append the additional user-friendly message
            additional_text = f"Need more information? Login to your account at [{LNBITS_URL}]({LNBITS_URL})"
            message_lines.append(additional_text)

            full_message = "\n".join(message_lines)

            # Send the message to Telegram
            try:
                bot.send_message(chat_id=CHAT_ID, text=full_message, parse_mode='Markdown')
                logger.info("Latest payments notification sent to Telegram successfully.")
                latest_payments.extend(new_processed_hashes)
            except Exception as telegram_error:
                logger.error(f"Failed to send payments message to Telegram: {telegram_error}")
                logger.debug(traceback.format_exc())

        else:
            logger.error(f"Failed to fetch payments. Status Code: {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching payments: {e}")
        logger.debug(traceback.format_exc())

def send_wallet_balance():
    """
    Send the current wallet balance via Telegram in the specified format.
    """
    logger.info("Sending daily wallet balance notification...")
    try:
        # Fetch wallet info
        response = requests.get(
            f"{LNBITS_URL}/api/v1/wallet",
            headers={"X-Api-Key": LNBITS_READONLY_API_KEY},
            timeout=10
        )
        if response.status_code == 200:
            wallet_info = response.json()
            current_balance_msat = wallet_info.get("balance", 0)
            current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

            # Fetch payments to calculate counts and totals
            payments_response = requests.get(
                f"{LNBITS_URL}/api/v1/payments",
                headers={"X-Api-Key": LNBITS_READONLY_API_KEY},
                timeout=10
            )
            incoming_count = outgoing_count = 0
            incoming_total = outgoing_total = 0
            if payments_response.status_code == 200:
                payments = payments_response.json()
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

            # Prepare the Telegram message
            message = (
                f"📊 *{INSTANCE_NAME}* Daily Wallet Balance 📊\n\n"
                f"| Direction     | Count | Total Amount (sats) |\n"
                f"|---------------|-------|---------------------|\n"
                f"| 🟢 *Incoming* | {incoming_count}     | {int(incoming_total)}               |\n"
                f"| 🔴 *Outgoing* | {outgoing_count}     | {int(outgoing_total)}                |\n\n"
                f"• *Current Balance:* {int(current_balance_sats)} sats\n"
                f"• *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                f"Need more information? Login to your account at [{LNBITS_URL}]({LNBITS_URL})"
            )

            # Send the message to Telegram
            try:
                bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
                logger.info("Daily wallet balance notification sent successfully.")
                # Update the latest_balance
                latest_balance["balance_sats"] = current_balance_sats
                latest_balance["last_change"] = "Daily balance report."
                latest_balance["memo"] = "N/A"
            except Exception as telegram_error:
                logger.error(f"Failed to send daily wallet balance message to Telegram: {telegram_error}")
                logger.debug(traceback.format_exc())
        else:
            logger.error(f"Failed to fetch wallet info for daily balance. Status Code: {response.status_code}")
    except Exception as e:
        logger.error(f"Error sending daily wallet balance: {e}")
        logger.debug(traceback.format_exc())

def check_balance_change():
    """
    Periodically check the wallet balance and notify if it has changed beyond the threshold.
    """
    logger.info("Checking for balance changes...")
    try:
        response = requests.get(
            f"{LNBITS_URL}/api/v1/wallet",
            headers={"X-Api-Key": LNBITS_READONLY_API_KEY},
            timeout=10
        )
        if response.status_code == 200:
            wallet_info = response.json()
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

            # Prepare the Telegram message
            message = (
                f"⚡ *{INSTANCE_NAME}* Balance Update ⚡\n\n"
                f"🔹 *Previous Balance:* {last_balance:,} sats  \n"
                f"🔹 *Change:* {'+' if change_amount > 0 else '-'}{abs_change:,} sats  \n"
                f"🔹 *New Balance:* {current_balance_sats:,} sats  \n"
                f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                f"Need more information? Login to your account at [{LNBITS_URL}]({LNBITS_URL})"
            )

            # Send the message to Telegram
            try:
                bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
                logger.info(f"Balance changed from {last_balance:.0f} to {current_balance_sats:.0f} sats. Notification sent.")
            except Exception as telegram_error:
                logger.error(f"Failed to send balance change message to Telegram: {telegram_error}")
                logger.debug(traceback.format_exc())

            # Update the balance file and latest_balance
            save_current_balance(current_balance_sats)
            latest_balance["balance_sats"] = current_balance_sats
            latest_balance["last_change"] = f"Balance {direction} by {abs_change:.0f} sats."
            latest_balance["memo"] = "N/A"
        else:
            logger.error(f"Failed to fetch wallet info. Status Code: {response.status_code}")
    except Exception as e:
        logger.error(f"Error checking balance change: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Scheduler Setup ---------------------

def start_scheduler():
    """
    Start the scheduler for periodic tasks.
    """
    scheduler = BackgroundScheduler(timezone=utc)

    # Schedule balance change check if interval is greater than 0
    if WALLET_INFO_UPDATE_INTERVAL > 0:
        next_run = datetime.utcnow() + timedelta(seconds=1)
        scheduler.add_job(
            check_balance_change,
            'interval',
            seconds=WALLET_INFO_UPDATE_INTERVAL,
            id='balance_check',
            next_run_time=next_run
        )
        logger.info(f"Balance change monitoring scheduled every {WALLET_INFO_UPDATE_INTERVAL} seconds.")
    else:
        logger.info("Balance change monitoring is disabled (WALLET_INFO_UPDATE_INTERVAL set to 0).")

    # Schedule daily wallet balance notification if interval is greater than 0
    if WALLET_BALANCE_NOTIFICATION_INTERVAL > 0:
        next_run = datetime.utcnow() + timedelta(seconds=1)
        scheduler.add_job(
            send_wallet_balance,
            'interval',
            seconds=WALLET_BALANCE_NOTIFICATION_INTERVAL,
            id='wallet_balance_notification',
            next_run_time=next_run
        )
        logger.info(f"Daily wallet balance notification scheduled every {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds.")
    else:
        logger.info("Daily wallet balance notification is disabled (WALLET_BALANCE_NOTIFICATION_INTERVAL set to 0).")

    # Schedule latest payments fetch if interval is greater than 0
    if PAYMENTS_FETCH_INTERVAL > 0:
        next_run = datetime.utcnow() + timedelta(seconds=1)
        scheduler.add_job(
            send_latest_payments,
            'interval',
            seconds=PAYMENTS_FETCH_INTERVAL,
            id='latest_payments_fetch',
            next_run_time=next_run
        )
        logger.info(f"Latest payments fetch scheduled every {PAYMENTS_FETCH_INTERVAL} seconds.")
    else:
        logger.info("Latest payments fetch notification is disabled (PAYMENTS_FETCH_INTERVAL set to 0).")

    # Start the scheduler only if at least one job is scheduled
    if scheduler.get_jobs():
        scheduler.start()
        logger.info("Scheduler started successfully.")
    else:
        logger.warning("No jobs scheduled. Scheduler is not running.")

# --------------------- Flask Routes ---------------------

@app.route('/')
def home():
    return "LNbits Balance Monitor is running."

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "latest_balance": latest_balance,
        "latest_payments": latest_payments
    })

# --------------------- Application Entry Point ---------------------

if __name__ == "__main__":
    logger.info("Starting LNbits Balance Monitor.")

    # Log the current configuration
    logger.info(f"Using notification threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"Fetching latest {LATEST_TRANSACTIONS_COUNT} transactions for notifications")
    logger.info(f"Scheduler intervals - Balance Change Monitoring: {WALLET_INFO_UPDATE_INTERVAL} seconds, Daily Wallet Balance Notification: {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds, Latest Payments Fetch: {PAYMENTS_FETCH_INTERVAL} seconds")

    # Start scheduler for periodic updates
    start_scheduler()

    # Run Flask app in a separate thread to avoid blocking the scheduler
    def run_flask():
        app.run(host=APP_HOST, port=APP_PORT)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True  # Allows the program to exit even if the thread is running
    flask_thread.start()

    # Keep the main thread alive to let scheduler and Flask run
    try:
        while True:
            time.sleep(1)  # Use sleep to prevent high CPU usage
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down LNbits Balance Monitor.")
