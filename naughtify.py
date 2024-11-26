import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Bot
from dotenv import load_dotenv
import aiohttp
import asyncio
import traceback
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import utc
from flask import Flask, jsonify
from datetime import datetime, timedelta
import aiofiles
import sys
from threading import Thread

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

async def load_processed_payments():
    """
    Asynchronously load processed payment hashes from the tracking file into a set.
    """
    processed = set()
    if os.path.exists(PROCESSED_PAYMENTS_FILE):
        try:
            async with aiofiles.open(PROCESSED_PAYMENTS_FILE, mode='r') as f:
                async for line in f:
                    processed.add(line.strip())
            logger.debug(f"Loaded {len(processed)} processed payment hashes.")
        except Exception as e:
            logger.error(f"Failed to load processed payments: {e}")
            logger.debug(traceback.format_exc())
    return processed

async def add_processed_payment(payment_hash):
    """
    Asynchronously add a processed payment hash to the tracking file.
    """
    try:
        async with aiofiles.open(PROCESSED_PAYMENTS_FILE, mode='a') as f:
            await f.write(f"{payment_hash}\n")
        logger.debug(f"Added payment hash {payment_hash} to processed payments.")
    except Exception as e:
        logger.error(f"Failed to add processed payment: {e}")
        logger.debug(traceback.format_exc())

async def load_last_balance():
    """
    Asynchronously load the last known balance from the balance file.
    """
    if not os.path.exists(CURRENT_BALANCE_FILE):
        logger.info("Balance file does not exist. Creating with current balance.")
        return None
    try:
        async with aiofiles.open(CURRENT_BALANCE_FILE, mode='r') as f:
            content = (await f.read()).strip()
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

async def save_current_balance(balance):
    """
    Asynchronously save the current balance to the balance file.
    """
    try:
        async with aiofiles.open(CURRENT_BALANCE_FILE, mode='w') as f:
            await f.write(str(balance))
        logger.debug(f"Saved current balance: {balance} sats.")
    except Exception as e:
        logger.error(f"Failed to save current balance: {e}")
        logger.debug(traceback.format_exc())

# Initialize the set of processed payments
processed_payments = asyncio.run(load_processed_payments())

# Initialize Flask app
app = Flask(__name__)

# Global variables to store the latest data
latest_balance = {
    "balance_sats": None,
    "last_change": None,
    "memo": None
}

latest_payments = []

# --------------------- Async Functions ---------------------

