import socket
import threading
Server_IP = "127.0.0.1"
Server_Port = 20000
Max_data = 1024

ClientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ClientSocket.connect((Server_IP, Server_Port))
print("Ket noi Server Thanh Cong!")

def Listening():
    while True:
        try:
            data = ClientSocket.recv(Max_data)
            if data:
                print(data.decode('utf-8'))
            else:
                break
        except:
            break

Luong_2 = threading.Thread(target=Listening)
Luong_2.start()

while True:
    msg = input()
    if msg.lower() == "exit":
        break
    ClientSocket.send(msg.encode('utf-8'))

ClientSocket.close()
print("Disconnected.")
