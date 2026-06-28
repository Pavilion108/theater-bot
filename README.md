# 🎬 Theater Bot

A Telegram-driven bot that finds nearby movie theaters and provides booking links. Runs headlessly in GitHub Codespaces or Docker.

## ⚡ Quick Start (Codespaces)

1. Click the green **Code** button → **Codespaces** → **Create codespace on master**
2. In the terminal, run:
   ```bash
   bash start.sh
   ```
3. First time? It will ask you to edit `.env` — add your Telegram bot token
4. Open Telegram and message your bot!

## 🤖 Telegram Commands

| Command | Action |
|---------|--------|
| Any location (e.g. `Nerul`) | Search for nearby theaters |
| `all` or `0,1,2` | Select theaters to monitor |
| `/status` | Check bot health |
| `/restart` | Reset and search again |
| `STOP` | Shut down the bot |

## 🔑 Setup

Get your tokens:
- **Telegram**: Message [@BotFather](https://t.me/BotFather) → `/newbot`
- **Google Places** *(optional)*: [Google Cloud Console](https://console.cloud.google.com/apis/credentials)

## 🐳 Docker

```bash
cp .env.example .env   # edit with your tokens
docker compose up -d --build
```

## 📄 License

MIT
