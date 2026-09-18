# Bleeding Moon Bot

## Windows setup

Open PowerShell and run:

```powershell
git clone https://github.com/BleedingM00n/bm-bot.git
cd bm-bot
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Put the Discord bot token after `DISCORD_TOKEN=` in `.env`, save the file, then start the bot:

```powershell
py bot.py
```

Keep the PowerShell window open while the bot is running. The bot creates `minecraft_links.db` automatically.

## If PowerShell blocks activation

Run this once in PowerShell, then activate the environment again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
