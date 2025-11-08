Fix deadlock - Tách việc lấy connection và gửi message
python
# CŨ (trong notify):
def notify(username, msg):
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                try:
                    c.send(...)  # ❌ GỬI TRONG LOCK
                except:
                    pass

# MỚI:
def notify(username, msg):
    target_conn = None
    with lock:
        for c, _, u, _, _ in Client_list:
            if u == username:
                target_conn = c
                break
    
    # ✅ GỬI NGOÀI LOCK
    if target_conn:
        try:
            target_conn.send(f"[THÔNG BÁO] {msg}".encode('utf-8'))
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logging.error(f"[ERROR] notify to {username}: {e}")
            return False
    return False
Lý do: Tránh deadlock khi gửi message trong lock