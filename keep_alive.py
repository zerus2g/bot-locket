# keep_alive.py
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive! DarkForge-X is watching."

def run():
    # Render yêu cầu chạy trên 0.0.0.0
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()