#!/bin/bash
# =============================================================================
# 🎬 Theater Bot — One-Command Setup & Launch
# =============================================================================
# Usage:  bash start.sh
# =============================================================================

set -e

# Auto-detect python and pip commands
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "❌ Python not found! Please install Python 3."
    exit 1
fi

echo "=================================================="
echo "🎬 Theater Bot — Setup & Launch"
echo "=================================================="
echo "   Using: $($PY --version)"

# Step 1: Ensure pip is available
echo ""
echo "📦 Checking for pip..."
if ! $PY -m pip --version &>/dev/null; then
    echo "   ⚠️  pip not found — installing it now..."
    # Method 1: ensurepip
    if $PY -m ensurepip --upgrade 2>/dev/null; then
        echo "   ✅ pip installed via ensurepip!"
    else
        # Method 2: Download get-pip.py (works everywhere)
        echo "   Downloading get-pip.py..."
        curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        $PY /tmp/get-pip.py --quiet
        rm -f /tmp/get-pip.py
        echo "   ✅ pip installed via get-pip.py!"
    fi
fi

# Verify pip works
if ! $PY -m pip --version &>/dev/null; then
    echo "   ❌ Failed to install pip. Please run manually:"
    echo "      curl -sSL https://bootstrap.pypa.io/get-pip.py | python3"
    exit 1
fi

# Step 2: Install dependencies
echo "📦 Installing Python dependencies..."
$PY -m pip install -r requirements.txt --quiet --break-system-packages 2>/dev/null || $PY -m pip install -r requirements.txt --quiet
echo "   ✅ Dependencies installed!"

# Step 2: Check for .env file
if [ ! -f .env ]; then
    echo ""
    echo "⚙️  Creating .env from template..."
    cp .env.example .env
    echo "   ⚠️  Please edit .env and add your TELEGRAM_BOT_TOKEN!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi

# Step 3: Validate token is set
TOKEN=$(grep -oP 'TELEGRAM_BOT_TOKEN="\K[^"]+' .env 2>/dev/null || grep -oP 'TELEGRAM_BOT_TOKEN=\K.+' .env 2>/dev/null || echo "")
if [ -z "$TOKEN" ] || [ "$TOKEN" = "your_telegram_bot_token_here" ]; then
    echo ""
    echo "❌ TELEGRAM_BOT_TOKEN is not set in .env!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi

echo "   ✅ .env file found and token is set!"

# Step 4: Launch the bot
echo ""
echo "🚀 Starting Theater Bot..."
echo "   Send any message to your Telegram bot to begin!"
echo "   Press Ctrl+C to stop."
echo "=================================================="
echo ""

$PY theater_automation.py
