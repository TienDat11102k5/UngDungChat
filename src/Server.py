import socket
import threading
import time
import hashlib
import sqlite3
import logging
import os
import re

MAX_CLIENTS = 5
MAX_MESSAGE_LENGTH = 500
MAX_USERNAME_LENGTH = 20
MIN_USERNAME_LENGTH = 3
MAX_PASSWORD_LENGTH = 50
MIN_PASSWORD_LENGTH = 6
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]+$')
REQUEST_TIMEOUT = 60
SOCKET_BACKLOG = 10

Client_list = []  # (conn, addr, username, room_type, room_target)
Max_data = 1024
Local_IP = "127.0.0.1"
Local_Port = 20000  
lock = threading.Lock()
def get_client_count():
    with lock:
        return len(Client_list)
pending_requests = {}  # {(sender, receiver): timestamp}

DB_FILE = "chat_server.db"
LOG_FILE = "server_log.txt"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

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
        logging.error(f"[DB ERROR] {e}")

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
    except:
        return []

def send_history(conn, username, room_type, target):
    try:
        msgs = get_history(username, target) if room_type == "private" else get_history()
        if msgs:
            conn.send(f"LỊCH SỬ:=== {'CHAT với ' + target if target else 'PHÒNG CHUNG'} ===".encode('utf-8'))
            time.sleep(0.05)
            for msg_data in msgs:
                if room_type == "private":
                    sender, _, txt, ts = msg_data
                    prefix = "Bạn" if sender == username else sender
                    conn.send(f"LỊCH SỬ:[{ts}] {prefix}: {txt}".encode('utf-8'))
                else:
                    uname, txt, ts = msg_data
                    conn.send(f"LỊCH SỬ:[{ts}] {uname}: {txt}".encode('utf-8'))
                time.sleep(0.02)
            conn.send("LỊCH SỬ:=== HẾT ===".encode('utf-8'))
            time.sleep(0.05)
    except:
        pass

def notify(username, msg):
    """Gửi thông báo cho một user cụ thể"""
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                try:
                    c.send(f"[THÔNG BÁO] {msg}".encode('utf-8'))
                    return True
                except:
                    pass
    return False

def broadcast_public(sender, msg, exclude_sender=True):
    """Gửi tin nhắn tới tất cả người trong phòng chung"""
    with lock:
        for c, _, u, rt, _ in Client_list:
            if rt == "public" and (not exclude_sender or u != sender):
                try:
                    if sender == "MÁY CHỦ":
                        c.send(f"[MÁY CHỦ] {msg}".encode('utf-8'))
                    else:
                        c.send(f"[{sender}] {msg}".encode('utf-8'))
                except:
                    pass

def cleanup_user(username, room_type, room_target):
    """Dọn dẹp khi user disconnect"""
    if room_type == "public":
        broadcast_public("MÁY CHỦ", f"{username} đã rời phòng chung", False)
    elif room_type == "private" and room_target:
        notify(room_target, f"{username} đã ngắt kết nối")
        # Đưa người kia về phòng chung
        with lock:
            for i, (c, a, u, rt, tg) in enumerate(Client_list):
                if u == room_target and rt == "private" and tg == username:
                    Client_list[i] = (c, a, u, "public", None)
                    try:
                        c.send("OK:Đã quay lại phòng chung (người kia ngắt kết nối).".encode('utf-8'))
                        time.sleep(0.1)
                        send_history(c, u, "public", None)
                        time.sleep(0.1)
                        broadcast_public("MÁY CHỦ", f"{u} đã tham gia phòng chung", False)
                    except:
                        pass
                    break
    
    # Xóa user khỏi danh sách
    with lock:
        Client_list[:] = [(c, a, u, rt, tg) for c, a, u, rt, tg in Client_list if u != username]
    logging.info(f"[NGẮT KẾT NỐI] {username}")

def get_user_conn(username):
    """Lấy connection của user"""
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                return c
    return None

def get_current_state(username):
    """Lấy (room_type, room_target) mới nhất của user từ Client_list."""
    with lock:
        for _, _, u, rt, tg in Client_list:
            if u == username:
                return rt, tg
    return None, None

