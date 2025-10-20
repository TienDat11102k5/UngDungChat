import socket
import threading
import sqlite3
import hashlib
import time

clients = {}
lock = threading.Lock()
db_lock = threading.Lock()

conn_db = sqlite3.connect("chat.db", check_same_thread=False)
c = conn_db.cursor()
c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)")
c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, username TEXT, message TEXT, timestamp TEXT)")
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
        for conn in clients:
            if conn != exclude:
                try:
                    conn.send(message)
                except:
                    pass

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
            conn.close()
            return
        conn.send("✓ Đăng ký thành công!\n".encode('utf-8'))
    else:
        if not login_user(username, password):
            conn.send("✗ Sai thông tin!\n".encode('utf-8'))
            conn.close()
            return
        conn.send("✓ Đăng nhập thành công!\n".encode('utf-8'))
    
    with lock:
        clients[conn] = username
    
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
/exit - Thoát
================
"""
                conn.send(help_text.encode('utf-8'))
            
            elif msg == '/list':
                with lock:
                    users = [u for u in clients.values() if u != username]
                conn.send(f"Online: {', '.join(users) if users else 'Không có'}\n".encode('utf-8'))
            
            elif msg == '/exit':
                conn.send("Tạm biệt!\n".encode('utf-8'))
                break
            
            elif msg:
                save_message(username, msg)
                broadcast(f"[{username}] {msg}".encode('utf-8'), conn)
        except:
            break
    
    with lock:
        if conn in clients:
            del clients[conn]
    broadcast(f"[Hệ thống] {username} rời phòng".encode('utf-8'))
    conn.close()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 20000))
server.listen()
print("Server với Commands...")

while True:
    conn, addr = server.accept()
    threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()