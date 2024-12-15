#!/bin/bash

echo "🚀 Starting Naughtify Installation..."

# Initialize an empty array to hold the ports
PORTS=()

# Function to install prerequisites
install_prerequisites() {
    echo "🔧 Updating system and installing prerequisites..."
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl git ufw jq
}

# Function to install and configure Caddy (only once)
install_caddy() {
    echo "📦 Installing Caddy..."
    sudo bash -c "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    sudo bash -c "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list"
    sudo apt-get update
    sudo apt-get install -y caddy
    echo "✅ Caddy installed successfully."
}

# Function to clone the repository
clone_repository() {
    local dir_name=$1
    echo "📥 Cloning Naughtify repository into '$dir_name'..."
    git clone https://github.com/DoktorShift/naughtify.git "$dir_name" || { echo "❌ Failed to clone repository."; exit 1; }
    cd "$dir_name" || { echo "❌ Failed to enter '$dir_name' directory."; exit 1; }
}

# Function to set up Python virtual environment
setup_virtualenv() {
    local dir_name=$1
    echo "🛠️ Setting up Python virtual environment in '$dir_name'..."
    python3 -m venv venv
    source venv/bin/activate
    pip install wheel
    pip install -r requirements.txt
    deactivate
}

# Function to configure .env file
configure_env() {
    local dir_name=$1
    echo "📝 Configuring the .env file for '$dir_name'..."
    wget -q https://raw.githubusercontent.com/DoktorShift/naughtify/refs/heads/main/example.env -O .env

    read -p "Enter your Telegram Bot Token for '$dir_name': " TELEGRAM_TOKEN
    read -p "Enter your Telegram Chat ID (User ID) for '$dir_name': " CHAT_ID
    read -p "Enter your LNBits Read-only API Key for '$dir_name': " LNBITS_KEY
    read -p "Enter your LNBits Server URL for '$dir_name' (e.g., https://yourlnbitsserver.com): " LNBITS_URL

    # Update the .env file with user inputs
    sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN|g" .env
    sed -i "s|CHAT_ID=.*|CHAT_ID=$CHAT_ID|g" .env
    sed -i "s|LNBITS_READONLY_API_KEY=.*|LNBITS_READONLY_API_KEY=$LNBITS_KEY|g" .env
    sed -i "s|LNBITS_URL=.*|LNBITS_URL=$LNBITS_URL|g" .env

    # Configure APP_PORT
    echo "🔢 Configuring the port for '$dir_name'..."
    read -p "Enter the port to run '$dir_name' on (default: 5009): " PORT
    PORT=${PORT:-5009}

    # Check if APP_PORT exists in .env and update; else, append it
    if grep -q "^APP_PORT=" .env; then
        sed -i "s|^APP_PORT=.*|APP_PORT=$PORT|g" .env
    else
        echo "APP_PORT=$PORT" >> .env
    fi

    echo "✅ Port set to $PORT for '$dir_name'."

    # Add the port to the PORTS array
    PORTS+=("$PORT")
}

