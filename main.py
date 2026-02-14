# main.py
from app.bot import run_bot
from keep_alive import keep_alive  # Import cái "tim giả" vào

if __name__ == "__main__":
    keep_alive()  # Kích hoạt Web Server để Render không kill app
    run_bot()     # Sau đó mới chạy Bot Telegram