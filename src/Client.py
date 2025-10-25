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