async def fetch_api(session, endpoint):
    """
    Asynchronously fetch data from the LNbits API.
    """
    url = f"{LNBITS_URL}/api/v1/{endpoint}"
    headers = {"X-Api-Key": LNBITS_READONLY_API_KEY}
    try:
        async with session.get(url, headers=headers, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                logger.debug(f"Fetched data from {endpoint}: {data}")
                return data
            else:
                logger.error(f"Failed to fetch {endpoint}. Status Code: {response.status}")
                return None
    except Exception as e:
        logger.error(f"Error fetching {endpoint}: {e}")
        logger.debug(traceback.format_exc())
        return None

async def send_latest_payments():
    """
    Fetch the latest payments and notify Telegram with incoming and outgoing transactions.
    """
    logger.info("Fetching latest payments for notification...")
    async with aiohttp.ClientSession() as session:
        payments = await fetch_api(session, "payments")
        if payments is None:
            return

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
            memo = payment.get("memo", "No memo provided")  # LOL ....
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
            await add_processed_payment(payment_hash)
            new_processed_hashes.append(payment_hash)

        if not (incoming_payments or outgoing_payments):
            logger.info("No new incoming or outgoing payments to notify.")
            return

        # Prepare the Telegram message with enhanced markdown formatting
        message_lines = [
            f"⚡ *{INSTANCE_NAME}* - *Latest Transactions* ⚡\n"
        ]

        if incoming_payments:
            message_lines.append("🟢 *Incoming Payments:*")
            for idx, payment in enumerate(incoming_payments, 1):
                message_lines.append(
                    f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
                )
            message_lines.append("")  # Add an empty line for spacing

        if outgoing_payments:
            message_lines.append("🔴 *Outgoing Payments:*")
            for idx, payment in enumerate(outgoing_payments, 1):
                message_lines.append(
                    f"{idx}. *Amount:* `{payment['amount']} sats`\n   *Memo:* {payment['memo']}"
                )
            message_lines.append("")  # Add an empty line for spacing

        # Append the additional user-friendly message
        additional_text = (
            f"🔗 [View Details]({LNBITS_URL})\n"
            f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        message_lines.append(additional_text)

        full_message = "\n".join(message_lines)

        # Send the message to Telegram
        try:
            await bot.send_message(chat_id=CHAT_ID, text=full_message, parse_mode='Markdown')
            logger.info("Latest payments notification sent to Telegram successfully.")
            latest_payments.extend(new_processed_hashes)
        except Exception as telegram_error:
            logger.error(f"Failed to send payments message to Telegram: {telegram_error}")
            logger.debug(traceback.format_exc())

async def send_wallet_balance():
    """
    Send the current wallet balance via Telegram in a professional and clear format.
    """
    logger.info("Sending daily wallet balance notification...")
    async with aiohttp.ClientSession() as session:
        wallet_info = await fetch_api(session, "wallet")
        if wallet_info is None:
            return

        current_balance_msat = wallet_info.get("balance", 0)
        current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

        # Fetch payments to calculate counts and totals
        payments = await fetch_api(session, "payments")
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

        # Prepare the Telegram message with enhanced markdown formatting
        message = (
            f"📊 *{INSTANCE_NAME}* - *Daily Wallet Balance* 📊\n\n"
            f"🔹 *Current Balance:* `{int(current_balance_sats)} sats`\n"
            f"🔹 *Total Incoming:* `{int(incoming_total)} sats` across `{incoming_count}` transactions\n"
            f"🔹 *Total Outgoing:* `{int(outgoing_total)} sats` across `{outgoing_count}` transactions\n\n"
            f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"🔗 [View Details]({LNBITS_URL})"
        )

        # Send the message to Telegram
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
            logger.info("Daily wallet balance notification sent successfully.")
            # Update the latest_balance
            latest_balance["balance_sats"] = current_balance_sats
            latest_balance["last_change"] = "Daily balance report."
            latest_balance["memo"] = "N/A"
            # Save the current balance
            await save_current_balance(current_balance_sats)
        except Exception as telegram_error:
            logger.error(f"Failed to send daily wallet balance message to Telegram: {telegram_error}")
            logger.debug(traceback.format_exc())

async def check_balance_change():
    """
    Periodically check the wallet balance and notify if it has changed beyond the threshold.
    """
    logger.info("Checking for balance changes...")
    async with aiohttp.ClientSession() as session:
        wallet_info = await fetch_api(session, "wallet")
        if wallet_info is None:
            return

        current_balance_msat = wallet_info.get("balance", 0)
        current_balance_sats = current_balance_msat / 1000  # Convert msats to sats

        last_balance = await load_last_balance()

        if last_balance is None:
            # First run, initialize the balance file
            await save_current_balance(current_balance_sats)
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

        # Prepare the Telegram message with enhanced markdown formatting
        message = (
            f"⚡ *{INSTANCE_NAME}* - *Balance Update* ⚡\n\n"
            f"🔹 *Previous Balance:* `{int(last_balance):,} sats`\n"
            f"🔹 *Change:* `{'+' if change_amount > 0 else '-'}{int(abs_change):,} sats`\n"
            f"🔹 *New Balance:* `{int(current_balance_sats):,} sats`\n\n"
            f"🕒 *Timestamp:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"🔗 [View Details]({LNBITS_URL})"
        )

        # Send the message to Telegram
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
            logger.info(f"Balance changed from {last_balance:.0f} to {current_balance_sats:.0f} sats. Notification sent.")
            # Update the balance file and latest_balance
            await save_current_balance(current_balance_sats)
            latest_balance["balance_sats"] = current_balance_sats
            latest_balance["last_change"] = f"Balance {direction} by {int(abs_change):,} sats."
            latest_balance["memo"] = "N/A"
        except Exception as telegram_error:
            logger.error(f"Failed to send balance change message to Telegram: {telegram_error}")
            logger.debug(traceback.format_exc())

# --------------------- Scheduler Setup ---------------------

def start_scheduler(scheduler):
    """
    Start the scheduler for periodic tasks using AsyncIOScheduler.
    """
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
    return "🔍 LNbits Balance Monitor is running."

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "latest_balance": latest_balance,
        "latest_payments": latest_payments
    })

# --------------------- Run Flask in Separate Thread ---------------------

def run_flask():
    """
    Run the Flask app in a separate thread.
    """
    try:
        app.run(host=APP_HOST, port=APP_PORT)
    except Exception as e:
        logger.error(f"Flask server error: {e}")
        logger.debug(traceback.format_exc())

# --------------------- Application Entry Point ---------------------

if __name__ == "__main__":
    logger.info("🚀 Starting LNbits Balance Monitor.")

    # Log the current configuration
    logger.info(f"🔔 Notification Threshold: {BALANCE_CHANGE_THRESHOLD} sats")
    logger.info(f"📊 Fetching Latest {LATEST_TRANSACTIONS_COUNT} Transactions for Notifications")
    logger.info(f"⏲️ Scheduler Intervals - Balance Change Monitoring: {WALLET_INFO_UPDATE_INTERVAL} seconds, Daily Wallet Balance Notification: {WALLET_BALANCE_NOTIFICATION_INTERVAL} seconds, Latest Payments Fetch: {PAYMENTS_FETCH_INTERVAL} seconds")

    # Create a new event loop and set it as the current loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Initialize and start the scheduler within the event loop
    scheduler = AsyncIOScheduler(timezone=utc)
    start_scheduler(scheduler)

    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask server running on {APP_HOST}:{APP_PORT}")

    # Run the event loop forever
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Shutting down LNbits Balance Monitor.")
        scheduler.shutdown()
        sys.exit()
