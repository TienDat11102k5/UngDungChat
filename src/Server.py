import socket
import threading
import time
import hashlib
import sqlite3
import logging
import os
import re
import struct

# === CẤU HÌNH GIỚI HẠN ===
MAX_CLIENTS = 5
MAX_MESSAGE_LENGTH = 500
MAX_USERNAME_LENGTH = 20
MIN_USERNAME_LENGTH = 3
MAX_PASSWORD_LENGTH = 50
MIN_PASSWORD_LENGTH = 6
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]+$')
REQUEST_TIMEOUT = 60
SOCKET_BACKLOG = 10
AUTH_TIMEOUT = 60 
CHAT_TIMEOUT = 600

Client_list = [] # conn, addr, username, room_type, room_target
Max_data = 1024
Local_IP = "127.0.0.1"
Local_Port = 20000  
lock = threading.Lock()
pending_requests = {}

DB_FILE = "chat_server.db"
LOG_FILE = "server_log.txt"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
def send_message(conn, msg):
    """Gửi tin nhắn với header chứa độ dài (4 bytes)"""
    try:
        msg_bytes = msg.encode('utf-8')
        msg_length = len(msg_bytes)
        conn.sendall(struct.pack('!I', msg_length) + msg_bytes)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        logging.error(f"[SEND ERROR] {e}")
        return False
    except Exception as e:
        logging.error(f"[SEND ERROR] {e}")
        return False

def recv_message(conn):
    """Nhận tin nhắn với header chứa độ dài (4 bytes)"""
    try:
        raw_msglen = b''
        while len(raw_msglen) < 4:
            chunk = conn.recv(4 - len(raw_msglen))
            if not chunk:
                return None 
            raw_msglen += chunk
        
        msg_length = struct.unpack('!I', raw_msglen)[0]
        if msg_length > MAX_MESSAGE_LENGTH * 2:
            logging.warning(f"[RECV] Tin nhắn quá lớn: {msg_length} bytes")
            return None
        msg_data = b''
        while len(msg_data) < msg_length:
            chunk = conn.recv(min(msg_length - len(msg_data), Max_data))
            if not chunk:
                return None
            msg_data += chunk
        
        return msg_data.decode('utf-8')
    except struct.error:
        logging.error("[RECV] Lỗi unpack header")
        return None
    except UnicodeDecodeError:
        logging.error("[RECV] Lỗi decode UTF-8")
        return None
    except Exception as e:
        logging.error(f"[RECV ERROR] {e}")
        return None

