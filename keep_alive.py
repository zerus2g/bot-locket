import os
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive! DarkForge-X is watching."

def run():
    # Lấy PORT từ biến môi trường của Render, mặc định là 8080 nếu chạy local
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()