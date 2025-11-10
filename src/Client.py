import socket
import threading
import sys
import os
import struct
import logging


SERVER_IP = "127.0.0.1"
SERVER_PORT = 20000
MAX_DATA = 1024

client_socket = None
running = True
authenticated = False

def clear_screen():
    """Xóa màn hình console"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_separator():
    """In dòng phân cách"""
    print("=" * 60)
    
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
        if msg_length > 10*1024*1024:  # 10 MB
            print(f"\n[CẢNH BÁO] Tin nhắn quá lớn: {msg_length} bytes")
            return None
        msg_data = b''

        while len(msg_data) < msg_length:
            chunk = conn.recv(min(msg_length - len(msg_data), MAX_DATA))
            if not chunk:
                return None
            msg_data += chunk
            
        return msg_data.decode('utf-8')
    except struct.error:
        print("\n[LỖI] Lỗi đọc header tin nhắn")
        return None
    except UnicodeDecodeError:
        print("\n[LỖI] Lỗi decode UTF-8")
        return None
    except Exception as e:
        logging.error(f"[RECV ERROR] {e}")
        return None

def receive_messages():
    """Thread nhận tin nhắn từ server"""
    global running, authenticated
    
    while running:
        try:
            message = recv_message(client_socket)
            
            if message is None:
                print("\n[HỆ THỐNG] Mất kết nối với server")
                running = False
                break
            
            # Xử lý các loại message khác nhau
            if message.startswith("XÁC THỰC:"):
                content = message.split(":", 1)[1]
                print(f"\n{content}")
                print("> ", end='', flush=True)
            
            elif message.startswith("DANGNHAP:") or message.startswith("DANGKY:"):
                msg_type, content = message.split(":", 1)
                print(f"\n{content}")
                print("> ", end='', flush=True)
            
            elif message.startswith("OK:"):
                content = message.split(":", 1)[1]
                print(f"\n✓ {content}")
                
                if "Chào mừng" in content:
                    authenticated = True
                    print_separator()
            
            elif message.startswith("LỖI:"):
                content = message.split(":", 1)[1]
                print(f"\n✗ LỖI: {content}")
            
            elif message.startswith("LỊCH SỬ:"):
                content = message.split(":", 1)[1]
                if "===" in content:
                    print(f"\n{content}")
                else:
                    print(content)
            
            elif message.startswith("[THÔNG BÁO]"):
                print(f"\n{message}")
                print("> ", end='', flush=True)
            
            elif message.startswith("[MÁY CHỦ]"):
                content = message.split("] ", 1)[1]
                print(f"\n {content}")
                print("> ", end='', flush=True)
            
            elif message.startswith("["):
                try:
                    sender = message[1:message.index("]")]
                    content = message[message.index("]") + 2:]
                    print(f"\n {sender}: {content}")
                    print("> ", end='', flush=True)
                except:
                    print(f"\n{message}")
                    print("> ", end='', flush=True)
            
            elif message == "Tạm biệt!":
                print(f"\n{message}")
                running = False
                break
            
            else:
                print(f"\n{message}")
                if authenticated:
                    print("> ", end='', flush=True)
        
        except ConnectionResetError:
            print("\n[HỆ THỐNG] Server đã đóng kết nối")
            running = False
            break
        except Exception as e:
            if running:
                print(f"\n[LỖI] Lỗi nhận tin: {e}")
            running = False
            break

def send_messages():
    """Thread gửi tin nhắn tới server"""
    global running
    
    while running:
        try:
            message = input()
            
            if not running:
                break
            
            if message.strip():
                if not send_message(client_socket, message):
                     running = False
                     break

                if message.strip() == '/exit':
                    running = False
                    break
        
        except KeyboardInterrupt:
            print("\n\n[HỆ THỐNG] Đang thoát...")
            running = False
            break
        except Exception as e:
            if running:
                print(f"[LỖI] Lỗi gửi tin: {e}")
            running = False
            break

def main():
    """Hàm chính khởi động client"""
    global client_socket, running
    
    # Hiển thị banner
    clear_screen()
    print_separator()
    print("         CHAT CLIENT - KẾT NỐI VỚI SERVER")
    print_separator()
    print(f"Server: {SERVER_IP}:{SERVER_PORT}")
    print_separator()
    
    try:
        # Kết nối tới server
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect((SERVER_IP, SERVER_PORT))
        print("\n✓ Đã kết nối thành công!")
        print("\nĐang chờ server...")
        
        # Tạo thread nhận tin nhắn
        receive_thread = threading.Thread(target=receive_messages, daemon=True)
        receive_thread.start()
        
        # Thread chính xử lý gửi tin
        send_messages()
        
    except ConnectionRefusedError:
        print("\n✗ Không thể kết nối! Server có thể chưa bật.")
        print(f"   Kiểm tra server đang chạy tại {SERVER_IP}:{SERVER_PORT}")
    except KeyboardInterrupt:
        print("\n\n[HỆ THỐNG] Thoát bằng Ctrl+C")
    except Exception as e:
        print(f"\n✗ Lỗi kết nối: {e}")
    finally:
        running = False
        if client_socket:
            try:
                client_socket.close()
            except:
                pass
        print("\n[HỆ THỐNG] Đã ngắt kết nối")
        print_separator()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTạm biệt!")
        sys.exit(0)