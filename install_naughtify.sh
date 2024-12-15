#!/bin/bash

echo "Starting Naughtify installation..."

# Step 1: Prerequisites
echo "Updating system and installing prerequisites..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl

# Step 2: Clone the Repository
echo "Cloning Naughtify repository..."
git clone https://github.com/DoktorShift/naughtify.git
cd naughtify || { echo "Failed to enter naughtify directory"; exit 1; }

# Step 3: Set up Python Virtual Environment
echo "Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install wheel
pip install -r requirements.txt
deactivate

# Step 4: Configure .env File
echo "Configuring the .env file..."
wget -q https://raw.githubusercontent.com/DoktorShift/naughtify/refs/heads/main/example.env -O .env

read -p "Enter your Telegram Bot Token: " TELEGRAM_TOKEN
read -p "Enter your Telegram Chat ID (User ID): " CHAT_ID
read -p "Enter your LNBits Read-only API Key: " LNBITS_KEY
read -p "Enter your LNBits Server URL: " LNBITS_URL

# Update the .env file with user inputs
sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN|g" .env
sed -i "s|CHAT_ID=.*|CHAT_ID=$CHAT_ID|g" .env
sed -i "s|LNBITS_API_KEY=.*|LNBITS_API_KEY=$LNBITS_KEY|g" .env
sed -i "s|LNBITS_URL=.*|LNBITS_URL=$LNBITS_URL|g" .env

# Step 5: Optional Live Ticker Setup
echo "Would you like to enable the Live Ticker feature? (yes/no)"
read ENABLE_LIVE_TICKER
if [[ "$ENABLE_LIVE_TICKER" == "yes" ]]; then
    read -p "Enter Live Ticker subdomain (e.g., liveticker.yourdomain.com): " LIVE_TICKER_DOMAIN
    read -p "Enter LNURLP ID (6-letter Pay Link ID): " LNURLP_ID
    read -p "Enter Highlight Threshold (default: 2100 sats): " HIGHLIGHT_THRESHOLD
    read -p "Enter Information Page URL: " INFORMATION_URL

    sed -i "s|#DONATIONS_URL=.*|DONATIONS_URL=$LIVE_TICKER_DOMAIN|g" .env
    sed -i "s|#LNURLP_ID=.*|LNURLP_ID=$LNURLP_ID|g" .env
    sed -i "s|#HIGHLIGHT_THRESHOLD=.*|HIGHLIGHT_THRESHOLD=${HIGHLIGHT_THRESHOLD:-2100}|g" .env
    sed -i "s|#INFORMATION_URL=.*|INFORMATION_URL=$INFORMATION_URL|g" .env

    echo "Configuring Live Ticker in Caddy..."
    sudo bash -c "cat >> /etc/caddy/Caddyfile" <<EOL

# Configuration for Live Ticker
$LIVE_TICKER_DOMAIN {
    @root path /
    rewrite @root /donations
    reverse_proxy 127.0.0.1:5009
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer-when-downgrade"
        Content-Security-Policy "
            default-src 'self';
            script-src 'self' 'unsafe-inline';
            style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
            font-src 'self' https://fonts.gstatic.com;
            img-src 'self' data:;
            connect-src 'self';
            object-src 'none';
            frame-ancestors 'none';
            base-uri 'self';
            form-action 'self';
        "
    }
}
EOL
    sudo systemctl reload caddy
    echo "Live Ticker enabled and configured."
fi

# Step 6: Optional Overwatch Setup
echo "Would you like to enable the Overwatch feature? (yes/no)"
read ENABLE_OVERWATCH
if [[ "$ENABLE_OVERWATCH" == "yes" ]]; then
    read -p "Enter Overwatch URL (e.g., https://overwatch.yourdomain.com): " OVERWATCH_URL
    sed -i "s|#OVERWATCH_URL=.*|OVERWATCH_URL=$OVERWATCH_URL|g" .env

    echo "Overwatch link added to the bot. Make sure you set up Overwatch via Netlify or similar."
fi

# Step 7: Install and Configure Caddy
echo "Installing and configuring Caddy..."
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy

read -p "Enter the primary subdomain for Naughtify (e.g., naughtify.yourdomain.com): " NAUGHTIFY_DOMAIN
sudo bash -c "cat > /etc/caddy/Caddyfile" <<EOL
# Configuration for Naughtify
$NAUGHTIFY_DOMAIN {
    reverse_proxy /webhook* 127.0.0.1:5009
    reverse_proxy * 127.0.0.1:5009
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer-when-downgrade"
        Content-Security-Policy "
            default-src 'self';
            script-src 'self';
            style-src 'self' 'unsafe-inline';
            img-src 'self' data:;
            connect-src 'self';
            font-src 'self';
            object-src 'none';
            frame-ancestors 'none';
            base-uri 'self';
            form-action 'self';
        "
    }
}
EOL
sudo systemctl reload caddy

# Step 8: Configure Telegram Webhook
echo "Configuring Telegram webhook..."
WEBHOOK_URL="https://$NAUGHTIFY_DOMAIN/webhook"
curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook?url=$WEBHOOK_URL"

# Step 9: Create Systemd Service
echo "Creating systemd service for Naughtify..."
USER=$(whoami)
sudo bash -c "cat > /etc/systemd/system/naughtify.service" <<EOL
[Unit]
Description=Naughtify
After=network.target

[Service]
User=$USER
WorkingDirectory=/home/$USER/naughtify
EnvironmentFile=/home/$USER/naughtify/.env
ExecStart=/home/$USER/naughtify/venv/bin/python /home/$USER/naughtify/naughtify.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOL
sudo systemctl enable naughtify
sudo systemctl start naughtify

# Final Instructions
echo "Installation complete!"
echo "Visit your subdomains:"
echo " - Naughtify: https://$NAUGHTIFY_DOMAIN"
[[ "$ENABLE_LIVE_TICKER" == "yes" ]] && echo " - Live Ticker: https://$LIVE_TICKER_DOMAIN"
[[ "$ENABLE_OVERWATCH" == "yes" ]] && echo " - Overwatch: $OVERWATCH_URL"
echo "Use the following command to check Naughtify logs:"
echo "  sudo journalctl -u naughtify -f --since '2 hours ago'"
