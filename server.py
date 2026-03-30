"""
Reliable FTP Server - UDP with Encryption, Multi-Client, Resumable Transfers, and Sliding Window Optimization
"""

import socket
import os
import threading
import select
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

SERVER_IP = "0.0.0.0"
MAIN_PORT = 9000

# Track active client sessions
active_clients = {}
clients_lock = threading.Lock()
running = True

print("=" * 50)
print("       Reliable FTP Server (UDP + Encrypted)")
print(f"       Listening on port: {MAIN_PORT}")
print("       Press Ctrl+C to stop server")
print("=" * 50)


def get_free_port():
    """Find an available port for client session"""
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(('', 0))
    port = temp_sock.getsockname()[1]
    temp_sock.close()
    return port


def handle_client(client_addr, filename, resume_seq=0):
    """Handle file transfer for a single client (Download with Sliding Window)"""
    client_ip, client_port = client_addr
    session_id = f"{client_ip}:{client_port}"
    
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session_port = get_free_port()
    client_sock.bind((SERVER_IP, session_port))
    client_sock.settimeout(30)
    
    print(f"[SESSION] {session_id} assigned to port {session_port}")
    
    try:
        filepath = "server_storage/" + filename
        
        if not os.path.exists(filepath):
            error_msg = encrypt_data(b"ERROR File not found")
            client_sock.sendto(error_msg, client_addr)
            return
        
        filesize = os.path.getsize(filepath)
        response = f"OK {filesize} {session_port}"
        client_sock.sendto(encrypt_data(response.encode()), client_addr)
        
        try:
            ack_data, addr = client_sock.recvfrom(1024)
            ack = decrypt_data(ack_data).decode()
            if ack != "READY": return
        except socket.timeout:
            return
        
        print(f"[TRANSFER] Starting {filename} ({filesize} bytes) -> {session_id}")
        
        with open(filepath, "rb") as f:
            seq = resume_seq
            
            # 1. RESUME LOGIC: Jump to the correct chunk
            if resume_seq > 0:
                f.seek(resume_seq * CHUNK_SIZE)
                print(f"[RESUME] Skipping to chunk {resume_seq} for {session_id}")
            
            # 2. THROUGHPUT OPTIMIZATION: Sliding Window (Go-Back-N)
            WINDOW_SIZE = 5
            unacked_packets = {}  # {seq: encrypted_packet}
            timeout_count = 0
            max_retries = 5
            
            while running:
                # Fill the window
                while len(unacked_packets) < WINDOW_SIZE:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break  # EOF
                    
                    chunk_checksum = checksum(chunk)
                    packet = f"{seq}|{chunk_checksum}|".encode() + chunk
                    encrypted_packet = encrypt_data(packet)
                    
                    unacked_packets[seq] = encrypted_packet
                    client_sock.sendto(encrypted_packet, client_addr)
                    seq += 1
                
                if not unacked_packets:
                    break  # All chunks sent and acknowledged
                
                # Wait for ACKs
                ready = select.select([client_sock], [], [], 2)
                
                if ready[0]:
                    try:
                        ack_data, _ = client_sock.recvfrom(1024)
                        ack = decrypt_data(ack_data).decode()
                        
                        if ack.startswith("ERROR"):
                            # Client paused (Ctrl+C)
                            print(f"[PAUSED] Client {session_id} halted transfer.")
                            return

                        if ack.startswith("ACK"):
                            ack_seq = int(ack.split()[1])
                            if ack_seq in unacked_packets:
                                del unacked_packets[ack_seq]
                                timeout_count = 0  # Reset retries on successful progress
                                
                        elif ack.startswith("NACK"):
                            nack_seq = int(ack.split()[1])
                            if nack_seq in unacked_packets:
                                print(f"[NACK] Chunk {nack_seq} corrupted, retransmitting...")
                                client_sock.sendto(unacked_packets[nack_seq], client_addr)
                                
                    except Exception as e:
                        pass # Ignore corrupted acks
                else:
                    # Timeout: Retransmit the entire window
                    timeout_count += 1
                    print(f"[TIMEOUT] Retransmitting window (attempt {timeout_count})...")
                    for p_seq, p_data in unacked_packets.items():
                        client_sock.sendto(p_data, client_addr)
                    
                    if timeout_count >= max_retries:
                        print(f"[FAILED] Max retries reached for {session_id}")
                        client_sock.sendto(encrypt_data(b"ERROR Transfer failed"), client_addr)
                        return
        
        # Send completion signal
        client_sock.sendto(encrypt_data(b"DONE"), client_addr)
        print(f"[COMPLETE] {filename} -> {session_id}")
        
    except socket.timeout:
        print(f"[TIMEOUT] Session expired: {session_id}")
    finally:
        client_sock.close()
        with clients_lock:
            if session_id in active_clients:
                del active_clients[session_id]


