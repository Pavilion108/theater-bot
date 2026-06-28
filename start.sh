#!/bin/bash
# =============================================================================
# 🎬 Theater Bot — One-Command Setup & Launch (Alpine/Local Mode)
# =============================================================================
# Usage:  bash start.sh
# =============================================================================

set -e

echo "=================================================="
echo "🎬 Theater Bot — Setup & Launch"
echo "=================================================="

# Auto-detect python command
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "❌ Python not found! Please install Python 3."
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────
# Handle Alpine Linux environment (Install Chromium directly)
# ──────────────────────────────────────────────────────────────────────
if command -v apk &>/dev/null; then
    echo "📦 Detected Alpine Linux. Installing system Chromium for Playwright..."
    sudo apk add --no-cache chromium chromium-chromedriver &>/dev/null || apk add --no-cache chromium chromium-chromedriver &>/dev/null || echo "⚠️ Could not auto-install chromium. You might need root."
    
    # Tell Playwright not to download its own incompatible binaries
    export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
fi

# Step 1: Create virtual environment to avoid PEP 668 issues
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    $PY -m venv "$VENV_DIR" || {
        echo "⚠️ Failed to create venv. Trying to install venv module..."
        if command -v apk &>/dev/null; then
            sudo apk add --no-cache py3-virtualenv &>/dev/null || apk add --no-cache py3-virtualenv &>/dev/null
        fi
        $PY -m venv "$VENV_DIR"
    }
fi

echo "📦 Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Step 2: Ensure pip is installed
if ! python3 -m pip --version &>/dev/null; then
    echo "📦 Installing pip inside venv..."
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py --quiet
    rm -f /tmp/get-pip.py
fi

# Step 3: Install dependencies
echo "📦 Installing Python dependencies..."
python3 -m pip install -r requirements.txt --quiet
if [ -z "$PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" ]; then
    echo "📦 Installing Playwright browsers..."
    playwright install chromium --with-deps &>/dev/null || playwright install chromium &>/dev/null
fi
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

python3 theater_automation.py