def update_user_state(username, new_room_type, new_room_target):
    """Cập nhật trạng thái của user trong Client_list"""
    with lock:
        for i, (c, a, u, rt, tg) in enumerate(Client_list):
            if u == username:
                Client_list[i] = (c, a, u, new_room_type, new_room_target)
                logging.info(f"[CẬP NHẬT] {username}: {rt}/{tg} -> {new_room_type}/{new_room_target}")
                return True
    return False

def broadcast_public(sender, msg, exclude_sender=True):
    targets = []
    with lock:
        for c, _, u, rt, _ in Client_list:
            if rt == "public" and (not exclude_sender or u != sender):
                targets.append((c, u))
    for c, u in targets:
        try:
            if sender == "MÁY CHỦ":
                c.send(f"[MÁY CHỦ] {msg}".encode('utf-8'))
            else:
                c.send(f"[{sender}] {msg}".encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logging.error(f"[ERROR] broadcast to {u}: {e}")

def handle_client(conn, addr):
    username = None 
    if get_client_count() >= MAX_CLIENTS:
        conn.send("LỖI:Server đã đầy. Vui lòng thử lại sau.".encode('utf-8'))
        logging.warning(f"[TỪ CHỐI] {addr} - Server đầy ({MAX_CLIENTS} clients)")
        conn.close()
        return
    try:
        # === ĐĂNG NHẬP ===
        while True:
            conn.send("XÁC THỰC:DANGNHAP hoặc DANGKY?".encode('utf-8'))
            auth_type = conn.recv(Max_data).decode('utf-8').strip().upper()
            if not auth_type or auth_type == "THOAT":
                return
            if auth_type in ["DANGNHAP", "DANGKY"]:
                conn.send(f"{auth_type}:Nhập tên tài khoản".encode('utf-8'))
                username_input = conn.recv(Max_data).decode('utf-8').strip()
                if not username_input or username_input.upper() == "BACK":
                    continue
                conn.send(f"{auth_type}:Nhập mật khẩu".encode('utf-8'))
                password = conn.recv(Max_data).decode('utf-8').strip()
                if not password or password.upper() == "BACK":
                    continue
                db = sqlite3.connect(DB_FILE)
                c = db.cursor()
                if auth_type == "DANGKY":
                    try:
                        c.execute("INSERT INTO users VALUES (NULL, ?, ?, ?)", 
                                  (username_input, hash_pwd(password), time.strftime('%Y-%m-%d %H:%M:%S')))
                        db.commit()
                        conn.send(f"OK:Tài khoản '{username_input}' đã tạo!".encode('utf-8'))
                        logging.info(f"[ĐĂNG KÝ] {username_input}")
                    except sqlite3.IntegrityError:
                        conn.send("LỖI:Tên tài khoản đã tồn tại".encode('utf-8'))
                    db.close()
                    continue
                else:  # ĐĂNG NHẬP
                    c.execute("SELECT password_hash FROM users WHERE username=?", (username_input,))
                    result = c.fetchone()
                    db.close()
                    if not result:
                        conn.send("LỖI:Tài khoản không tồn tại".encode('utf-8'))
                        continue
                    if result[0] != hash_pwd(password):
                        conn.send("LỖI:Sai mật khẩu".encode('utf-8'))
                        continue
                    with lock:
                        if any(u == username_input for _, _, u, _, _ in Client_list):
                            conn.send("LỖI:Tài khoản đã đăng nhập".encode('utf-8'))
                            continue 
                    username = username_input
                    conn.send(f"OK:Chào mừng {username}!".encode('utf-8'))
                    logging.info(f"[ĐĂNG NHẬP] {username}")
                    break
        
        # === TỰ ĐỘNG VÀO PHÒNG CHUNG ===
        with lock:
            Client_list.append((conn, addr, username, "public", None))
        time.sleep(0.1)
        send_history(conn, username, "public", None)
        time.sleep(0.2)
        conn.send("OK:Đã vào phòng chung. Gõ /help để xem lệnh.".encode('utf-8'))
        time.sleep(0.1)
        broadcast_public("MÁY CHỦ", f"{username} đã tham gia phòng chung", True)
        # === VÒNG LẶP CHAT ===
        while True:
            data = conn.recv(Max_data)   
            if not data:
                logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - connection closed")
                break 
            msg = data.decode('utf-8').strip()
            if not msg:
                continue
            # Lấy trạng thái mới nhất
            room_type, room_target = get_current_state(username)
            if room_type is None:
                break
            # === XỬ LÝ LỆNH ===
            if msg == '/help':
                conn.send("""
=== LỆNH ===
/list, /ls - Danh sách online
/msg <tên> <tin> - Yêu cầu chat riêng
/accept <tên> - Chấp nhận
/decline <tên> - Từ chối
/back - Về phòng chung
/history, /his - Xem lịch sử
/changepass <cũ> <mới> - Đổi pass
/exit - Thoát
""".encode('utf-8'))
            
            elif msg in ['/list', '/ls']:
                with lock:
                    users = [f"{u} ({'chung' if rt=='public' else f'riêng-{tg}'})" 
                             for _, _, u, rt, tg in Client_list if u != username]
                conn.send(f"Online ({len(users)}): {', '.join(users) if users else 'Không có'}".encode('utf-8'))  
            elif msg.startswith('/msg '):
                parts = msg.split(' ', 2)
                if len(parts) < 3:
                    conn.send("Cách dùng: /msg <tên> <tin>".encode('utf-8'))
                    continue
                target, message = parts[1], parts[2]
                
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
                        conn.send("Không thể gửi yêu cầu chat riêng cho chính mình".encode('utf-8'))
                        continue
                    if not target_exists:
                        conn.send(f"Lỗi: {target} không online".encode('utf-8'))
                        continue
                    if target_in_private:
                        conn.send(f"Lỗi: {target} đang chat riêng".encode('utf-8'))
                        continue
                    pending_requests[(username, target)] = time.time()
                
                notify(target, f"{username} muốn chat riêng: '{message}'\nGõ /accept {username} hoặc /decline {username}")
                conn.send(f"Đã gửi yêu cầu tới {target}".encode('utf-8'))
                logging.info(f"[YÊU CẦU] {username} -> {target}")
            
            elif msg.startswith('/accept '):
                requester = msg.split(' ', 1)[1]
                # Kiểm tra request tồn tại
                with lock:
                    if (requester, username) not in pending_requests:
                        conn.send(f"Không có yêu cầu từ {requester}".encode('utf-8'))
                        continue
                    del pending_requests[(requester, username)]
                logging.info(f"[ACCEPT START] {username} chấp nhận {requester}")
                
                # Lấy connection của requester
                requester_conn = get_user_conn(requester)
                if not requester_conn:
                    conn.send(f"Lỗi: {requester} đã offline".encode('utf-8'))
                    continue
                
                # 1. Thông báo rời phòng chung (nếu đang ở đó)
                if room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{username} đã rời phòng chung", True)
                
                # 2. Cập nhật trạng thái người accept (username)
                update_user_state(username, "private", requester)
                
                # 3. Kiểm tra và cập nhật trạng thái requester
                req_room_type, _ = get_current_state(requester)
                if req_room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{requester} đã rời phòng chung", True)
                
                update_user_state(requester, "private", username)
                
                # 4. Gửi thông báo cho requester
                try:
                    requester_conn.send(f"OK:Đã vào chat riêng với {username}. Gõ /back về phòng chung.".encode('utf-8'))
                    time.sleep(0.1)
                    send_history(requester_conn, requester, "private", username)
                except Exception as e:
                    logging.error(f"[ACCEPT ERROR] Gửi cho {requester}: {e}")
                
                # 5. Gửi thông báo cho mình
                time.sleep(0.1)
                conn.send(f"OK:Đã vào chat riêng với {requester}. Gõ /back về phòng chung.".encode('utf-8'))
                time.sleep(0.1)
                send_history(conn, username, "private", requester)
                
                logging.info(f"[CHAT RIÊNG] {username} <-> {requester}")
            
            elif msg.startswith('/decline '):
                requester = msg.split(' ', 1)[1]
                with lock:
                    if (requester, username) not in pending_requests:
                        conn.send(f"Không có yêu cầu từ {requester}".encode('utf-8'))
                        continue
                    del pending_requests[(requester, username)]
                conn.send(f"Đã từ chối {requester}".encode('utf-8'))
                notify(requester, f"{username} đã từ chối")
            
            elif msg == '/back':
                if room_type == "public":
                    conn.send("Bạn đang ở phòng chung".encode('utf-8'))
                    continue
                
                # Thông báo cho người kia
                notify(room_target, f"{username} đã về phòng chung")
                
                # Chuyển người kia về phòng chung
                partner_conn = get_user_conn(room_target)
                if partner_conn:
                    update_user_state(room_target, "public", None)
                    try:
                        partner_conn.send("OK:Đã quay lại phòng chung.".encode('utf-8'))
                        time.sleep(0.1)
                        send_history(partner_conn, room_target, "public", None)
                        time.sleep(0.1)
                        broadcast_public("MÁY CHỦ", f"{room_target} đã tham gia phòng chung", True)
                    except:
                        pass
                
                # Chuyển mình về phòng chung
                update_user_state(username, "public", None)
                time.sleep(0.1)
                send_history(conn, username, "public", None)
                time.sleep(0.2)
                conn.send("OK:Đã quay lại phòng chung.".encode('utf-8'))
                time.sleep(0.1)
                broadcast_public("MÁY CHỦ", f"{username} đã tham gia phòng chung", True)
            
            elif msg in ['/history', '/his']:
                send_history(conn, username, room_type, room_target)
            
            elif msg.startswith('/changepass '):
                parts = msg.split(' ')
                if len(parts) != 3:
                    conn.send("Cách dùng: /changepass <cũ> <mới>".encode('utf-8'))
                    continue
                db = sqlite3.connect(DB_FILE)
                c = db.cursor()
                c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
                if c.fetchone()[0] == hash_pwd(parts[1]):
                    c.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_pwd(parts[2]), username))
                    db.commit()
                    conn.send("Đổi mật khẩu thành công!".encode('utf-8'))
                    logging.info(f"[ĐỔI PASS] {username}")
                else:
                    conn.send("LỖI: Sai mật khẩu cũ".encode('utf-8'))
                db.close()
            
            elif msg == '/exit':
                conn.send("Tạm biệt!".encode('utf-8'))
                break
            
            else:
                # Tin nhắn thường
                if room_type == "public":
                    save_msg(username, msg)
                    broadcast_public(username, msg)
                    logging.info(f"[CHUNG] {username}: {msg}")
                elif room_type == "private":
                    save_msg(username, msg, room_target)
                    # Gửi cho người nhận
                    partner_conn = get_user_conn(room_target)
                    if partner_conn:
                        # Kiểm tra người nhận vẫn đang chat với mình
                        partner_rt, partner_tg = get_current_state(room_target)
                        if partner_rt == "private" and partner_tg == username:
                            try:
                                partner_conn.send(f"[{username}] {msg}".encode('utf-8'))
                            except:
                                pass
                    logging.info(f"[RIÊNG] {username} -> {room_target}: {msg}")
                    
    except ConnectionResetError:
        logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - ConnectionResetError")
    except BrokenPipeError:
        logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - BrokenPipeError")
    except Exception as e:
        logging.error(f"[LỖI] {username or addr}: {e}")
    finally:
        if username:
            final_room_type, final_room_target = get_current_state(username)
            if final_room_type is not None:
                cleanup_user(username, final_room_type, final_room_target)
        try:
            conn.close()
        except:
            pass

# === KHỞI ĐỘNG SERVER ===
count = db_init()
ServerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ServerSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ServerSocket.bind((Local_IP, Local_Port))
ServerSocket.listen(10)

logging.info("=" * 50)
logging.info(f"SERVER BẬT - {Local_IP}:{Local_Port} - {count} tài khoản")
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

# === LỆNH SERVER ===
print("Lệnh: users | rooms | requests | exit")
while True:
    try:
        cmd = input().strip().lower()
        
        if cmd == 'users':
            with lock:
                if not Client_list:
                    print("Không có client nào")
                else:
                    print(f"\n--- CLIENT ({len(Client_list)}) ---")
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
        
        elif cmd == 'exit':
            logging.info("SERVER TẮT")
            ServerSocket.close()
            os._exit(0)
        
        else:
            if cmd:
                print("Lệnh: users | rooms | requests | exit")
            
    except (KeyboardInterrupt, EOFError):
        logging.info("\nSERVER TẮT (Ctrl+C)")
        ServerSocket.close()
        os._exit(0)