import socket
import threading

def receive(client):
    while True:
        try:
            msg = client.recv(1024).decode('utf-8')
            print(f"\n{msg}")
            print("> ", end='', flush=True)
        except:
            break

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(("127.0.0.1", 20000))

threading.Thread(target=receive, args=(client,), daemon=True).start()

while True:
    msg = input("> ")
    if msg.lower() == 'exit':
        break
    client.send(msg.encode('utf-8'))

client.close()