def handle_upload(client_addr, filename, filesize, resume_seq=0):
    """Handle file upload from a client (Standard Stop-and-Wait)"""
    client_ip, client_port = client_addr
    session_id = f"{client_ip}:{client_port}"

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session_port = get_free_port()
    client_sock.bind((SERVER_IP, session_port))
    client_sock.settimeout(30)

    try:
        filepath = "server_storage/" + filename
        response = f"OK {session_port}"
        client_sock.sendto(encrypt_data(response.encode()), client_addr)

        try:
            ready_data, addr = client_sock.recvfrom(1024)
            if decrypt_data(ready_data).decode() != "READY": return
        except socket.timeout: return

        client_sock.sendto(encrypt_data(b"GO"), client_addr)

        expected_seq = resume_seq
        received_bytes = resume_seq * CHUNK_SIZE

        # Open in append mode if resuming, otherwise overwrite
        file_mode = "ab" if resume_seq > 0 else "wb"
        if resume_seq > 0:
            print(f"[RESUME] Upload resuming from chunk {resume_seq} for {session_id}")

        with open(filepath, file_mode) as f:
            while running:
                try:
                    packet, addr = client_sock.recvfrom(65535)
                except socket.timeout: return
                
                try: decrypted = decrypt_data(packet)
                except: continue
                
                if decrypted == b"DONE":
                    client_sock.sendto(encrypt_data(b"OK"), client_addr)
                    print(f"[COMPLETE] Received {filename} <- {session_id}")
                    return
                
                try:
                    parts = decrypted.split(b"|", 2)
                    seq, received_checksum, chunk = int(parts[0]), parts[1].decode(), parts[2]
                except: continue
                
                if seq == expected_seq:
                    if checksum(chunk) == received_checksum:
                        f.write(chunk)
                        received_bytes += len(chunk)
                        client_sock.sendto(encrypt_data(f"ACK {seq}".encode()), client_addr)
                        expected_seq += 1
                    else:
                        client_sock.sendto(encrypt_data(f"NACK {seq}".encode()), client_addr)
                elif seq < expected_seq:
                    client_sock.sendto(encrypt_data(f"ACK {seq}".encode()), client_addr)
                    
    finally:
        client_sock.close()
        with clients_lock:
            if session_id in active_clients: del active_clients[session_id]


def main():
    global running
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((SERVER_IP, MAIN_PORT))
    main_sock.settimeout(1)
    
    print(f"\n[SERVER] Ready to accept connections...\n")
    
    try:
        while running:
            try:
                data, addr = main_sock.recvfrom(65535)
                try: message = decrypt_data(data).decode()
                except: continue
                
                session_id = f"{addr[0]}:{addr[1]}"
                
                if message.startswith("REQUEST"):
                    parts = message.split()
                    filename = parts[1]
                    # Parse resume sequence if provided, otherwise 0
                    resume_seq = int(parts[2]) if len(parts) > 2 else 0
                    
                    with clients_lock:
                        if session_id in active_clients: continue
                        active_clients[session_id] = True
                    
                    t = threading.Thread(target=handle_client, args=(addr, filename, resume_seq))
                    t.daemon = True
                    t.start()
                
                elif message.startswith("UPLOAD"):
                    parts = message.split()
                    filename, filesize = parts[1], int(parts[2])
                    resume_seq = int(parts[3]) if len(parts) > 3 else 0

                    with clients_lock:
                        if session_id in active_clients: continue
                        active_clients[session_id] = True

                    t = threading.Thread(target=handle_upload, args=(addr, filename, filesize, resume_seq))
                    t.daemon = True
                    t.start()
                    
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\n[SERVER] Shutdown requested...")
        running = False
    finally:
        main_sock.close()

if __name__ == "__main__":
    main()