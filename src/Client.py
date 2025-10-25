import socket
import threading
import sys
import os

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

def receive_messages():
    """Thread nhận tin nhắn từ server"""
    global running, authenticated
    
    while running:
        try:
            data = client_socket.recv(MAX_DATA)
            if not data:
                print("\n[HỆ THỐNG] Mất kết nối với server")
                running = False
                break
            
            message = data.decode('utf-8')
            
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
                client_socket.send(message.encode('utf-8'))
                
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