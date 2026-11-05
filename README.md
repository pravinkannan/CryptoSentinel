<div align="center">

<img src="assets/sentinel_banner.svg" alt="Crypto Sentinel Banner" width="100%" />

</div>

---

## 🚀 Setup & Installation

### 1. Clone & Install Dependencies
Ensure you have **Python 3.10+** installed:
```bash
git clone https://github.com/pravinkannan/CryptoSentinel.git
cd CryptoSentinel
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Create a `.env` file in the root directory by copying the example:
```bash
cp .env.example .env
```
Open `.env` and fill in your credentials:
```env
# Telegram Integration (Required for live signal alerts)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Google Gemini AI (Optional: for news sentiment audit and trade autopsies)
GEMINI_API_KEY=your_gemini_api_key_here
```

> **How to get your Telegram credentials:**
> - **Bot Token**: Open Telegram, search for [@BotFather](https://t.me/BotFather), send `/newbot`, follow the prompts, and copy the API token.
> - **Chat ID**: Message [@userinfobot](https://t.me/userinfobot) to get your Telegram user ID, or add your new bot to a group and message [@getmyid_bot](https://t.me/getmyid_bot) to get the group chat ID.

### 3. Run System Diagnostic Audit
Verify that your database, exchange APIs, and configuration are 100% operational (completes in ~0.5s):
```bash
python scripts/health_check.py
```

### 4. Launch Autonomous Sentinel Daemon
Start the live background daemon:
```bash
python main.py
```

---

<div align="center">
<sub>Confidential &amp; Proprietary Quantitative Architecture. Built for Autonomous High-Expectancy Execution.</sub>
</div>
