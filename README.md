Its by far not perfect - it just fullfiles the need to get notified about lnbits wallet movements. Feel free to write issues or provide PR's. This code is free to use under the MIT License.

### Introduction

The **LNbits Balance Monitor aka. Naughtify** is a Python-based skript that monitors a single LNbits wallet and provides notifications via Telegram. It checks wallet balances, tracks changes, and fetches transaction details to keep you updated on your LNbits activity.

### **Features Summary**

- **Balance Monitoring:**  
  Continuously checks wallet balance every 60 seconds (configurable) and sends notifications if a change exceeds the defined threshold.

- **Daily Wallet Balance Report:**  
  Provides a detailed summary of your wallet balance, including total incoming and outgoing amounts, sent once every 24 hours (configurable).

- **Transactions Summary:**  
  Fetches and notifies about the latest 21 transactions (configurable) every 24 hours. Prevents duplicate notifications for already processed transactions.

- **Customizable Intervals:**  
  Fine-tune notification frequencies and thresholds via the `.env` file.

- **Flask API:**  
  Offers a lightweight API to check wallet status. Information

---

### **Customization Options**

You can modify the `.env` file to adjust default behaviors:

- **Balance Change Threshold:**  
  Set `BALANCE_CHANGE_THRESHOLD` to define the minimum Satoshi change required to trigger a notification. Example: 10 sats.

- **Transaction Count:**  
  Adjust `LATEST_TRANSACTIONS_COUNT` to set how many transactions are fetched for notifications. Already displayed transactions won't be notified again.

- **Scheduler Intervals:**  
  - `WALLET_INFO_UPDATE_INTERVAL`: Frequency of wallet balance checks. Default: 60 seconds.  
  - `WALLET_BALANCE_NOTIFICATION_INTERVAL`: Frequency of daily wallet balance reports. Default: 24 hours (86400 seconds).  
  - `PAYMENTS_FETCH_INTERVAL`: Frequency of transaction summary notifications. Default: 24 hours (86400 seconds).
---


### Screenshot:

![Screenshot from 2024-11-26 16-24-02](https://github.com/user-attachments/assets/cbb8959a-45d5-4272-a582-bd96227868d1)




---

### Step-by-Step Setup

#### Prerequisites
1. **Python**: Ensure you have Python 3.7+ (recommended 3.9+).
2. **LNbits Instance**: Accessible and configured with a Readonly API key.
3. **Telegram Bot**: Create a Telegram bot and obtain the bot token and your chat ID (also known as User ID)
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
#### Step 5: Create Telegram Webhook Link - Inline Command Feature

To connect your Telegram bot with your Python app using a webhook, follow these simple steps:

1. **Prepare Your Webhook URL:**

   - Combine your public app URL with the `/webhook` endpoint.
   - **Example:**

     ```
     https://your-public-domain.com/webhook
     ```

2. **Set the Webhook with an Inline Command:**

   - Replace `<YOUR_BOT_TOKEN>` with your Telegram bot token.
   - Replace `<YOUR_WEBHOOK_URL>` with your webhook URL from step 1.

   - **Option 1: Use a Web Browser**

     - Enter the following URL in your browser's address bar:

       ```
       https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>
       ```

     - **Example:**

       ```
       https://api.telegram.org/bot123456:ABCDEF/setWebhook?url=https://your-public-domain.com/webhook
       ```

   - **Option 2: Use cURL in Terminal**

     - Run the following command:

       ```bash
       curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>"
       ```

3. **Verify the Webhook Setup:**

   - You should receive a response confirming that the webhook was set successfully:

     ```json
     {
       "ok": true,
       "result": true,
       "description": "Webhook was set"
     }
     ```

4. **Test Your Bot:**

   - Your Python app should now receive updates via the webhook and respond accordingly.

**That's it!** Your Telegram bot is now connected to your Python app using a webhook.

#### Step 6: Run the Application
1. Start the app manually
   ```bash
   python naughtify.py
   ```
2. Logs will display in the console and are saved to `app.log`.

#### Step 7: Run the Application 24/7 with PM2
 **a) : Install PM2 in the Virtual Environment**
Activate your virtual environment and install PM2:
```bash
source venv/bin/activate
npm install -g pm2
```

 **b): Start the Script with PM2**
Navigate to the script's directory and start it with PM2:
```bash
pm2 start python3 --name naughtify -- naughtify.py
```

 **c): Save PM2 Processes**
To ensure the script is saved as process:
```bash
pm2 save
```
**c.1): Save PM2 Processes**
To ensure the script runs automatically after a reboot:
```bash
pm2 startup
```
 **d): Useful PM2 Commands**
- **View logs:**  
  ```bash
  pm2 logs
  ```

- **List processes:**  
  ```bash
  pm2 list
  ```

- **Stop the process:**  
  ```bash
  pm2 stop naughtify.py
  ```

- **Restart the process:**  
  ```bash
  pm2 restart naughtify.py
  ```


---
