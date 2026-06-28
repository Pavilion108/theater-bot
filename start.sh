#!/bin/bash
# =============================================================================
# 🎬 Theater Bot — One-Command Setup & Launch (Dockerized)
# =============================================================================
# Usage:  bash start.sh
# =============================================================================

set -e

echo "=================================================="
echo "🎬 Theater Bot — Setup & Launch (Docker Mode)"
echo "=================================================="

# Step 1: Check for .env file
if [ ! -f .env ]; then
    echo ""
    echo "⚙️  Creating .env from template..."
    cp .env.example .env
    echo "   ⚠️  Please edit .env and add your TELEGRAM_BOT_TOKEN!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi

# Step 2: Validate token is set
TOKEN=$(grep -oP 'TELEGRAM_BOT_TOKEN="\K[^"]+' .env 2>/dev/null || grep -oP 'TELEGRAM_BOT_TOKEN=\K.+' .env 2>/dev/null || echo "")
if [ -z "$TOKEN" ] || [ "$TOKEN" = "your_telegram_bot_token_here" ]; then
    echo ""
    echo "❌ TELEGRAM_BOT_TOKEN is not set in .env!"
    echo "   Run: nano .env"
    echo ""
    exit 1
fi
echo "   ✅ .env file found and token is set!"

# Step 3: Launch via Docker
echo ""
echo "🚀 Building and starting the Theater Bot container..."
echo "   This uses the official Playwright image to ensure full compatibility."
echo ""
docker compose up --build -d

echo ""
echo "=================================================="
echo "✅ Bot is running in the background!"
echo "   Send any message to your Telegram bot to begin."
echo "   To view logs, run: docker compose logs -f"
echo "   To stop the bot, run: docker compose down"
echo "=================================================="
echo ""
