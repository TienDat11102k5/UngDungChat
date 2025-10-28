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
                if (requester, username) not in pending_requests:
                    conn.send(f"✗ Không có yêu cầu từ {requester}!\n".encode('utf-8'))
                    continue

                del pending_requests[(requester, username)]
                update_user_state(username, 'private', requester)
                update_user_state(requester, 'private', username)

                conn.send(f"✓ Đã chấp nhận! Chat riêng với {requester}\n".encode('utf-8'))
                notify(requester, f"{username} đã chấp nhận yêu cầu!")

            elif msg.startswith('/decline '):
                requester = msg.split(' ', 1)[1]

                if (requester, username) not in pending_requests:
                    conn.send(f"✗ Không có yêu cầu từ {requester}!\n".encode('utf-8'))
                    continue

                del pending_requests[(requester, username)]
                conn.send(f"✓ Đã từ chối {requester}\n".encode('utf-8'))
                notify(requester, f"{username} đã từ chối")

            elif msg in ['/history', '/his']:
                rt, partner = get_current_state(username)
                if rt == 'private' and partner:
                    history = get_history(username, partner)
                    conn.send(f"\n=== LỊCH SỬ CHAT với {partner} ===\n".encode('utf-8'))
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
                rt, partner = get_current_state(username)
                if rt == 'public':
                    save_message(username, msg)
                    broadcast_public(username, msg, exclude_sender=True)
                elif rt == 'private' and partner:
                    save_message(username, msg, private_to=partner)
                    partner_conn = get_user_conn(partner)
                    if partner_conn:
                        try:
                            partner_conn.send(f"[{username}] {msg}".encode('utf-8'))
                        except:
                            pass
        except:
            break

    with lock:
        Client_list[:] = [(c, a, u, rt, tg) for c, a, u, rt, tg in Client_list if u != username]

    logging.info(f"[NGẮT KẾT NỐI] {username}")
    broadcast_public("MÁY CHỦ", f"{username} rời phòng", exclude_sender=False)
    conn.close()


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
                        for _, _, u, rt, tg in Client_list:
                            status = f"Riêng với {tg}" if rt == 'private' else "Phòng chung"
                            print(f"  • {u} - {status}")
                        print()

            elif cmd == 'rooms':
                with lock:
                    public_users = [u for _, _, u, rt, _ in Client_list if rt == 'public']
                    private_pairs = {}
                    for _, _, u, rt, tg in Client_list:
                        if rt == 'private':
                            pair = tuple(sorted([u, tg]))
                            private_pairs[pair] = True
                    print(f"\n--- PHÒNG ---")
                    print(f"Chung ({len(public_users)}): {', '.join(public_users) or 'Trống'}")
                    print(f"Riêng ({len(private_pairs)} cặp):")
                    for u1, u2 in private_pairs.keys():
                        print(f"  • {u1} <-> {u2}")
                    print()

            elif cmd == 'requests':
                with lock:
                    if not pending_requests:
                        print("Không có yêu cầu đang chờ\n")
                    else:
                        print(f"\n--- YÊU CẦU ({len(pending_requests)}) ---")
                        for (sender, receiver), msg in pending_requests.items():
                            print(f"  • {sender} -> {receiver}: '{msg}'")
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
