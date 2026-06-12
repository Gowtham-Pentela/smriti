#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Smriti Reverse Proxy Swapper (Blue/Green Routing Control)
#
# Usage:
#   ./smriti-swap-proxy.sh --target blue     # Routes external traffic to port 8000
#   ./smriti-swap-proxy.sh --target green    # Routes external traffic to port 9000
#   ./smriti-swap-proxy.sh --rollback        # Reverts Nginx to previous active port
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

TARGET=""
NGINX_CONF="/etc/nginx/conf.d/smriti.conf"
BACKUP_CONF="/etc/nginx/conf.d/smriti.conf.bak"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --rollback) TARGET="rollback"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "Error: --target [blue|green] or --rollback is required."
    exit 1
fi

# Check if Nginx configuration folder exists
if [ ! -d "/etc/nginx/conf.d" ]; then
    echo "⚠️  Nginx conf.d folder not found. Simulating configurations locally..."
    # Local demo fallback
    mkdir -p .temp_nginx
    NGINX_CONF=".temp_nginx/smriti.conf"
    BACKUP_CONF=".temp_nginx/smriti.conf.bak"
    touch "$NGINX_CONF"
fi

swap_port() {
    local port=$1
    local name=$2
    echo "🔄 Swapping active upstream in $NGINX_CONF to point to $name (port $port)..."
    
    # Back up current configuration
    if [ -f "$NGINX_CONF" ]; then
        cp "$NGINX_CONF" "$BACKUP_CONF"
    fi
    
    # Update proxy_pass to point to new port
    # Matches: proxy_pass http://127.0.0.1:XXXX;
    if grep -q "proxy_pass" "$NGINX_CONF" 2>/dev/null; then
        sed -i.bak "s|proxy_pass http://127.0.0.1:[0-9]\{4\}|proxy_pass http://127.0.0.1:$port|g" "$NGINX_CONF"
        rm -f "${NGINX_CONF}.bak"
    else
        # Write fresh minimal server block if file is empty/non-existent
        cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name localhost;
    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
    fi
    
    # Test and reload Nginx
    if command -v nginx &>/dev/null; then
        echo "🧪 Testing Nginx configuration..."
        nginx -t
        echo "🚀 Reloading Nginx daemon..."
        nginx -s reload
        echo "✅ Traffic successfully routed to $name (port $port)."
    else
        echo "ℹ️  Nginx command not found on host. Simulating successful reload..."
        echo "✅ Local configuration swapped to point to port $port successfully."
    fi
}

rollback() {
    if [ ! -f "$BACKUP_CONF" ]; then
        echo "❌ Rollback failed: No backup configuration file found in Nginx directory."
        exit 1
    fi
    
    echo "🚨 Rollback triggered! Reverting Nginx configuration..."
    cp "$BACKUP_CONF" "$NGINX_CONF"
    
    if command -v nginx &>/dev/null; then
        nginx -t
        nginx -s reload
        echo "✅ Rollback executed. Nginx reloaded successfully."
    else
        echo "✅ Simulated rollback successfully completed."
    fi
}

# Execution
case "$TARGET" in
    blue)
        swap_port 8000 "Blue"
        ;;
    green)
        swap_port 9000 "Green"
        ;;
    rollback)
        rollback
        ;;
    *)
        echo "Error: Invalid target name '$TARGET'. Use 'blue' or 'green'."
        exit 1
        ;;
esac