def db_init():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS public_messages (id INTEGER PRIMARY KEY, username TEXT, message TEXT, timestamp TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS private_messages (id INTEGER PRIMARY KEY, sender TEXT, receiver TEXT, message TEXT, timestamp TEXT)")
    conn.commit()
    c.execute("SELECT COUNT(*) FROM users")
    count = c.fetchone()[0]
    conn.close()
    return count

def hash_pwd(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def validate_username(username):
    if not username:
        return False, "Tên tài khoản không được để trống"
    if len(username) < MIN_USERNAME_LENGTH:
        return False, f"Tên tài khoản phải có ít nhất {MIN_USERNAME_LENGTH} ký tự"
    if len(username) > MAX_USERNAME_LENGTH:
        return False, f"Tên tài khoản không được vượt quá {MAX_USERNAME_LENGTH} ký tự"
    if not USERNAME_PATTERN.match(username):
        return False, "Tên tài khoản chỉ được chứa chữ, số và dấu gạch dưới"
    if username.upper() in ['ADMIN', 'SERVER', 'SYSTEM', 'ROOT']:
        return False, "Tên tài khoản không được sử dụng từ khóa hệ thống"
    return True, ""

def validate_password(password):
    if not password:
        return False, "Mật khẩu không được để trống"
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự"
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Mật khẩu không được vượt quá {MAX_PASSWORD_LENGTH} ký tự"
    return True, ""

def validate_message(msg):
    if not msg or not msg.strip():
        return False, "Tin nhắn không được để trống"
    if len(msg) > MAX_MESSAGE_LENGTH:
        return False, f"Tin nhắn không được vượt quá {MAX_MESSAGE_LENGTH} ký tự"
    dangerous_chars = ['\x00', '\x01', '\x02']
    if any(char in msg for char in dangerous_chars):
        return False, "Tin nhắn chứa ký tự không hợp lệ"
    return True, ""

def get_client_count():
    with lock:
        return len(Client_list)

def cleanup_expired_requests():
    current_time = time.time()
    expired = []
    
    with lock:
        for (sender, receiver), timestamp in list(pending_requests.items()):
            if current_time - timestamp > REQUEST_TIMEOUT:
                expired.append((sender, receiver))
        
        for sender, receiver in expired:
            del pending_requests[(sender, receiver)]
    
    for sender, receiver in expired:
        notify_result = notify(sender, f"Yêu cầu chat với {receiver} đã hết hạn ({REQUEST_TIMEOUT}s)")
        if notify_result:
            logging.info(f"[HẾT HẠN] {sender} -> {receiver} (đã thông báo người gửi)")
        else:
            logging.info(f"[HẾT HẠN] {sender} -> {receiver} (người gửi offline)")
        notify_result = notify(receiver, f"Yêu cầu chat từ {sender} đã hết hạn ({REQUEST_TIMEOUT}s)")
        if not notify_result:
            logging.info(f"[HẾT HẠN] Không thể thông báo cho {receiver} (offline)")
    
    return len(expired)

def save_msg(username, msg, private_to=None):
    try:
        db = sqlite3.connect(DB_FILE)
        c = db.cursor()
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        if private_to:
            c.execute("INSERT INTO private_messages (sender, receiver, message, timestamp) VALUES (?, ?, ?, ?)",
                      (username, private_to, msg, ts))
        else:
            c.execute("INSERT INTO public_messages (username, message, timestamp) VALUES (?, ?, ?)",
                      (username, msg, ts))
        db.commit()
        db.close()
    except Exception as e:
        logging.error(f"[DB ERROR] save_msg: {e}")

def get_history(user1=None, user2=None, limit=50):
    try:
        db = sqlite3.connect(DB_FILE)
        c = db.cursor()
        if user1 and user2:
            c.execute("SELECT sender, receiver, message, timestamp FROM private_messages WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) ORDER BY id DESC LIMIT ?",
                      (user1, user2, user2, user1, limit))
        else:
            c.execute("SELECT username, message, timestamp FROM public_messages ORDER BY id DESC LIMIT ?", (limit,))
        msgs = c.fetchall()
        db.close()
        return list(reversed(msgs))
    except Exception as e:
        logging.error(f"[DB ERROR] get_history: {e}")
        return []

def send_history(conn, username, room_type, target):
    try:
        msgs = get_history(username, target) if room_type == "private" else get_history()
        if msgs:
            send_message(conn, f"LỊCH SỬ:=== {'CHAT với ' + target if target else 'PHÒNG CHUNG'} ===")
            time.sleep(0.05)
            for msg_data in msgs:
                if room_type == "private":
                    sender, _, txt, ts = msg_data
                    prefix = "Bạn" if sender == username else sender
                    send_message(conn, f"LỊCH SỬ:[{ts}] {prefix}: {txt}")
                else:
                    uname, txt, ts = msg_data
                    send_message(conn, f"LỊCH SỬ:[{ts}] {uname}: {txt}")
                time.sleep(0.02)
            send_message(conn, "LỊCH SỬ:=== HẾT ===")
            time.sleep(0.05)
    except Exception as e:
        logging.error(f"[ERROR] send_history: {e}")

def notify(username, msg):
    target_conn = None
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                target_conn = c
                break
    
    if target_conn:
        return send_message(target_conn, f"[THÔNG BÁO] {msg}")
    return False

def broadcast_public(sender, msg, exclude_sender=True):
    targets = []
    with lock:
        for c, _, u, rt, _ in Client_list:
            if rt == "public" and (not exclude_sender or u != sender):
                targets.append((c, u))
    
    for c, u in targets:
        if sender == "MÁY CHỦ":
            send_message(c, f"[MÁY CHỦ] {msg}")
        else:
            send_message(c, f"[{sender}] {msg}")

def cleanup_user(username, room_type, room_target):
    if room_type == "public":
        broadcast_public("MÁY CHỦ", f"{username} đã rời phòng chung", False)
    elif room_type == "private" and room_target:
        notify(room_target, f"{username} đã ngắt kết nối")
        partner_conn = None
        partner_username = None
        with lock:
            for i, (c, a, u, rt, tg) in enumerate(Client_list):
                if u == room_target and rt == "private" and tg == username:
                    partner_conn = c
                    partner_username = u
                    Client_list[i] = (c, a, u, "public", None)
                    break
        if partner_conn and partner_username:
            try:
                send_message(partner_conn, "OK:Đã quay lại phòng chung (người kia ngắt kết nối).")
                time.sleep(0.1)
                send_history(partner_conn, partner_username, "public", None)
                time.sleep(0.1)
                broadcast_public("MÁY CHỦ", f"{partner_username} đã tham gia phòng chung", False)
            except Exception as e:
                logging.error(f"[ERROR] cleanup_user notify: {e}")
    
    with lock:
        Client_list[:] = [(c, a, u, rt, tg) for c, a, u, rt, tg in Client_list if u != username]
    logging.info(f"[NGẮT KẾT NỐI] {username}")

def get_user_conn(username):
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                return c
    return None

def get_current_state(username):
    with lock:
        for _, _, u, rt, tg in Client_list:
            if u == username:
                return rt, tg
    return None, None

def update_user_state(username, new_room_type, new_room_target):
    with lock:
        for i, (c, a, u, rt, tg) in enumerate(Client_list):
            if u == username:
                Client_list[i] = (c, a, u, new_room_type, new_room_target)
                logging.info(f"[CẬP NHẬT] {username}: {rt}/{tg} -> {new_room_type}/{new_room_target}")
                return True
    return False

def handle_client(conn, addr):
    username = None 
    try:
        conn.settimeout(AUTH_TIMEOUT)
        
        if get_client_count() >= MAX_CLIENTS:
            send_message(conn, "LỖI:Server đã đầy. Vui lòng thử lại sau.")
            logging.warning(f"[TỪ CHỐI] {addr} - Server đầy ({MAX_CLIENTS} clients)")
            return
    
        while True:
            send_message(conn, "XÁC THỰC:DANGNHAP hoặc DANGKY?")
            auth_type = recv_message(conn)
            if not auth_type:
                return
            auth_type = auth_type.strip().upper()
            if auth_type == "THOAT":
                return
            if auth_type in ["DANGNHAP", "DANGKY"]:
                send_message(conn, f"{auth_type}:Nhập tên tài khoản")
                username_input = recv_message(conn)
                if not username_input:
                    return
                username_input = username_input.strip()
                if username_input.upper() == "BACK":
                    continue
                
                valid, error_msg = validate_username(username_input)
                if not valid:
                    send_message(conn, f"LỖI:{error_msg}")
                    continue
                
                send_message(conn, f"{auth_type}:Nhập mật khẩu")
                password = recv_message(conn)
                if not password:
                    return
                password = password.strip()
                if password.upper() == "BACK":
                    continue
                
                valid, error_msg = validate_password(password)
                if not valid:
                    send_message(conn, f"LỖI:{error_msg}")
                    continue
                
                db = sqlite3.connect(DB_FILE)
                c = db.cursor()
                if auth_type == "DANGKY":
                    try:
                        c.execute("INSERT INTO users VALUES (NULL, ?, ?, ?)", 
                                  (username_input, hash_pwd(password), time.strftime('%Y-%m-%d %H:%M:%S')))
                        db.commit()
                        send_message(conn, f"OK:Tài khoản '{username_input}' đã tạo!")
                        logging.info(f"[ĐĂNG KÝ] {username_input}")
                    except sqlite3.IntegrityError:
                        send_message(conn, "LỖI:Tên tài khoản đã tồn tại")
                    except Exception as e:
                        send_message(conn, "LỖI:Lỗi tạo tài khoản")
                        logging.error(f"[DB ERROR] Register: {e}")
                    db.close()
                    continue
                else:
                    c.execute("SELECT password_hash FROM users WHERE username=?", (username_input,))
                    result = c.fetchone()
                    db.close()
                    if not result:
                        send_message(conn, "LỖI:Tài khoản không tồn tại")
                        continue
                    if result[0] != hash_pwd(password):
                        send_message(conn, "LỖI:Sai mật khẩu")
                        continue
                    with lock:
                        if any(u == username_input for _, _, u, _, _ in Client_list):
                            send_message(conn, "LỖI:Tài khoản đã đăng nhập")
                            continue 
                    username = username_input
                    send_message(conn, f"OK:Chào mừng {username}!")
                    logging.info(f"[ĐĂNG NHẬP] {username} từ {addr[0]}")
                    break
        
        conn.settimeout(CHAT_TIMEOUT)
        with lock:
            Client_list.append((conn, addr, username, "public", None))
        time.sleep(0.1)
        send_history(conn, username, "public", None)
        time.sleep(0.2)
        send_message(conn, "OK:Đã vào phòng chung. Gõ /help để xem lệnh.")
        time.sleep(0.1)
        broadcast_public("MÁY CHỦ", f"{username} đã tham gia phòng chung", True)
        
        while True:
            msg = recv_message(conn)
            if not msg:
                logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - connection closed")
                break
            msg = msg.strip()
            if not msg:
                continue
            
            room_type, room_target = get_current_state(username)
            if room_type is None:
                break
            
            if msg == '/help':
                help_lines = [
                    "=== LỆNH ===",
                    "/list, /ls - Danh sách online",
                    f"/msg <tên> <tin> - Yêu cầu chat riêng (hết hạn sau {REQUEST_TIMEOUT}s)",
                    "/accept <tên> - Chấp nhận",
                    "/decline <tên> - Từ chối",
                    "/back - Về phòng chung",
                    "/history, /his - Xem lịch sử",
                    "/changepass <cũ> <mới> - Đổi pass",
                    "/exit - Thoát",
                    "",
                    "=== GIỚI HẠN ===",
                    f"- Tin nhắn: tối đa {MAX_MESSAGE_LENGTH} ký tự",
                    f"- Username: {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} ký tự (chữ, số, _)",
                    f"- Password: {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} ký tự",
                    f"- Yêu cầu chat: tự động hủy sau {REQUEST_TIMEOUT} giây"
                ]
                for line in help_lines:
                    send_message(conn, line)
                    time.sleep(0.01)
            
            elif msg in ['/list', '/ls']:
                with lock:
                    users = [f"{u} ({'chung' if rt=='public' else f'riêng-{tg}'})" 
                             for _, _, u, rt, tg in Client_list if u != username]
                send_message(conn, f"Online ({len(users)}/{MAX_CLIENTS}): {', '.join(users) if users else 'Không có'}")
            
            elif msg.startswith('/msg '):
                parts = msg.split(' ', 2)
                if len(parts) < 3:
                    send_message(conn, "Cách dùng: /msg <tên> <tin>")
                    continue
                target, message = parts[1], parts[2]
                
                valid, error_msg = validate_message(message)
                if not valid:
                    send_message(conn, f"Lỗi: {error_msg}")
                    continue
                
                valid, error_msg = validate_username(target)
                if not valid:
                    send_message(conn, f"Lỗi: Tên người nhận không hợp lệ")
                    continue
                
                with lock:
                    target_exists = False
                    target_in_private = False
                    for _, _, u, rt, _ in Client_list:
                        if u == target:
                            target_exists = True
                            if rt == "private":
                                target_in_private = True
                            break
                    if target == username:
                        send_message(conn, "Không thể gửi yêu cầu chat riêng cho chính mình")
                        continue
                    if not target_exists:
                        send_message(conn, f"Lỗi: {target} không online")
                        continue
                    if target_in_private:
                        send_message(conn, f"Lỗi: {target} đang chat riêng")
                        continue
                    pending_requests[(username, target)] = time.time()
                
                notify(target, f"{username} muốn chat riêng: '{message[:50]}...'\nGõ /accept {username} hoặc /decline {username} (hết hạn sau {REQUEST_TIMEOUT}s)")
                send_message(conn, f"Đã gửi yêu cầu tới {target} (hết hạn sau {REQUEST_TIMEOUT}s)")
                logging.info(f"[YÊU CẦU] {username} -> {target}")
            
            elif msg.startswith('/accept '):
                requester = msg.split(' ', 1)[1]
                
                valid, _ = validate_username(requester)
                if not valid:
                    send_message(conn, f"Tên không hợp lệ")
                    continue
                
                cleanup_expired_requests()
                
                requester_conn = None
                requester_room_type = None
                accepter_room_type = None
                
                with lock:
                    if (requester, username) not in pending_requests:
                        send_message(conn, f"Không có yêu cầu từ {requester} (có thể đã hết hạn)")
                        continue
                    
                    requester_found = False
                    for i, (c, a, u, rt, tg) in enumerate(Client_list):
                        if u == requester:
                            requester_conn = c
                            requester_room_type = rt
                            requester_found = True
                            break
                    
                    if not requester_found:
                        send_message(conn, f"Lỗi: {requester} đã offline")
                        del pending_requests[(requester, username)]
                        continue
                    
                    for i, (c, a, u, rt, tg) in enumerate(Client_list):
                        if u == username:
                            accepter_room_type = rt
                            break
                    
                    del pending_requests[(requester, username)]
                    
                    for i, (c, a, u, rt, tg) in enumerate(Client_list):
                        if u == username:
                            Client_list[i] = (c, a, u, "private", requester)
                        elif u == requester:
                            Client_list[i] = (c, a, u, "private", username)
                
                logging.info(f"[ACCEPT] {username} chấp nhận {requester}")
                
                if accepter_room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{username} đã rời phòng chung", True)
                
                if requester_room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{requester} đã rời phòng chung", True)
                
                if requester_conn:
                    send_message(requester_conn, f"OK:Đã vào chat riêng với {username}. Gõ /back về phòng chung.")
                    time.sleep(0.1)
                    send_history(requester_conn, requester, "private", username)
                
                time.sleep(0.1)
                send_message(conn, f"OK:Đã vào chat riêng với {requester}. Gõ /back về phòng chung.")
                time.sleep(0.1)
                send_history(conn, username, "private", requester)
                
                logging.info(f"[CHAT RIÊNG] {username} <-> {requester}")
            
            elif msg.startswith('/decline '):
                requester = msg.split(' ', 1)[1]
                
                valid, _ = validate_username(requester)
                if not valid:
                    send_message(conn, f"Tên không hợp lệ")
                    continue
                
                cleanup_expired_requests()
                
                with lock:
                    if (requester, username) not in pending_requests:
                        send_message(conn, f"Không có yêu cầu từ {requester} (có thể đã hết hạn)")
                        continue
                    del pending_requests[(requester, username)]
                send_message(conn, f"Đã từ chối {requester}")
                notify(requester, f"{username} đã từ chối")
                logging.info(f"[TỪ CHỐI] {username} từ chối {requester}")
            
            elif msg == '/back':
                if room_type == "public":
                    send_message(conn, "Bạn đang ở phòng chung")
                    continue
                
                partner_conn = None
                partner_username = None
                
                with lock:
                    for i, (c, a, u, rt, tg) in enumerate(Client_list):
                        if u == room_target and rt == "private" and tg == username:
                            partner_conn = c
                            partner_username = u
                            Client_list[i] = (c, a, u, "public", None)
                        elif u == username:
                            Client_list[i] = (c, a, u, "public", None)
                
                if partner_conn and partner_username:
                    send_message(partner_conn, "OK:Đã quay lại phòng chung.")
                    time.sleep(0.1)
                    send_history(partner_conn, partner_username, "public", None)
                    time.sleep(0.1)
                    broadcast_public("MÁY CHỦ", f"{partner_username} đã tham gia phòng chung", True)
                
                if room_target:
                    notify(room_target, f"{username} đã về phòng chung")
                
                time.sleep(0.1)
                send_history(conn, username, "public", None)
                time.sleep(0.2)
                send_message(conn, "OK:Đã quay lại phòng chung.")
                
                time.sleep(0.1)
                broadcast_public("MÁY CHỦ", f"{username} đã tham gia phòng chung", True)
                
                logging.info(f"[/BACK] {username} và {partner_username if partner_username else 'N/A'} về phòng chung")
            
            elif msg in ['/history', '/his']:
                send_history(conn, username, room_type, room_target)
            
            elif msg.startswith('/changepass '):
                parts = msg.split(' ')
                if len(parts) != 3:
                    send_message(conn, "Cách dùng: /changepass <cũ> <mới>")
                    continue
                
                old_pass, new_pass = parts[1], parts[2]
                
                valid, error_msg = validate_password(new_pass)
                if not valid:
                    send_message(conn, f"LỖI: {error_msg}")
                    continue
                
                db = sqlite3.connect(DB_FILE)
                c = db.cursor()
                c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
                if c.fetchone()[0] == hash_pwd(old_pass):
                    c.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_pwd(new_pass), username))
                    db.commit()
                    send_message(conn, "Đổi mật khẩu thành công!")
                    logging.info(f"[ĐỔI PASS] {username}")
                else:
                    send_message(conn, "LỖI: Sai mật khẩu cũ")
                db.close()
            
            elif msg == '/exit':
                send_message(conn, "Tạm biệt!")
                break
            
            else:
                valid, error_msg = validate_message(msg)
                if not valid:
                    send_message(conn, f"[LỖI] {error_msg}")
                    continue
                
                if room_type == "public":
                    save_msg(username, msg)
                    broadcast_public(username, msg)
                    logging.info(f"[CHUNG] {username}: {msg[:50]}...")
                elif room_type == "private":
                    save_msg(username, msg, room_target)
                    partner_conn = get_user_conn(room_target)
                    if partner_conn:
                        partner_rt, partner_tg = get_current_state(room_target)
                        if partner_rt == "private" and partner_tg == username:
                            send_message(partner_conn, f"[{username}] {msg}")
                    logging.info(f"[RIÊNG] {username} -> {room_target}: {msg[:50]}...")
                    
    except socket.timeout:
        if username:
            logging.warning(f"[TIMEOUT] {username} - Không hoạt động trong {AUTH_TIMEOUT if not username else CHAT_TIMEOUT}s")
        else:
            logging.warning(f"[TIMEOUT] {addr} - Timeout khi xác thực")
    except ConnectionResetError:
        logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - ConnectionResetError")
    except BrokenPipeError:
        logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - BrokenPipeError")
    except UnicodeDecodeError:
        logging.error(f"[LỖI] {username or addr} - Lỗi decode UTF-8")
    except Exception as e:
        logging.error(f"[LỖI] {username or addr}: {e}")
    finally:
        if username:
            final_room_type, final_room_target = get_current_state(username)
            if final_room_type is not None:
                cleanup_user(username, final_room_type, final_room_target)
        try:
            conn.close()
        except (OSError, AttributeError):
            pass

