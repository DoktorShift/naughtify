### Introduction

The **LNbits Balance Monitor** is a Python-based application that monitors a single LNbits wallet and provides notifications via Telegram. It checks wallet balances, tracks changes, and fetches transaction details to keep you updated on your LNbits activity.

Its by far not perfect but maybe someone can use or even modify it. Feel free to use it under the MIT License.

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
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
CHAT_ID=your_telegram_chat_id_here

# --------------------- LNbits Configuration ---------------------
LNBITS_READONLY_API_KEY=your_lnbits_readonly_api_key_here
LNBITS_URL=https://YourDomain.de  # Replace with your LNbits instance URL
INSTANCE_NAME=My LNbits Instance  # Replace with a custom name for your notifications

# --------------------- Notification Settings ---------------------
BALANCE_CHANGE_THRESHOLD=1000       # Notify if balance changes by at least 1 sat. Enter amount in msat. 1000 msat = 1 sat
LATEST_TRANSACTIONS_COUNT=21        # Fetch the latest 21 transactions for notification

# --------------------- Scheduler Intervals ---------------------
WALLET_INFO_UPDATE_INTERVAL=60      # Check wallet balance change every 60 seconds
PAYMENTS_FETCH_INTERVAL=172800      # Fetch payments every 48 hours (172800 seconds)

# --------------------- Flask Server Configuration ---------------------
APP_HOST=0.0.0.0                     # Listen on all interfaces (use 127.0.0.1 for localhost only)
APP_PORT=5009                       # Port number for the Flask server
```

---

#### Step 5: Run the Application
1. Start the bot:
   ```bash
   python lnbits_balance_monitor.py
   ```
2. Logs will display in the console and are saved to `app.log`.

---

### Features Summary

- **Balance Monitoring**: Check and notify wallet balance changes every 60 seconds (configurable).
- **Daily Wallet Balance Report**: Send a summary of the wallet balance every 24 hours.
- **Transactions Summary**: Fetch the latest 21 transactions and notify every 48 hours.
- **Customizable Intervals**: Configure notification thresholds and scheduler intervals via the `.env` file.
- **Flask API**: A lightweight server to fetch wallet status via `/status`.

---

### Customization Options
Modify the `.env` file to change default behavior:
- **Balance Threshold**: Adjust the `BALANCE_CHANGE_THRESHOLD` to define the minimum change to trigger notifications.
- **Transaction Count**: Set `LATEST_TRANSACTIONS_COUNT` for the number of transactions to fetch.
- **Scheduler Intervals**:
  - `WALLET_INFO_UPDATE_INTERVAL`: Frequency of balance checks.
  - `PAYMENTS_FETCH_INTERVAL`: Frequency of transaction summary notifications.

---
