import socket
import threading
import sqlite3
import hashlib
import time
import os
import logging

Client_list = []  # [(conn, addr, username, room_type, room_target)]
lock = threading.Lock()
db_lock = threading.Lock()
pending_requests = {}

LOG_FILE = "server_log.txt"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

conn_db = sqlite3.connect("chat.db", check_same_thread=False)
c = conn_db.cursor()
c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)")
c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, username TEXT, message TEXT, timestamp TEXT)")
c.execute("""
CREATE TABLE IF NOT EXISTS private_messages (
    id INTEGER PRIMARY KEY,
    sender TEXT,
    receiver TEXT,
    message TEXT,
    timestamp TEXT
)
""")
conn_db.commit()


def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()


def save_message(username, message, private_to=None):
    with db_lock:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        if private_to:
            c.execute("INSERT INTO private_messages VALUES (NULL, ?, ?, ?, ?)", (username, private_to, message, timestamp))
        else:
            c.execute("INSERT INTO messages VALUES (NULL, ?, ?, ?)", (username, message, timestamp))
        conn_db.commit()


def register_user(username, password):
    try:
        with db_lock:
            c.execute("INSERT INTO users VALUES (NULL, ?, ?)", (username, hash_password(password)))
            conn_db.commit()
        return True
    except:
        return False


def login_user(username, password):
    with db_lock:
        c.execute("SELECT password FROM users WHERE username=?", (username,))
        result = c.fetchone()
    return result and result[0] == hash_password(password)


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


