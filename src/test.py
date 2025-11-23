import socket
import threading
import time
import struct
import sys
import random

# === CẤU HÌNH TEST ===
SERVER_IP = "127.0.0.1"
SERVER_PORT = 20000
NUM_CLIENTS_TO_TEST = 6
TEST_DURATION_SECONDS = 60 # Chạy test trong 60 giây
BASE_USERNAME = "chaostester"
BASE_PASSWORD = "password123"

# Biến toàn cục (đơn giản) để các client biết tên nhau mà /msg
# Sẽ được cập nhật bởi các client khi chúng đăng nhập thành công
active_usernames = []
lock = threading.Lock()

# === CÁC HÀM HELPER GIAO THỨC MẠNG ===
# (Copy y hệt từ server/client)

def send_message(conn, msg):
    try:
        data = msg.encode('utf-8')
        length = len(data)
        conn.sendall(struct.pack('!I', length) + data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False
    except Exception:
        return False

def recv_message(conn, timeout=10.0):
    try:
        conn.settimeout(timeout)
        header = b''
        while len(header) < 4:
            chunk = conn.recv(4 - len(header))
            if not chunk: return None
            header += chunk
        
        length = struct.unpack('!I', header)[0]
        if length > 4096: return None 

        data = b''
        while len(data) < length:
            chunk = conn.recv(min(4096, length - len(data)))
            if not chunk: return None
            data += chunk
        
        return data.decode('utf-8')
    except socket.timeout:
        return None # Server không nói gì -> timeout
    except Exception:
        return None

# === LUỒNG NHẬN TIN (ĐỂ TỰ ĐỘNG /ACCEPT) ===

def receive_worker(sock, username, ident):
    """
    Đây là luồng "nghe" của mỗi client.
    Nó sẽ tự động chấp nhận hoặc từ chối các yêu cầu chat riêng.
    """
    while True:
        try:
            msg = recv_message(sock, timeout=TEST_DURATION_SECONDS + 10)
            if msg is None:
                # Bị timeout (do test đã xong) hoặc server ngắt kết nối
                # print(f"{ident} Luồng nhận: Bị timeout/ngắt kết nối.")
                break
            
            # --- TỰ ĐỘNG HÓA CHAT RIÊNG ---
            if "[THÔNG BÁO]" in msg and "muốn chat riêng" in msg:
                try:
                    # Phân tích xem ai gửi: "[THÔNG BÁO] user1 muốn chat riêng..."
                    sender = msg.split("] ")[1].split(" ")[0]
                    
                    # 70% chấp nhận, 30% từ chối
                    if random.random() < 0.7:
                        print(f"{ident} TỰ ĐỘNG: Chấp nhận {sender}")
                        send_message(sock, f"/accept {sender}")
                    else:
                        print(f"{ident} TỰ ĐỘNG: Từ chối {sender}")
                        send_message(sock, f"/decline {sender}")
                except Exception as e:
                    print(f"{ident} Lỗi phân tích thông báo: {e} | Msg: {msg}")
            
            # (Không cần in ra mọi tin nhắn, sẽ làm loãng log)

        except Exception as e:
            # print(f"{ident} Lỗi luồng nhận: {e}")
            break
    # print(f"{ident} Luồng nhận đã DỪNG.")


# === HÀM XỬ LÝ CỦA MỖI CLIENT TEST ===

def client_worker(client_id):
    """
    Hàm này được mỗi thread chạy, mô phỏng 1 client.
    """
    username = f"{BASE_USERNAME}{client_id}"
    password = BASE_PASSWORD
    ident = f"[Client {client_id} - {username}]"
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((SERVER_IP, SERVER_PORT))
    except Exception as e:
        print(f"{ident} KẾT NỐI THẤT BẠI: {e}")
        return

    # --- Giai đoạn 1: Xác thực ---
    try:
        response = recv_message(s, timeout=5) # Chờ server chào
        if not response or "XÁC THỰC:" not in response:
            raise Exception("Không nhận được lời chào XÁC THỰC")

        # 1. Thử ĐĂNG KÝ
        send_message(s, "DANGKY")
        recv_message(s) # "DANGKY:Nhập tên tài khoản"
        send_message(s, username)
        recv_message(s) # "DANGKY:Nhập mật khẩu"
        send_message(s, password)
        
        while "XÁC THỰC:" not in response:
            response = recv_message(s)
            if response is None: raise Exception("Mất kết nối khi đăng ký")

        # 2. Thử ĐĂNG NHẬP
        send_message(s, "DANGNHAP")
        recv_message(s) # "DANGNHAP:Nhập tên tài khoản"
        send_message(s, username)
        recv_message(s) # "DANGNHAP:Nhập mật khẩu"
        send_message(s, password)

        # 3. Chờ vào phòng
        while "OK:Đã vào phòng chung" not in response:
            response = recv_message(s, timeout=15)
            if response is None: raise Exception("Mất kết nối khi chờ vào phòng")
            
            if "LỖI:Server đã đầy" in response:
                print(f"{ident} BỊ TỪ CHỐI: Server đầy. (OK)")
                s.close()
                return
            if "LỖI:" in response:
                print(f"{ident} LỖI ĐĂNG NHẬP: {response}")

        print(f"{ident} === ĐĂNG NHẬP THÀNH CÔNG ===")
        with lock:
            active_usernames.append(username)

    except Exception as e:
        print(f"{ident} LỖI XÁC THỰC: {e}")
        s.close()
        return

    # --- Giai đoạn 2: Khởi động luồng nhận (cho chat riêng) ---
    threading.Thread(target=receive_worker, args=(s, username, ident), daemon=True).start()

    # --- Giai đoạn 3: Stress Test Hỗn Hợp (Luồng gửi) ---
    start_time = time.time()
    action_count = 0
    try:
        while time.time() - start_time < TEST_DURATION_SECONDS:
            action_count += 1
            
            # Lấy danh sách user khác để /msg
            with lock:
                other_users = [u for u in active_usernames if u != username]
            
            # --- CHỌN HÀNH ĐỘNG NGẪU NHIÊN ---
            choice = random.randint(1, 100)
            
            # 60% cơ hội: Gửi tin nhắn chung (Test DB Lock)
            if 1 <= choice <= 60:
                send_message(s, f"Tin nhắn chung hỗn độn số {action_count}")
            
            # 20% cơ hội: Gửi /list (Test Global Lock)
            elif 61 <= choice <= 80:
                send_message(s, "/list")
            
            # 15% cơ hội: Gửi /msg (Test pending_requests)
            elif 81 <= choice <= 95:
                if other_users:
                    target = random.choice(other_users)
                    send_message(s, f"/msg {target} Yêu cầu chat hỗn độn {action_count}")
            
            # 5% cơ hội: Gửi /back (Test đổi state)
            else:
                send_message(s, "/back") # Vô hại nếu đang ở public
            
            time.sleep(random.uniform(0.5, 1.5)) # Thời gian chờ ngẫu nhiên

    except Exception as e:
        print(f"{ident} LỖI KHI TEST: {e}")
    finally:
        print(f"{ident} === HOÀN TẤT TEST ({action_count} hành động) ===")
        send_message(s, "/exit")
        s.close()
        with lock:
            if username in active_usernames:
                active_usernames.remove(username)
        print(f"{ident} Đã ngắt kết nối.")


# === HÀM MAIN ĐỂ CHẠY TEST ===
if __name__ == "__main__":
    print(f"Bắt đầu stress test HỖN HỢP với {NUM_CLIENTS_TO_TEST} client...")
    print(f"Server: {SERVER_IP}:{SERVER_PORT}")
    print(f"Thời gian test: {TEST_DURATION_SECONDS} giây")
    print("="*40)
    
    # In ra cảnh báo về log
    print("LƯU Ý: Bạn sẽ thấy log [RECV] Lỗi unpack header hoặc [TIMEOUT]...")
    print("Điều này là BÌNH THƯỜNG vì 2 luồng đang cùng recv() trên 1 socket.")
    print("Mục tiêu của test này là xem SERVER có sập không.")
    print("="*40)
    time.sleep(3)


    threads = []
    for i in range(NUM_CLIENTS_TO_TEST):
        t = threading.Thread(target=client_worker, args=(i,), name=f"Client-{i}")
        threads.append(t)
        t.start()
        time.sleep(0.1) # Dãn cách các kết nối

    for t in threads:
        t.join()

    print("="*40)
    print("Stress test HỖN HỢP hoàn tất.")