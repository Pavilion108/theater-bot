#!/bin/bash
# =============================================================================
# 🎬 Theater Bot — One-Command Setup & Launch
# =============================================================================
# Usage:  bash start.sh
# =============================================================================

set -e

# Auto-detect python command
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

# Step 1: Create virtual environment if it doesn't exist
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "📦 Creating virtual environment..."
    $PY -m venv "$VENV_DIR"
    echo "   ✅ Virtual environment created!"
fi

# Step 2: Activate the venv
echo "📦 Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Step 3: Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt --quiet
echo "📦 Installing Playwright browsers..."
playwright install chromium --with-deps || playwright install chromium
echo "   ✅ Dependencies installed!"

# Step 4: Check for .env file
if [ ! -f .env ]; then
    echo ""
    echo "⚙️  Creating .env from template..."
    cp .env.example .env
    echo "   ⚠️  Please edit .env and add your TELEGRAM_BOT_TOKEN!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi

# Step 5: Validate token is set
TOKEN=$(grep -oP 'TELEGRAM_BOT_TOKEN="\K[^"]+' .env 2>/dev/null || grep -oP 'TELEGRAM_BOT_TOKEN=\K.+' .env 2>/dev/null || echo "")
if [ -z "$TOKEN" ] || [ "$TOKEN" = "your_telegram_bot_token_here" ]; then
    echo ""
    echo "❌ TELEGRAM_BOT_TOKEN is not set in .env!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi

echo "   ✅ .env file found and token is set!"

# Step 6: Launch the bot
echo ""
echo "🚀 Starting Theater Bot..."
echo "   Send any message to your Telegram bot to begin!"
echo "   Press Ctrl+C to stop."
echo "=================================================="
echo ""

python theater_automation.py