def get_history(user1=None, user2=None, limit=20):
    """Lấy lịch sử chat"""
    with db_lock:
        if user1 and user2:
            c.execute("""
                SELECT sender, receiver, message, timestamp 
                FROM private_messages 
                WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
                ORDER BY id DESC LIMIT ?
            """, (user1, user2, user2, user1, limit))
        else:
            c.execute("SELECT username, message, timestamp FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(c.fetchall()))


def send_history(conn, username, room_type, target):
    """Gửi lịch sử chat cho user"""
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
    except Exception as e:
        logging.error(f"[HISTORY ERROR] {e}")


def get_user_conn(username):
    """Lấy connection của user"""
    with lock:
        for conn, addr, uname, room_type, room_target in Client_list:
            if uname == username:
                return conn
    return None


def get_current_state(username):
    """Lấy (room_type, room_target) của user"""
    with lock:
        for _, _, uname, room_type, room_target in Client_list:
            if uname == username:
                return room_type, room_target
    return None, None


def update_user_state(username, new_room_type, new_room_target):
    """Cập nhật trạng thái user"""
    with lock:
        for idx, (conn, addr, uname, room_type, room_target) in enumerate(Client_list):
            if uname == username:
                Client_list[idx] = (conn, addr, uname, new_room_type, new_room_target)
                logging.info(f"[CẬP NHẬT] {username}: {new_room_type}/{new_room_target}")
                return True
    return False


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


def handle_client(conn, addr):
    username = None
    conn.send("1-Đăng ký | 2-Đăng nhập: ".encode('utf-8'))
    choice = conn.recv(1024).decode('utf-8').strip()
    conn.send("Username: ".encode('utf-8'))
    username = conn.recv(1024).decode('utf-8').strip()
    conn.send("Password: ".encode('utf-8'))
    password = conn.recv(1024).decode('utf-8').strip()

    if choice == '1':
        if not register_user(username, password):
            conn.send("✗ Username đã tồn tại!\n".encode('utf-8'))
            logging.warning(f"[ĐĂNG KÝ THẤT BẠI] {username}")
            conn.close()
            return
        conn.send("✓ Đăng ký thành công!\n".encode('utf-8'))
        logging.info(f"[ĐĂNG KÝ] {username}")
    else:
        if not login_user(username, password):
            conn.send("✗ Sai thông tin!\n".encode('utf-8'))
            logging.warning(f"[ĐĂNG NHẬP THẤT BẠI] {username}")
            conn.close()
            return
        conn.send("✓ Đăng nhập thành công!\n".encode('utf-8'))
        logging.info(f"[ĐĂNG NHẬP] {username}")

    with lock:
        Client_list.append((conn, addr, username, "public", None))

    time.sleep(0.1)
    send_history(conn, username, "public", None)
    time.sleep(0.2)
    conn.send("OK:Đã vào phòng chung. Gõ /help để xem lệnh.".encode('utf-8'))
    time.sleep(0.1)
    broadcast_public("MÁY CHỦ", f"{username} đã tham gia phòng chung", True)

    while True:
        try:
            data = conn.recv(1024)
            if not data:
                break
            msg = data.decode('utf-8').strip()

            if msg == '/help':
                help_text = """
=== LỆNH ===
/help - Hiện menu
/list - Danh sách online
/msg <tên> <tin> - Yêu cầu chat riêng
/history - Xem lịch sử chat 
/changepass <cũ> <mới> - Thay đổi mật khẩu
/accept <tên> - Chấp nhận yêu cầu
/decline <tên> - Từ chối yêu cầu
/back - Về phòng chung
/exit - Thoát
================
"""
                conn.send(help_text.encode('utf-8'))

            elif msg in ['/list', '/ls']:
                with lock:
                    users = [f"{u} ({'chung' if rt == 'public' else f'riêng-{tg}'})"
                             for _, _, u, rt, tg in Client_list if u != username]
                    conn.send(f"Online ({len(users)}): {', '.join(users) if users else 'Không có'}\n".encode('utf-8'))

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
                        conn.send("Không thể gửi yêu cầu cho chính mình".encode('utf-8'))
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
    
                # 1. Kiểm tra request
                with lock:
                    if (requester, username) not in pending_requests:
                        conn.send(f"Không có yêu cầu từ {requester}".encode('utf-8'))
                        continue
                    del pending_requests[(requester, username)]
    
                logging.info(f"[ACCEPT START] {username} chấp nhận {requester}")
    
                # 2. Lấy connection requester
                requester_conn = get_user_conn(requester)
                if not requester_conn:
                    conn.send(f"Lỗi: {requester} đã offline".encode('utf-8'))
                    continue
    
                # 3. Lấy trạng thái hiện tại
                room_type, _ = get_current_state(username)
                req_room_type, _ = get_current_state(requester)
    
                # 4. Thông báo rời phòng chung
                if room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{username} đã rời phòng chung", True)
                if req_room_type == "public":
                    broadcast_public("MÁY CHỦ", f"{requester} đã rời phòng chung", True)
    
                # 5. Cập nhật trạng thái
                update_user_state(username, "private", requester)
                update_user_state(requester, "private", username)
    
                # 6. Gửi cho requester
                try:
                    requester_conn.send(f"OK:Đã vào chat riêng với {username}. Gõ /back về phòng chung.".encode('utf-8'))
                    time.sleep(0.1)
                    send_history(requester_conn, requester, "private", username)
                except Exception as e:
                    logging.error(f"[ACCEPT ERROR] {e}")
    
                # 7. Gửi cho mình
                time.sleep(0.1)
                conn.send(f"OK:Đã vào chat riêng với {requester}. Gõ /back về phòng chung.".encode('utf-8'))
                time.sleep(0.1)
                send_history(conn, username, "private", requester)
    
                logging.info(f"[CHAT RIÊNG] {username} <-> {requester}")

            elif msg.startswith('/decline '):
                requester = msg.split(' ', 1)[1]

                if (requester, username) not in pending_requests:
                    conn.send(f"Không có yêu cầu từ {requester}".encode('utf-8'))
                    continue

                del pending_requests[(requester, username)]
                conn.send(f"Đã từ chối {requester}".encode('utf-8'))
                notify(requester, f"{username} đã từ chối")
                
                
            elif msg == '/back':
                room_type, room_target = get_current_state(username)
                if room_type == "public":
                    conn.send("Bạn đang ở phòng chung".encode('utf-8'))
                    continue
                # Thông báo cho người kia
                notify(room_target, f"{username} đã về phòng chung")
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
                room_type, room_target = get_current_state(username)
                if room_type == "private" and room_target:
                    history = get_history(username, room_target)
                    conn.send(f"\n=== LỊCH SỬ CHAT với {room_target} ===\n".encode('utf-8'))
                    for sender, receiver, text, ts in history:
                        prefix = "Bạn" if sender == username else sender
                        conn.send(f"[{ts}] {prefix}: {text}\n".encode('utf-8'))
                else:
                    history = get_history()
                    conn.send("\n=== LỊCH SỬ PHÒNG CHUNG ===\n".encode('utf-8'))
                    for uname, text, ts in history:
                        conn.send(f"[{ts}] {uname}: {text}\n".encode('utf-8'))
                conn.send("==================\n".encode('utf-8'))

            elif msg.startswith('/changepass '):
                parts = msg.split(' ')
                if len(parts) != 3:
                    conn.send("Cách dùng: /changepass <mật khẩu cũ> <mật khẩu mới>\n".encode('utf-8'))
                    continue

                old_pass = parts[1]
                new_pass = parts[2]

                with db_lock:
                    c.execute("SELECT password FROM users WHERE username=?", (username,))
                    current_hash = c.fetchone()[0]

                    if current_hash == hash_password(old_pass):
                        c.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pass), username))
                        conn_db.commit()
                        conn.send("✓ Đổi mật khẩu thành công!\n".encode('utf-8'))
                    else:
                        conn.send("✗ Sai mật khẩu cũ!\n".encode('utf-8'))

            elif msg == '/exit':
                conn.send("Tạm biệt!\n".encode('utf-8'))
                break

            elif msg:
                room_type, room_target = get_current_state(username)
                
                if room_type == "public":
                    save_message(username, msg)
                    broadcast_public(username, msg)
                    logging.info(f"[CHUNG] {username}: {msg}")
                
                elif room_type == "private":
                    save_message(username, msg, room_target)
                    partner_conn = get_user_conn(room_target)
                    if partner_conn:
                        partner_rt, partner_tg = get_current_state(room_target)
                        if partner_rt == "private" and partner_tg == username:
                            try:
                                partner_conn.send(f"[{username}] {msg}".encode('utf-8'))
                            except:
                                pass
                    logging.info(f"[RIÊNG] {username} -> {room_target}: {msg}")
                    
        except ConnectionResetError:
            logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - ConnectionResetError")
            break
        except BrokenPipeError:
            logging.warning(f"[NGẮT ĐỘT NGỘT] {username} - BrokenPipeError")
            break
        except Exception as e:
            logging.error(f"[LỖI] {username}: {e}")
            break
    
    # Cleanup khi disconnect
    if username:
        final_room_type, final_room_target = get_current_state(username)
        if final_room_type is not None:
            cleanup_user(username, final_room_type, final_room_target)
    
    try:
        conn.close()
    except:
        pass


def admin_console():
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
                        print("Không có yêu cầu đang chờ\n")
                    else:
                        print(f"\n--- YÊU CẦU ({len(pending_requests)}) ---")
                        for (sender, receiver), ts in pending_requests.items():
                            print(f"  • {sender} -> {receiver} ({int(time.time()-ts)}s trước)")
                        print()

            elif cmd == 'exit':
                print("\n[Đang tắt server...]")
                os._exit(0)

            else:
                if cmd:
                    print("Lệnh: users | rooms | requests | exit")

        except KeyboardInterrupt:
            print("\n[Tắt server bằng Ctrl+C]")
            os._exit(0)


# Khởi động server
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 20000))
server.listen()
logging.info("=" * 50)
logging.info("SERVER BẬT - 127.0.0.1:20000")
logging.info("=" * 50)

print("\n=== LỆNH ADMIN ===")
print("users   - Xem danh sách client")
print("rooms   - Xem phòng chat")
print("requests- Xem yêu cầu đang chờ")
print("exit    - Tắt server")
print("==================\n")

threading.Thread(target=admin_console, daemon=True).start()

while True:
    conn, addr = server.accept()
    threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()