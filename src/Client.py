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