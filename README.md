

# LNbits Balance Monitor (aka. Naughtify)

The **LNbits Balance Monitor** is a Python Flask application hack to provide nearly real-time wallet monitoring and Telegram notifications for LNbits users. Whether you're tracking payments or monitoring balance changes, Naughtify keeps you informed.

---

## Features

- **Real-Time Notifications:**  
  Stay updated with balance changes exceeding a configurable threshold.

- **Daily Balance Report:**  
  Receive a daily summary of your wallet's balance and transaction statistics.

- **Transaction Notifications:**  
  Fetch and notify the latest transactions while avoiding duplicates.

- **Custom Inline Commands:**  
  Use `/balance`, `/transactions`, and `/info` in Telegram for instant updates.

- **Configurable Intervals:**  
  Set flexible notification intervals and thresholds via an `.env` file.

- **Flask API:**  
  A lightweight API provides wallet balance, transactions, and app status.

---

## Screenshots

![Balance Notification](https://github.com/user-attachments/assets/dc52e9e5-17a8-4016-ad1f-3e4c4f5b18c0)  
![Transaction Summary](https://github.com/user-attachments/assets/abd4269a-c137-40e9-bfda-5b322befa8df)

---

## Prerequisites

1. **Python 3.9+**
2. **LNbits Instance:** Access your LNbits API key (read-only).
3. **Telegram Bot:** Create a Telegram bot via [BotFather](https://t.me/BotFather) and obtain your bot token.
4. **Chat ID:** Use the [@userinfobot](https://t.me/userinfobot) on Telegram to find your chat ID.
5. (Optional) **Virtual Environment:** Recommended for dependency isolation.

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/DoktorShift/naughtify.git
cd naughtify
```

### Step 2: Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure the Environment

1. Create a `.env` file in the project directory.
2. Use the following template:

```plaintext
# --------------------- Telegram Configuration ---------------------
# Token for your Telegram bot (obtained from BotFather)
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE

# Telegram Chat ID where notifications will be sent
# Use tools like @userinfobot to find your Chat ID
CHAT_ID=YOUR_TELEGRAM_CHAT_ID_HERE

# --------------------- LNbits Configuration ---------------------
# Read-only API key for retrieving wallet balances and authenticating webhooks
LNBITS_READONLY_API_KEY=YOUR_LNBITS_READONLY_API_KEY_HERE

# Base URL of your LNbits instance (ensure it includes the protocol, e.g., https://)
LNBITS_URL=https://your-lnbits-instance-url.com

# Custom name for your LNbits instance (used in Telegram notifications)
# Enclosed in quotes because it contains spaces
INSTANCE_NAME="Your_Instance_Name"

# --------------------- Notification Settings ---------------------
# Threshold for balance changes in Satoshis to trigger a notification.
BALANCE_CHANGE_THRESHOLD=10

# Number of latest transactions to fetch for notifications. Default is 21
# Duplicates will be ignored.
LATEST_TRANSACTIONS_COUNT=21

# --------------------- Scheduler Intervals ---------------------
# Interval in seconds for checking balance changes
# Set to 0 to disable the notification
WALLET_INFO_UPDATE_INTERVAL=60

# Interval in seconds for sending daily wallet balance notifications
# Default: 86400 seconds (24 hours)
# Set to 0 to disable the daily notification
WALLET_BALANCE_NOTIFICATION_INTERVAL=86400

# Interval in seconds for fetching the latest payments
# Default: 86400 seconds (24 hours)
# Set to 0 to disable fetching payments
PAYMENTS_FETCH_INTERVAL=86400

# --------------------- Flask Server Configuration ---------------------
# Host address for the Flask server
APP_HOST=127.0.0.1

# Port number for the Flask server
APP_PORT=5009

# --------------------- File Paths ---------------------
# File to track processed payments
PROCESSED_PAYMENTS_FILE=processed_payments.txt

# File to store the current balance
CURRENT_BALANCE_FILE=current-balance.txt
```

### Step 5: Telegram Bot Webhook Setup

To enable inline commands (like `/balance`, `/transactions`, `/info`), connect your Telegram bot to the app:

1. **Prepare Your Webhook URL:**  
   Combine your app's public URL with the apps `/webhook` endpoint.  
   Example:  
   ```
   https://your-public-domain.com/webhook
   ```

2. **Set the Webhook:**  
   Replace placeholders below with your **Telegram Bot Token** and **Webhook URL**:

   - **Using a Web Browser:**  
     ```
     https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>
     ```
     Example:  
     ```
     https://api.telegram.org/bot123456:ABCDEF/setWebhook?url=https://your-public-domain.com/webhook
     ```

   - **Using cURL (Command Line):**  
     ```bash
     curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>"
     ```

3. **Verify the Webhook:**  
   Telegram should confirm:  
   ```json
   {
     "ok": true,
     "result": true,
     "description": "Webhook was set"
   }
   ```

4. **Test Your Bot:**  
   Open Telegram and test commands after the next Step:
   - `/balance`  
   - `/transactions`  
   - `/info`

---

### Step 6: Start the Application

```bash
python naughtify.py
```

To run in the background:
--> You can also use just basic systemd.service instead

1. **Install PM2:**  
   ```bash
   npm install -g pm2
   ```

2. **Start the script:**  
   ```bash
   pm2 start python3 --name naughtify -- naughtify.py
   pm2 save
   pm2 startup
   ```

---

## Usage

### Inline Commands in Telegram

- `/balance`: View the current wallet balance.
- `/transactions`: Fetch the latest transactions.
- `/info`: Check app configuration and intervals.

### API Endpoints

- **`GET /status`**: Check app status and latest updates.
- **`POST /webhook`**: Receive updates from Telegram.

---

## Scheduler

- **Balance Monitoring**: Triggered every 60 seconds (default).  
- **Daily Reports**: Sent every 24 hours (default).  
- **Transaction Fetching**: Updates every 24 hours (default).  

Intervals can be adjusted in the `.env` file.

---
---
---

## Contributing

We welcome feedback and pull requests! Feel free to submit issues or enhance the app with new features.  
Licensed under the MIT License.

### A Note on This Solution

This bot is a simple hack designed to keep you informed about your LNbits wallet activity. While it fulfills its purpose, a more robust solution could be built as an official LNbits extension.  

If you're inspired to take this further, feel free to develop a proper LNbits extension! You can find detailed information on creating an extension here:  
[**LNbits Extensions Wiki**](https://github.com/lnbits/lnbits/wiki/LNbits-Extensions)  



## Acknowledgments

A big thank you to [**AxelHamburch**](https://github.com/AxelHamburch) for expressing the need for this bot and inspiring its creation.  

A heartfelt thank you to the entire [**LNbits Team**](https://github.com/lnbits) for your incredible work on the outstanding LNbits project. Your contributions make solutions like this possible!  

--- 

