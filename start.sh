#!/bin/bash
# =============================================================================
# 🎬 Theater Bot — One-Command Setup & Launch
# =============================================================================
# Usage:  bash start.sh
# =============================================================================

set -e

echo "=================================================="
echo "🎬 Theater Bot — Setup & Launch"
echo "=================================================="

# Step 1: Install dependencies
echo ""
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt --quiet
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

python theater_automation.py