count = db_init()
ServerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ServerSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ServerSocket.bind((Local_IP, Local_Port))
ServerSocket.listen(SOCKET_BACKLOG)

logging.info("=" * 50)
logging.info(f"SERVER BẬT - {Local_IP}:{Local_Port}")
logging.info(f"Giới hạn: {MAX_CLIENTS} clients, tin nhắn {MAX_MESSAGE_LENGTH} ký tự")
logging.info(f"Timeout: Xác thực {AUTH_TIMEOUT}s, Chat {CHAT_TIMEOUT}s")
logging.info(f"Database: {count} tài khoản")
logging.info("=" * 50)

def accept_clients():
    while True:
        try:
            conn, addr = ServerSocket.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except OSError:
            break
        except Exception as e:
            logging.error(f"[ACCEPT ERROR] {e}")
            break

threading.Thread(target=accept_clients, daemon=True).start()

def cleanup_requests_periodically():
    while True:
        time.sleep(10)
        expired_count = cleanup_expired_requests()
        if expired_count > 0:
            logging.info(f"[DỌN DẸP] Xóa {expired_count} yêu cầu hết hạn")

threading.Thread(target=cleanup_requests_periodically, daemon=True).start()

print("\nLệnh: users | rooms | requests | limits | exit")
while True:
    try:
        cmd = input().strip().lower()
        
        if cmd == 'users':
            with lock:
                if not Client_list:
                    print("Không có client nào")
                else:
                    print(f"\n--- CLIENT ({len(Client_list)}/{MAX_CLIENTS}) ---")
                    for _, addr, u, rt, tg in Client_list:
                        status = "Chung" if rt == "public" else f"Riêng với {tg}"
                        print(f"  {u} | {addr[0]}:{addr[1]} | {status}")
                    print()
        
        elif cmd == 'rooms':
            with lock:
                public = [u for _, _, u, rt, _ in Client_list if rt == "public"]
                private_pairs = {tuple(sorted((u, tg))) for _, _, u, rt, tg in Client_list if rt == "private"}
                print(f"\n--- PHÒNG ---")
                print(f"Chung ({len(public)}): {', '.join(public) or 'Trống'}")
                print(f"Riêng ({len(private_pairs)} cặp): {', '.join([f'{a}<->{b}' for a, b in private_pairs]) or 'Không'}\n")
        
        elif cmd == 'requests':
            with lock:
                if not pending_requests:
                    print("Không có yêu cầu\n")
                else:
                    print(f"\n--- YÊU CẦU ({len(pending_requests)}) ---")
                    for (s, r), ts in pending_requests.items():
                        print(f"  {s} -> {r} ({int(time.time()-ts)}s trước)")
                    print()
        
        elif cmd == 'limits':
            print(f"\n--- GIỚI HẠN SERVER ---")
            print(f"Max clients: {MAX_CLIENTS}")
            print(f"Max message length: {MAX_MESSAGE_LENGTH} ký tự")
            print(f"Username: {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} ký tự (a-z, A-Z, 0-9, _)")
            print(f"Password: {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} ký tự")
            print(f"Request timeout: {REQUEST_TIMEOUT} giây")
            print(f"Auth timeout: {AUTH_TIMEOUT} giây")
            print(f"Chat timeout: {CHAT_TIMEOUT} giây")
            print(f"Current clients: {get_client_count()}/{MAX_CLIENTS}")
            print()
        
        elif cmd == 'exit':
            logging.info("SERVER TẮT")
            ServerSocket.close()
            os._exit(0)
        
        else:
            print("Lệnh: users | rooms | requests | limits | exit")
            
    except (KeyboardInterrupt, EOFError):
        logging.info("\nSERVER TẮT (Ctrl+C)")
        ServerSocket.close()
        os._exit(0)