import socket
import threading
import sqlite3
import hashlib
import time
import os
import logging

clients = {}
client_states = {}  # {username: 'public' hoặc 'private'}
lock = threading.Lock()
db_lock = threading.Lock()
pending_requests = {}
private_rooms = {}

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
c.execute("""CREATE TABLE IF NOT EXISTS private_messages (id INTEGER PRIMARY KEY, sender TEXT, receiver TEXT, message TEXT, timestamp TEXT)""")
conn_db.commit()


def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def save_message(username, message):
    with db_lock:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
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

def broadcast(message, exclude=None):
    with lock:
        for conn, uname in clients.items():
            if uname != exclude:
                try:
                    conn.send(message)
                except:
                    pass

def notify_user(username, message):
    with lock:
        for conn, uname in clients.items():
            if uname == username:
                try:
                    conn.send(message)
                    return True
                except:
                    pass
    return False

def get_history(user1=None, user2=None, limit=20):
    """Lấy lịch sử chat"""
    with db_lock:
        if user1 and user2:
            # Lịch sử chat riêng
            c.execute("""SELECT sender, receiver, message, timestamp 
                        FROM private_messages 
                        WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
                        ORDER BY id DESC LIMIT ?""", 
                     (user1, user2, user2, user1, limit))
        else:
            # Lịch sử phòng chung
            c.execute("SELECT username, message, timestamp FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(c.fetchall()))

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
        clients[conn] = username
        client_states[username] = 'public'
    
    conn.send("Gõ /help để xem lệnh\n".encode('utf-8'))
    broadcast(f"[Hệ thống] {username} vào phòng".encode('utf-8'))
    
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
/changepass  - Thay đổi mật khẩu
/accept <tên> - Chấp nhận yêu cầu
/decline <tên> - Từ chối yêu cầu
/exit - Thoát
================
"""
                conn.send(help_text.encode('utf-8'))
            
            elif msg == '/list':
                with lock:
                    users = [u for u in clients.values() if u != username]
                conn.send(f"Online: {', '.join(users) if users else 'Không có'}\n".encode('utf-8'))
            
            elif msg.startswith('/msg '):
                parts = msg.split(' ', 2)
                if len(parts) < 3:
                    conn.send("Cách dùng: /msg <tên> <tin>\n".encode('utf-8'))
                    continue
                
                target_user = parts[1]
                message = parts[2]
                
                with lock:
                    if target_user not in clients.values():
                        conn.send(f"✗ {target_user} không online!\n".encode('utf-8'))
                        continue
                    
                    if client_states.get(target_user) == 'private':
                        conn.send(f"✗ {target_user} đang chat riêng!\n".encode('utf-8'))
                        continue
                
                pending_requests[(username, target_user)] = message
                notify_user(target_user, 
                    f"\n[YÊU CẦU] {username}: '{message}'\nGõ /accept {username} hoặc /decline {username}\n".encode('utf-8'))
                conn.send(f"✓ Đã gửi yêu cầu tới {target_user}\n".encode('utf-8'))
            
            elif msg.startswith('/accept '):
                requester = msg.split(' ', 1)[1]
                if (requester, username) not in pending_requests:
                    conn.send(f"✗ Không có yêu cầu từ {requester}!\n".encode('utf-8'))
                    continue
                del pending_requests[(requester, username)]
                
                with lock:
                    client_states[username] = 'private'
                    client_states[requester] = 'private'
                
                conn.send(f"✓ Đã chấp nhận! Chat riêng với {requester}\n".encode('utf-8'))
                notify_user(requester, f"\n✓ {username} đã chấp nhận yêu cầu!\n".encode('utf-8'))
            
            elif msg.startswith('/decline '):
                requester = msg.split(' ', 1)[1]
                
                if (requester, username) not in pending_requests:
                    conn.send(f"✗ Không có yêu cầu từ {requester}!\n".encode('utf-8'))
                    continue
                
                del pending_requests[(requester, username)]
                conn.send(f"✓ Đã từ chối {requester}\n".encode('utf-8'))
                notify_user(requester, f"\n✗ {username} đã từ chối\n".encode('utf-8'))
            
           
            elif msg in ['/history', '/his']:
                with lock:
                    if username in private_rooms:
                        partner = private_rooms[username]
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
                        c.execute("UPDATE users SET password=? WHERE username=?", 
                                 (hash_password(new_pass), username))
                        conn_db.commit()
                        conn.send("✓ Đổi mật khẩu thành công!\n".encode('utf-8'))
                    else:
                        conn.send("✗ Sai mật khẩu cũ!\n".encode('utf-8'))

            
            elif msg == '/exit':
                conn.send("Tạm biệt!\n".encode('utf-8'))
                break
            
            elif msg:
                save_message(username, msg)
                broadcast(f"[{username}] {msg}".encode('utf-8'), username)
        except:
            break
    
    with lock:
        if conn in clients:
            del clients[conn]
        if username in client_states:
            del client_states[username]
    logging.info(f"[NGẮT KẾT NỐI] {username}")
    broadcast(f"[Hệ thống] {username} rời phòng".encode('utf-8'))
    conn.close()

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

def admin_console():
    while True:
        try:
            cmd = input().strip().lower()
            
            if cmd == 'users':
                with lock:
                    if not clients:
                        print("Không có client nào")
                    else:
                        print(f"\n--- CLIENT ({len(clients)}) ---")
                        for conn, u in clients.items():
                            status = f"Riêng với {private_rooms[u]}" if u in private_rooms else "Phòng chung"
                            print(f"  • {u} - {status}")
                        print()
            
            elif cmd == 'rooms':
                with lock:
                    public_users = [u for u in clients.values() if u not in private_rooms]
                    private_pairs = {}
                    for u, partner in private_rooms.items():
                        pair = tuple(sorted([u, partner]))
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

threading.Thread(target=admin_console, daemon=True).start()
while True:
    conn, addr = server.accept()
    threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
 