# Function to configure Caddy for a domain and port
configure_caddy() {
    local domain=$1
    local port=$2
    echo "🔧 Configuring Caddy for domain '$domain' on port '$port'..."
    sudo bash -c "cat >> /etc/caddy/Caddyfile" <<EOL

# Configuration for $domain
$domain {
    reverse_proxy /webhook* 127.0.0.1:$port
    reverse_proxy * 127.0.0.1:$port
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
    echo "✅ Caddy configured for '$domain'."
}

# Function to configure Telegram webhook
configure_telegram_webhook() {
    local domain=$1
    local token=$2
    echo "🔗 Configuring Telegram webhook for domain '$domain'..."
    WEBHOOK_URL="https://$domain/webhook"
    RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot$token/setWebhook" -d "url=$WEBHOOK_URL")
    SUCCESS=$(echo "$RESPONSE" | jq -r '.ok')

    if [[ "$SUCCESS" == "true" ]]; then
        echo "✅ Telegram webhook configured for '$domain'."
    else
        ERROR_MSG=$(echo "$RESPONSE" | jq -r '.description')
        echo "❌ Failed to configure Telegram webhook for '$domain'. Error: $ERROR_MSG"
        echo "Please ensure that your domain is correctly pointed and accessible."
    fi
}

# Function to create systemd service
create_systemd_service() {
    local dir_path=$1
    local user=$2
    local instance_name=$(basename "$dir_path")
    echo "🛡️ Creating systemd service for '$dir_path'..."
    SERVICE_NAME="naughtify_${instance_name}.service"
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

    sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Naughtify $(basename "$dir_path")
After=network.target

[Service]
User=$user
WorkingDirectory=$dir_path
EnvironmentFile=$dir_path/.env
ExecStart=$dir_path/venv/bin/python $dir_path/naughtify.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOL

    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
    echo "✅ Systemd service '$SERVICE_NAME' created and started."
}

# Function to set up the initial instance
setup_initial_instance_prompt() {
    local dir_name="naughtify"
    clone_repository "$dir_name"
    setup_virtualenv "$dir_name"
    configure_env "$dir_name"

    # Prompt for initial domain
    read -p "Enter the primary subdomain for 'naughtify' (e.g., naughtify.yourdomain.com): " NAUGHTIFY_DOMAIN

    # Read port from .env
    PORT=$(grep "^APP_PORT=" "naughtify/.env" | cut -d '=' -f2)

    # Optional Live Ticker Setup for Initial Instance
    echo "🔔 Would you like to enable the Live Ticker feature for 'naughtify'? (yes/no)"
    read ENABLE_LIVE_TICKER

    if [[ "$ENABLE_LIVE_TICKER" == "yes" ]]; then
        read -p "Enter Live Ticker subdomain for 'naughtify' (e.g., liveticker.yourdomain.com): " LIVE_TICKER_DOMAIN
        read -p "Enter LNURLP ID for 'naughtify' (6-letter Pay Link ID): " LNURLP_ID
        read -p "Enter Highlight Threshold for 'naughtify' (default: 2100 sats): " HIGHLIGHT_THRESHOLD
        read -p "Enter Information Page URL for 'naughtify' (optional, press Enter to skip): " INFORMATION_URL

        # Update the .env file with Live Ticker configurations
        sed -i "s|DONATIONS_URL=.*|DONATIONS_URL=$LIVE_TICKER_DOMAIN|g" "naughtify/.env"
        sed -i "s|LNURLP_ID=.*|LNURLP_ID=$LNURLP_ID|g" "naughtify/.env"
        sed -i "s|HIGHLIGHT_THRESHOLD=.*|HIGHLIGHT_THRESHOLD=${HIGHLIGHT_THRESHOLD:-2100}|g" "naughtify/.env"
        if [[ -n "$INFORMATION_URL" ]]; then
            sed -i "s|INFORMATION_URL=.*|INFORMATION_URL=$INFORMATION_URL|g" "naughtify/.env"
        fi

        # Configure Caddy for Live Ticker
        configure_caddy "$LIVE_TICKER_DOMAIN" "$PORT"

        echo "🔗 Live Ticker enabled and configured for 'naughtify'."
    fi

    # Configure Caddy for the initial instance
    configure_caddy "$NAUGHTIFY_DOMAIN" "$PORT"

    # Configure Telegram webhook for the initial instance
    configure_telegram_webhook "$NAUGHTIFY_DOMAIN" "$TELEGRAM_TOKEN"

    # Create systemd service for the initial instance
    create_systemd_service "/home/$USER/naughtify" "$USER"
}

# Function to set up an additional instance
setup_additional_instance() {
    local clone_dir=$1
    clone_repository "$clone_dir"
    setup_virtualenv "$clone_dir"
    configure_env "$clone_dir"

    # Prompt for domain and port
    read -p "Enter the primary subdomain for '$clone_dir' (e.g., naughtify2.yourdomain.com): " NEW_DOMAIN
    read -p "Enter the port to run '$clone_dir' on (default: 5010): " NEW_PORT
    NEW_PORT=${NEW_PORT:-5010}

    # Update the .env file with new configurations
    sed -i "s|APP_PORT=.*|APP_PORT=$NEW_PORT|g" "$clone_dir/.env"

    # Add the new port to the PORTS array
    PORTS+=("$NEW_PORT")

    # Optional Live Ticker Setup for Additional Instance
    echo "🔔 Would you like to enable the Live Ticker feature for '$clone_dir'? (yes/no)"
    read ENABLE_LIVE_TICKER_ADD
    if [[ "$ENABLE_LIVE_TICKER_ADD" == "yes" ]]; then
        read -p "Enter Live Ticker subdomain for '$clone_dir' (e.g., liveticker2.yourdomain.com): " NEW_LIVE_TICKER_DOMAIN
        read -p "Enter LNURLP ID for '$clone_dir' (6-letter Pay Link ID): " NEW_LNURLP_ID
        read -p "Enter Highlight Threshold for '$clone_dir' (default: 2100 sats): " NEW_HIGHLIGHT_THRESHOLD
        read -p "Enter Information Page URL for '$clone_dir' (optional, press Enter to skip): " NEW_INFORMATION_URL

        # Update the .env file with Live Ticker configurations
        sed -i "s|DONATIONS_URL=.*|DONATIONS_URL=$NEW_LIVE_TICKER_DOMAIN|g" "$clone_dir/.env"
        sed -i "s|LNURLP_ID=.*|LNURLP_ID=$NEW_LNURLP_ID|g" "$clone_dir/.env"
        sed -i "s|HIGHLIGHT_THRESHOLD=.*|HIGHLIGHT_THRESHOLD=${NEW_HIGHLIGHT_THRESHOLD:-2100}|g" "$clone_dir/.env"
        if [[ -n "$NEW_INFORMATION_URL" ]]; then
            sed -i "s|INFORMATION_URL=.*|INFORMATION_URL=$NEW_INFORMATION_URL|g" "$clone_dir/.env"
        fi

        # Configure Caddy for Live Ticker
        configure_caddy "$NEW_LIVE_TICKER_DOMAIN" "$NEW_PORT"

        echo "🔗 Live Ticker enabled and configured for '$clone_dir'."
    fi

    # Configure Caddy for the additional instance
    configure_caddy "$NEW_DOMAIN" "$NEW_PORT"

    # Prompt for Telegram Bot Token for the additional instance
    read -p "Enter your Telegram Bot Token for '$clone_dir': " NEW_TELEGRAM_TOKEN

    # Update the .env file with the new Telegram Token
    sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$NEW_TELEGRAM_TOKEN|g" "$clone_dir/.env"

    # Configure Telegram webhook for the additional instance
    configure_telegram_webhook "$NEW_DOMAIN" "$NEW_TELEGRAM_TOKEN"

    # Create systemd service for the additional instance
    create_systemd_service "/home/$USER/$clone_dir" "$USER"

    echo "✅ Additional instance '$clone_dir' set up successfully."
}

# Function to prompt for additional instances
prompt_additional_instances() {
    while true; do
        echo "⚙️ Would you like to set up an additional Naughtify instance? (yes/no)"
        read SETUP_ADDITIONAL

        if [[ "$SETUP_ADDITIONAL" == "yes" ]]; then
            while true; do
                read -p "Enter a unique name for the additional instance (e.g., naughtify2): " ADDITIONAL_NAME
                if [[ -d "$ADDITIONAL_NAME" ]]; then
                    echo "❌ Directory '$ADDITIONAL_NAME' already exists. Please choose a different name."
                elif [[ ! "$ADDITIONAL_NAME" =~ ^naughtify[0-9]+$ ]]; then
                    echo "❌ Invalid name. Please use a name like 'naughtify2', 'naughtify3', etc."
                else
                    break
                fi
            done

            setup_additional_instance "$ADDITIONAL_NAME"
        elif [[ "$SETUP_ADDITIONAL" == "no" ]]; then
            echo "🛑 Skipping additional instance setup."
            break
        else
            echo "❓ Please answer 'yes' or 'no'."
        fi
    done
}

# Function to configure firewall rules
configure_firewall() {
    echo "🛡️ Checking UFW status..."
    UFW_STATUS=$(sudo ufw status | grep -i "Status:" | awk '{print $2}')
    if [[ "$UFW_STATUS" == "inactive" ]]; then
        echo "🛡️ UFW is inactive."
        echo "Would you like to enable UFW and allow SSH connections? (yes/no)"
        read ENABLE_UFW
        if [[ "$ENABLE_UFW" == "yes" ]]; then
            echo "🔓 Allowing SSH through UFW..."
            sudo ufw allow OpenSSH
            echo "🔐 Allowing collected ports through UFW..."
            for port in "${PORTS[@]}"; do
                echo "🔓 Allowing port $port through UFW..."
                sudo ufw allow "$port"
            done
            echo "🔄 Enabling UFW..."
            sudo ufw --force enable
            echo "✅ UFW enabled and configured successfully."
        else
            echo "⚠️ UFW remains inactive. Please configure your firewall manually to allow the necessary ports."
            return
        fi
    else
        echo "🛡️ UFW is active."
        echo "🔐 Allowing collected ports through UFW..."
        for port in "${PORTS[@]}"; do
            echo "🔓 Allowing port $port through UFW..."
            sudo ufw allow "$port"
        done
        echo "🔄 Reloading UFW to apply changes..."
        sudo ufw reload
        echo "✅ Firewall configuration completed."
    fi
}

# Main Installation Process

# Step 1: Install Prerequisites
install_prerequisites

# Step 2: Install and Configure Caddy (if not already installed)
if ! command -v caddy &> /dev/null; then
    install_caddy
else
    echo "✅ Caddy is already installed."
fi

# Step 3: Set up Initial Instance
echo "🔰 Setting up the initial Naughtify instance..."
setup_initial_instance_prompt

# Step 4: Optional Additional Instances Setup
prompt_additional_instances

# Step 5: Configure Firewall Rules
echo "🔐 Configuring firewall rules..."
configure_firewall

# Final Instructions
echo "✅ Installation complete!"
echo "🎉 Visit your subdomains:"
echo " - Naughtify: https://$NAUGHTIFY_DOMAIN"
[[ "$ENABLE_LIVE_TICKER" == "yes" ]] && echo " - Live Ticker: https://$LIVE_TICKER_DOMAIN"
echo ""
echo "🔍 Use the following commands to check Naughtify logs:"
echo "  - Initial Instance Logs: sudo journalctl -u naughtify_naughtify.service -f --since '2 hours ago'"
for dir in naughtify*; do
    if [[ "$dir" != "naughtify" ]]; then
        echo "  - Additional Instance Logs ($dir): sudo journalctl -u naughtify_${dir}.service -f --since '2 hours ago'"
    fi
done
