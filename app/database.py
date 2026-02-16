import sqlite3
import json
import string
import random
from datetime import datetime, timedelta

DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (
                    user_id INTEGER,
                    date TEXT,
                    count INTEGER,
                    PRIMARY KEY (user_id, date)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    uid TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS vip_keys (
                    key TEXT PRIMARY KEY,
                    key_type TEXT,
                    created_by INTEGER,
                    used_by INTEGER DEFAULT NULL,
                    used_at DATETIME DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (referrer_id, referred_id)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_vip (
                    user_id INTEGER PRIMARY KEY,
                    vip_until DATETIME DEFAULT NULL,
                    referral_code TEXT UNIQUE,
                    ref_bonus INTEGER DEFAULT 0
                )''')
    conn.commit()
    conn.close()

def get_user_usage(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT count FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def increment_usage(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    c.execute("SELECT count FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today))
    result = c.fetchone()
    
    if result:
        new_count = result[0] + 1
        c.execute("UPDATE usage_logs SET count = ? WHERE user_id = ? AND date = ?", (new_count, user_id, today))
    else:
        c.execute("INSERT INTO usage_logs (user_id, date, count) VALUES (?, ?, ?)", (user_id, today, 1))
        
    conn.commit()
    conn.close()

def get_daily_limit():
    """Get current daily limit from DB, default 5."""
    val = get_config("daily_limit", "5")
    try:
        return int(val)
    except ValueError:
        return 5

def is_vip(user_id):
    """Check if user has active VIP status."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT vip_until FROM user_vip WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    if not result or not result[0]:
        return False
    try:
        vip_until = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
        return datetime.now() < vip_until
    except (ValueError, TypeError):
        return False

def get_user_ref_bonus(user_id):
    """Get referral bonus limit for user."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT ref_bonus FROM user_vip WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def check_can_request(user_id):
    """Check if user can make a request. VIP = unlimited, otherwise base + ref bonus."""
    if is_vip(user_id):
        return True
    current = get_user_usage(user_id)
    total_limit = get_daily_limit() + get_user_ref_bonus(user_id)
    return current < total_limit

def get_user_total_limit(user_id):
    """Get total daily limit for a user (base + ref bonus)."""
    if is_vip(user_id):
        return -1  # unlimited
    return get_daily_limit() + get_user_ref_bonus(user_id)

def set_lang(user_id, lang):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_settings (user_id, language) VALUES (?, ?)", (user_id, lang))
    conn.commit()
    conn.close()

def get_lang(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT language FROM user_settings WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT user_id FROM usage_logs UNION SELECT user_id FROM user_settings")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def reset_usage(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("DELETE FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today))
    conn.commit()
    conn.close()

def set_config(key, value):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_config(key, default=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else default

def log_request(user_id, uid, status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO request_logs (user_id, uid, status) VALUES (?, ?, ?)", (user_id, uid, status))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM request_logs")
    total = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM request_logs WHERE status = 'SUCCESS'")
    success = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM request_logs WHERE status != 'SUCCESS'")
    fail = c.fetchone()[0]
    
    c.execute("SELECT COUNT(DISTINCT user_id) FROM request_logs")
    unique_users = c.fetchone()[0]
    
    conn.close()
    return {
        "total": total,
        "success": success,
        "fail": fail,
        "unique_users": unique_users
    }

def save_token_set(index, token_data):
    """Save a token set to DB for persistence across restarts."""
    key = f"token_set_{index}"
    value = json.dumps(token_data)
    set_config(key, value)

def load_token_sets():
    """Load all saved token sets from DB. Returns dict {index: token_data}."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT key, value FROM bot_config WHERE key LIKE 'token_set_%'")
    results = {}
    for row in c.fetchall():
        try:
            idx = int(row[0].replace("token_set_", ""))
            results[idx] = json.loads(row[1])
        except (ValueError, json.JSONDecodeError):
            pass
    conn.close()
    return results

# ===== KEY SYSTEM =====

def _gen_key_code():
    """Generate a random key like LG-XXXX-XXXX."""
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"LG-{part1}-{part2}"

def generate_keys(count, key_type, admin_id):
    """Generate VIP keys. Returns list of key strings."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    keys = []
    for _ in range(count):
        for attempt in range(10):
            key = _gen_key_code()
            try:
                c.execute("INSERT INTO vip_keys (key, key_type, created_by) VALUES (?, ?, ?)",
                          (key, key_type, admin_id))
                keys.append(key)
                break
            except sqlite3.IntegrityError:
                continue
    conn.commit()
    conn.close()
    return keys

KEY_TYPE_DAYS = {"1d": 1, "7d": 7, "30d": 30}

def redeem_key(key_str, user_id):
    """Redeem a VIP key. Returns (success, message_key, days)."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT key_type, used_by FROM vip_keys WHERE key = ?", (key_str,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "redeem_invalid", 0
    if row[1] is not None:
        conn.close()
        return False, "redeem_used", 0
    
    key_type = row[0]
    days = KEY_TYPE_DAYS.get(key_type, 0)
    if days == 0:
        conn.close()
        return False, "redeem_invalid", 0
    
    now = datetime.now()
    # Extend VIP if already active
    c.execute("SELECT vip_until FROM user_vip WHERE user_id = ?", (user_id,))
    vip_row = c.fetchone()
    if vip_row and vip_row[0]:
        try:
            current_vip = datetime.strptime(vip_row[0], "%Y-%m-%d %H:%M:%S")
            if current_vip > now:
                new_until = current_vip + timedelta(days=days)
            else:
                new_until = now + timedelta(days=days)
        except (ValueError, TypeError):
            new_until = now + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)
    
    until_str = new_until.strftime("%Y-%m-%d %H:%M:%S")
    
    # Mark key as used
    c.execute("UPDATE vip_keys SET used_by = ?, used_at = ? WHERE key = ?",
              (user_id, now.strftime("%Y-%m-%d %H:%M:%S"), key_str))
    
    # Upsert user VIP
    c.execute("SELECT user_id FROM user_vip WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute("UPDATE user_vip SET vip_until = ? WHERE user_id = ?", (until_str, user_id))
    else:
        c.execute("INSERT INTO user_vip (user_id, vip_until) VALUES (?, ?)", (user_id, until_str))
    
    conn.commit()
    conn.close()
    return True, "redeem_success", days

def list_unused_keys():
    """List all unused VIP keys."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT key, key_type, created_at FROM vip_keys WHERE used_by IS NULL ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"key": r[0], "type": r[1], "created": r[2]} for r in rows]

# ===== REFERRAL SYSTEM =====

def get_or_create_referral_code(user_id):
    """Get or create a unique referral code for user."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT referral_code FROM user_vip WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        conn.close()
        return row[0]
    
    # Generate unique code
    for _ in range(20):
        code = "REF-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        try:
            c.execute("SELECT user_id FROM user_vip WHERE user_id = ?", (user_id,))
            if c.fetchone():
                c.execute("UPDATE user_vip SET referral_code = ? WHERE user_id = ?", (code, user_id))
            else:
                c.execute("INSERT INTO user_vip (user_id, referral_code) VALUES (?, ?)", (user_id, code))
            conn.commit()
            conn.close()
            return code
        except sqlite3.IntegrityError:
            continue
    conn.close()
    return None

def find_user_by_referral_code(code):
    """Find user_id by referral code."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM user_vip WHERE referral_code = ?", (code,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def process_referral(referrer_id, new_user_id):
    """Process a referral. Returns True if successful (new referral)."""
    if referrer_id == new_user_id:
        return False
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if already referred
    c.execute("SELECT 1 FROM referrals WHERE referred_id = ?", (new_user_id,))
    if c.fetchone():
        conn.close()
        return False
    
    try:
        c.execute("INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                  (referrer_id, new_user_id))
    except sqlite3.IntegrityError:
        conn.close()
        return False
    
    # Give referrer +2 bonus
    c.execute("SELECT user_id FROM user_vip WHERE user_id = ?", (referrer_id,))
    if c.fetchone():
        c.execute("UPDATE user_vip SET ref_bonus = ref_bonus + 2 WHERE user_id = ?", (referrer_id,))
    else:
        c.execute("INSERT INTO user_vip (user_id, ref_bonus) VALUES (?, 2)", (referrer_id,))
    
    # Give new user +1 bonus
    c.execute("SELECT user_id FROM user_vip WHERE user_id = ?", (new_user_id,))
    if c.fetchone():
        c.execute("UPDATE user_vip SET ref_bonus = ref_bonus + 1 WHERE user_id = ?", (new_user_id,))
    else:
        c.execute("INSERT INTO user_vip (user_id, ref_bonus) VALUES (?, 1)", (new_user_id,))
    
    conn.commit()
    conn.close()
    return True

def get_referral_stats(user_id):
    """Get referral stats for a user."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
    total_refs = c.fetchone()[0]
    
    c.execute("SELECT ref_bonus FROM user_vip WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    bonus = row[0] if row else 0
    
    conn.close()
    return {"total_refs": total_refs, "bonus": bonus}

def get_vip_expiry(user_id):
    """Get VIP expiry date string, or None."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT vip_until FROM user_vip WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return None

init_db()
