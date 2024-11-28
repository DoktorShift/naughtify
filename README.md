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
5. **Caddy Web Server:** Required to serve the app and enable inline commands. See [Setting Up Caddy Web Server](#setting-up-caddy-web-server).
6. (Optional) **Virtual Environment:** Recommended for dependency isolation.

**Note:**  
The following installation runs the Python app locally on `127.0.0.1`. If you want to access it externally or integrate with Caddy, make sure to configure your setup and open ports.

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

1. Copy the `.env` and open it.

```bash
https://raw.githubusercontent.com/DoktorShift/naughtify/refs/heads/main/example.env
mv example.env .env
sudo nano .env
```

2. Fill in at least the first three fields of the template, e.g.:

```plaintext
# --------------------- Telegram Configuration ---------------------
# Token for your Telegram bot (obtained from BotFather)
TELEGRAM_BOT_TOKEN=7500000068:AAGGoeiJ8wuFXxxxxxxxxrqrSw-vbxR8Q

# Telegram Chat ID where notifications will be sent
# Use tools like @userinfobot to find your Chat ID
CHAT_ID=851000046

# --------------------- LNbits Configuration ---------------------
# Read-only API key for retrieving wallet balances and authenticating webhooks
LNBITS_READONLY_API_KEY=33a687483bb87xxxxxxxxx7def0Y6b0be0

# Base URL of your LNbits instance (ensure it includes the protocol, e.g., https://)
LNBITS_URL=https://lnbits.mydomain.com

# Custom name for your LNbits instance (used in Telegram notifications)
# Enclosed in quotes because it contains spaces
INSTANCE_NAME="My Wallet"

..
```

---

### Step 6: Setting Up Caddy Web Server

To expose the Flask app and enable inline commands, you'll need to set up Caddy as a reverse proxy.

#### Step a: Install Caddy

Follow the [official Caddy installation guide](https://caddyserver.com/docs/install) to install Caddy on your server.

#### Step b: Configure the Caddyfile

Create or edit your Caddyfile `nano /etc/caddy/Caddyfile` with the following configuration:

```plaintext
# Example configuration for Naughtify
naughtify.example.com {
        reverse_proxy /webhook* 127.0.0.1:5009

        encode gzip

        # Security headers
        header {
                Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
                X-Content-Type-Options "nosniff"
                X-Frame-Options "DENY"
                Referrer-Policy "no-referrer-when-downgrade"
                Content-Security-Policy "default-src 'self'"
        }
}
```

- Replace **`naughtify.example.com`** with your actual domain name.  
- Make sure your Flask app is running locally on `127.0.0.1:5009`.

#### Step c: Reload Caddy

Restart or reload Caddy to apply the changes:
```bash
sudo systemctl reload caddy
```

Note: Dont forget to set the A-Record on that Domain. You have to do that on your domain providers site.

---

### Step 6: Telegram Bot Webhook Setup

To enable inline commands (like `/balance`, `/transactions`, `/info`), connect your Telegram bot to the app:

1. **Prepare Your Webhook URL:**  
   Combine your domain with the `/webhook` endpoint.  
   Example:  
   ```
   https://naughtify.example.com/webhook
   ```

2. **Set the Webhook:**  
   Replace placeholders below with your **Telegram Bot Token** and **Webhook URL**:

   - **Using a Web Browser:**  
     ```
     https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>
     ```
     Example:  
     ```
     https://api.telegram.org/bot123456:ABCDEF/setWebhook?url=https://naughtify.example.com/webhook
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

---

### Step 7: Start the Application

```bash
python naughtify.py
```
Output:
```plaintext

[2024-11-28 15:14:32,759] [INFO] 🚀 Starting LNbits Balance Monitor.
[2024-11-28 15:14:32,760] [INFO] 🔔 Notification Threshold: 1 sats
[2024-11-28 15:14:32,760] [INFO] 📊 Fetching Latest 10 Transactions for Notifications
[2024-11-28 15:14:32,760] [INFO] ⏲️ Scheduler Intervals - Balance Change Monitoring: 60 seconds, Daily Wallet Balance Notification: 60 seconds, Latest Payments Fetch: 60 seconds
[2024-11-28 15:14:32,772] [INFO] Flask server running on 127.0.0.1:5009
 * Serving Flask app 'naughtify'
 * Debug mode: off
 * Running on http://127.0.0.1:5009

```

---

### Step 8: Naughtify Autostart Service

1. Create new system service:

```bash
sudo nano /etc/systemd/system/naughtify.service
```

2. Fill the file with the following and customize `youruser`:

```plaintext
[Unit]
Description=Naughtify
After=network.target

[Service]
User=youruser
WorkingDirectory=/home/youruser/naughtify
EnvironmentFile=/home/youruser/naughtify/.env
ExecStart=/home/youruser/naughtify/venv/bin/python /home/youruser/naughtify/naughtify.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

3. Activate, start and monitor:

```bash
sudo systemctl enable naughtify
sudo systemctl start naughtify
sudo systemctl status naughtify
```

From now on, naughtify will start automatically with every restart.

---

## Contributing

I welcome feedback and pull requests! Feel free to submit issues or enhance the app with new features.  
Licensed under the MIT License.

### A Note on This Solution

This bot is a simple hack designed to keep you informed about your LNbits wallet activity. While it fulfills its purpose, a more robust solution could be built as an official LNbits extension.  

If you're inspired to take this further, feel free to develop a proper LNbits extension! You can find detailed information on creating an extension here:  
[**LNbits Extensions Wiki**](https://github.com/lnbits/lnbits/wiki/LNbits-Extensions)

---

## Acknowledgments

A big thank you to [**AxelHamburch**](https://github.com/AxelHamburch) for expressing the need for this bot and inspiring its creation.  

A heartfelt thank you to the entire [**LNbits Team**](https://github.com/lnbits) for your incredible work on the outstanding [**LNbits**](https://lnbits.com) project. Your contributions make solutions like this possible!  
