### Introduction

The **LNbits Balance Monitor aka. Naughtify** is a Python-based application that monitors a single LNbits wallet and provides notifications via Telegram. It checks wallet balances, tracks changes, and fetches transaction details to keep you updated on your LNbits activity.

Its by far not perfect it just fullfiles the need to get notified about lnbits wallet movements. Feel free to write issues or provide PR's. This code is free to use under the MIT License.
---

### Screenshot:


![Screenshot from 2024-11-26 05-29-43](https://github.com/user-attachments/assets/247ad2a9-09e5-4581-ab9d-1ddda721138a)
![Screenshot from 2024-11-26 05-59-40](https://github.com/user-attachments/assets/dafc3244-cb6a-469d-b39f-adb1d2058117)


---

### Step-by-Step Setup

#### Prerequisites
1. **Python**: Ensure you have Python 3.7+ (recommended 3.9+).
2. **LNbits Instance**: Accessible and configured with a Readonly API key.
3. **Telegram Bot**: Create a Telegram bot and obtain the bot token and your chat ID.
4. **Virtual Environment (optional)**: To manage dependencies.

---

#### Step 1: Clone the Repository
```bash
git clone https://github.com/DoktorShift/naughtify.git
cd naughtify
```

---

#### Step 2: Create a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

---

#### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

---

#### Step 4: Configure Environment Variables
1. Create a `.env` file in the project directory.
2. Copy the following example configuration and replace placeholders with your information:

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

#### Step 5: Run the Application
1. Start the bot:
   ```bash
   python naughtify.py
   ```
2. Logs will display in the console and are saved to `app.log`.

---

### Features Summary

- **Balance Monitoring**: Check and notify wallet balance changes every 60 seconds (configurable).
- **Daily Wallet Balance Report**: Send a summary of the wallet balance every 24 hours (configurable).
- **Transactions Summary**: Fetch the latest 21 transactions and notify every 24 hours (configurable).
- **Customizable Intervals**: Configure notification thresholds and scheduler intervals via the `.env` file.
- **Flask API**: A lightweight server to fetch wallet status via `/status`.

---

### Customization Options
Modify the `.env` file to change default behavior:
- **Balance Threshold**: Adjust the `BALANCE_CHANGE_THRESHOLD` to define the minimum change to trigger notifications.
- **Transaction Count**: Set `LATEST_TRANSACTIONS_COUNT` for the number of transactions to fetch. Already displayed transactions will not show up again.
- **Scheduler Intervals**:
  - `WALLET_INFO_UPDATE_INTERVAL`: Frequency of balance checks.
  - `PAYMENTS_FETCH_INTERVAL`: Frequency of transaction summary notifications.

---
