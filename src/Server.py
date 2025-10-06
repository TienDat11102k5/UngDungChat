import socket
import threading
import time

Client_list = [] 
Max_data = 1024
Local_IP = "127.0.0.1"
Local_Port = 20000

ServerSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ServerSocket.bind((Local_IP, Local_Port))
ServerSocket.listen(5)
print("Server: - ON -")

def handle_client(conn, addr):
    print(f"Client connected: {addr}")
    Client_list.append((conn, addr))
    try:
        while True:
            data = conn.recv(Max_data)
            if not data:
                break
            msg = data.decode('utf-8')
            print(f"{addr}: {msg}")
            # Gửi tin lại cho tất cả client khác
            for c, a in Client_list:
                if c != conn:
                    try:
                        c.send(f"{addr}: {msg}".encode('utf-8'))
                    except:
                        pass
    except:
        pass
    print(f"Client disconnected: {addr}")
    Client_list.remove((conn, addr))
    conn.close()

def Listening():
    while True:
        conn, addr = ServerSocket.accept()
        thread = threading.Thread(target=handle_client, args=(conn, addr))
        thread.start()

Luong_2 = threading.Thread(target=Listening)
Luong_2.start()

while True:
    time.sleep(1)
