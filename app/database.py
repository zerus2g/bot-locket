import pymongo
from datetime import datetime
from app.config import MONGO_URI
import os

# Khởi tạo MongoDB Client
# Nếu không có MONGO_URI (chạy local test), sẽ faillback về in-memory hoặc lỗi
try:
    if MONGO_URI:
        client = pymongo.MongoClient(MONGO_URI)
        db = client.get_database() # Mặc định lấy database trong URI
    else:
        # Fallback for local testing/dev without Mongo
        # print("WARNING: No MONGO_URI found. Database features will not persist or work correctly.")
        client = None
        db = None
except Exception as e:
    print(f"MongoDB Connection Error: {e}")
    client = None
    db = None

# Collection names:
# usage_logs
# user_settings
# bot_config
# request_logs

def init_db():
    if db is not None:
        try:
            # Create indexes for performance
            db.usage_logs.create_index([("user_id", 1), ("date", 1)], unique=True)
            db.user_settings.create_index("user_id", unique=True)
            db.bot_config.create_index("key", unique=True)
            print("MongoDB Initialized & Indexes Created.")
        except Exception as e:
            print(f"DB Init Error: {e}")

def get_user_usage(user_id):
    if not db: return 0
    today = datetime.now().strftime("%Y-%m-%d")
    result = db.usage_logs.find_one({"user_id": user_id, "date": today})
    return result['count'] if result else 0

def increment_usage(user_id):
    if not db: return
    today = datetime.now().strftime("%Y-%m-%d")
    
    db.usage_logs.update_one(
        {"user_id": user_id, "date": today},
        {"$inc": {"count": 1}},
        upsert=True
    )

def check_can_request(user_id, max_limit=5):
    current = get_user_usage(user_id)
    return current < max_limit

def set_lang(user_id, lang):
    if not db: return
    db.user_settings.update_one(
        {"user_id": user_id},
        {"$set": {"language": lang}},
        upsert=True
    )

def set_user_name(user_id, name):
    if not db: return
    db.user_settings.update_one(
        {"user_id": user_id},
        {"$set": {"name": name}},
        upsert=True
    )

def get_lang(user_id):
    if not db: return None
    result = db.user_settings.find_one({"user_id": user_id})
    return result['language'] if result else None

def get_all_users():
    if not db: return []
    # Union of users from usage_logs and user_settings
    users_usage = db.usage_logs.distinct("user_id")
    users_settings = db.user_settings.distinct("user_id")
    return list(set(users_usage + users_settings))

def reset_usage(user_id):
    if not db: return
    today = datetime.now().strftime("%Y-%m-%d")
    db.usage_logs.delete_one({"user_id": user_id, "date": today})

def set_config(key, value):
    if not db: return
    db.bot_config.update_one(
        {"key": key},
        {"$set": {"value": value}},
        upsert=True
    )

def get_config(key, default=None):
    if not db: return default
    result = db.bot_config.find_one({"key": key})
    return result['value'] if result else default

def log_request(user_id, uid, status):
    if not db: return
    db.request_logs.insert_one({
        "user_id": user_id,
        "uid": uid,
        "status": status,
        "timestamp": datetime.utcnow()
    })

def get_stats():
    if not db: return {"total": 0, "success": 0, "fail": 0, "unique_users": 0}
    
    total = db.request_logs.count_documents({})
    success = db.request_logs.count_documents({"status": "SUCCESS"})
    fail = db.request_logs.count_documents({"status": {"$ne": "SUCCESS"}})
    unique_users = len(db.request_logs.distinct("user_id"))
    
    return {
        "total": total,
        "success": success,
        "fail": fail,
        "unique_users": unique_users
    }

init_db()
