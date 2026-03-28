"""
Reliable FTP Server - UDP with Encryption and Multi-Client Support
Security: Fernet symmetric encryption for all data
Multi-client: Each client session gets a dedicated socket
Integrity: MD5 checksum verification for each chunk
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


def handle_client(client_addr, filename):
    """Handle file transfer for a single client on dedicated socket"""
    client_ip, client_port = client_addr
    session_id = f"{client_ip}:{client_port}"
    
    # Create dedicated socket for this client session
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session_port = get_free_port()
    client_sock.bind((SERVER_IP, session_port))
    client_sock.settimeout(30)  # 30 second timeout for inactive clients
    
    print(f"[SESSION] {session_id} assigned to port {session_port}")
    
    try:
        filepath = "server_files/" + filename
        
        if not os.path.exists(filepath):
            error_msg = encrypt_data(b"ERROR File not found")
            client_sock.sendto(error_msg, client_addr)
            print(f"[ERROR] File '{filename}' not found for {session_id}")
            return
        
        filesize = os.path.getsize(filepath)
        
        # Send OK with filesize and session port
        response = f"OK {filesize} {session_port}"
        client_sock.sendto(encrypt_data(response.encode()), client_addr)
        
        # Wait for client to acknowledge and connect to session port
        try:
            ack_data, addr = client_sock.recvfrom(1024)
            ack = decrypt_data(ack_data).decode()
            if ack != "READY":
                print(f"[ERROR] Client not ready: {session_id}")
                return
        except socket.timeout:
            print(f"[TIMEOUT] Client did not respond: {session_id}")
            return
        
        print(f"[TRANSFER] Starting {filename} ({filesize} bytes) -> {session_id}")
        
        with open(filepath, "rb") as f:
            seq = 0
            retries = 0
            max_retries = 5
            
            while running:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                
                # Calculate checksum for integrity
                chunk_checksum = checksum(chunk)
                
                # Packet format: seq|checksum|data
                packet = f"{seq}|{chunk_checksum}|".encode() + chunk
                encrypted_packet = encrypt_data(packet)
                
                # Reliable delivery with ACK
                while running and retries < max_retries:
                    client_sock.sendto(encrypted_packet, client_addr)
                    
                    ready = select.select([client_sock], [], [], 2)
                    
                    if ready[0]:
                        try:
                            ack_data, _ = client_sock.recvfrom(1024)
                            ack = decrypt_data(ack_data).decode()
                            
                            if ack == f"ACK {seq}":
                                print(f"[SENT] Chunk {seq} ({len(chunk)} bytes) ✓")
                                retries = 0
                                break
                            elif ack.startswith("NACK"):
                                print(f"[NACK] Chunk {seq} checksum failed, retransmitting...")
                                retries += 1
                        except Exception as e:
                            print(f"[ERROR] ACK processing: {e}")
                            retries += 1
                    else:
                        print(f"[RETRY] Chunk {seq} (attempt {retries + 1})")
                        retries += 1
                
                if retries >= max_retries:
                    print(f"[FAILED] Max retries reached for chunk {seq}")
                    client_sock.sendto(encrypt_data(b"ERROR Transfer failed"), client_addr)
                    return
                
                seq += 1
        
        # Send completion signal
        client_sock.sendto(encrypt_data(b"DONE"), client_addr)
        print(f"[COMPLETE] {filename} -> {session_id} ({seq} chunks)")
        
    except socket.timeout:
        print(f"[TIMEOUT] Session expired: {session_id}")
    except Exception as e:
        print(f"[ERROR] {session_id}: {e}")
    finally:
        client_sock.close()
        with clients_lock:
            if session_id in active_clients:
                del active_clients[session_id]
        print(f"[CLOSED] Session {session_id}")


def handle_upload(client_addr, filename, filesize):
    """Handle file upload from a client"""
    client_ip, client_port = client_addr
    session_id = f"{client_ip}:{client_port}"
    
    # Create dedicated socket for this client session
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session_port = get_free_port()
    client_sock.bind((SERVER_IP, session_port))
    client_sock.settimeout(30)
    
    print(f"[SESSION] {session_id} assigned to port {session_port} (upload)")
    
    try:
        filepath = "server_files/" + filename
        
        # Send OK with session port
        response = f"OK {session_port}"
        client_sock.sendto(encrypt_data(response.encode()), client_addr)
        
        # Wait for client to start sending
        try:
            ready_data, addr = client_sock.recvfrom(1024)
            ready = decrypt_data(ready_data).decode()
            if ready != "READY":
                print(f"[ERROR] Client not ready: {session_id}")
                return
        except socket.timeout:
            print(f"[TIMEOUT] Client did not respond: {session_id}")
            return
        
        # Send GO signal
        client_sock.sendto(encrypt_data(b"GO"), client_addr)
        
        print(f"[UPLOAD] Receiving {filename} ({filesize} bytes) <- {session_id}")
        
        expected_seq = 0
        received_bytes = 0
        
        with open(filepath, "wb") as f:
            while running:
                try:
                    packet, addr = client_sock.recvfrom(65535)
                except socket.timeout:
                    print(f"[TIMEOUT] Upload stalled: {session_id}")
                    return
                
                try:
                    decrypted = decrypt_data(packet)
                except:
                    continue
                
                # Check for completion
                if decrypted == b"DONE":
                    # Send confirmation
                    client_sock.sendto(encrypt_data(b"OK"), client_addr)
                    print(f"[COMPLETE] Received {filename} ({received_bytes} bytes) <- {session_id}")
                    return
                
                # Parse packet: seq|checksum|data
                try:
                    parts = decrypted.split(b"|", 2)
                    seq = int(parts[0])
                    received_checksum = parts[1].decode()
                    chunk = parts[2]
                except:
                    continue
                
                if seq == expected_seq:
                    # Verify checksum
                    calculated_checksum = checksum(chunk)
                    
                    if calculated_checksum == received_checksum:
                        f.write(chunk)
                        received_bytes += len(chunk)
                        
                        # Send ACK
                        ack = encrypt_data(f"ACK {seq}".encode())
                        client_sock.sendto(ack, client_addr)
                        
                        print(f"[RECV] Chunk {seq} ({len(chunk)} bytes) ✓")
                        expected_seq += 1
                    else:
                        # Checksum mismatch
                        nack = encrypt_data(f"NACK {seq}".encode())
                        client_sock.sendto(nack, client_addr)
                        print(f"[NACK] Chunk {seq}: checksum mismatch")
                
                elif seq < expected_seq:
                    # Duplicate - re-send ACK
                    ack = encrypt_data(f"ACK {seq}".encode())
                    client_sock.sendto(ack, client_addr)
                    
    except Exception as e:
        print(f"[ERROR] Upload {session_id}: {e}")
    finally:
        client_sock.close()
        with clients_lock:
            if session_id in active_clients:
                del active_clients[session_id]
        print(f"[CLOSED] Session {session_id}")


def main():
    global running
    
    # Main socket for initial client connections
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((SERVER_IP, MAIN_PORT))
    main_sock.settimeout(1)  # Allow checking for Ctrl+C
    
    print(f"\n[SERVER] Ready to accept connections...\n")
    
    try:
        while running:
            try:
                data, addr = main_sock.recvfrom(65535)
                
                try:
                    message = decrypt_data(data).decode()
                except:
                    print(f"[WARN] Decryption failed from {addr}")
                    continue
                
                session_id = f"{addr[0]}:{addr[1]}"
                print(f"\n[REQUEST] {session_id} -> {message}")
                
                if message.startswith("REQUEST"):
                    parts = message.split()
                    if len(parts) < 2:
                        main_sock.sendto(encrypt_data(b"ERROR Invalid request"), addr)
                        continue
                    
                    filename = parts[1]
                    
                    # Check if client already has active session
                    with clients_lock:
                        if session_id in active_clients:
                            main_sock.sendto(encrypt_data(b"ERROR Session already active"), addr)
                            continue
                        active_clients[session_id] = True
                    
                    # Start client handler thread (download)
                    t = threading.Thread(target=handle_client, args=(addr, filename))
                    t.daemon = True
                    t.start()
                
                elif message.startswith("UPLOAD"):
                    parts = message.split()
                    if len(parts) < 3:
                        main_sock.sendto(encrypt_data(b"ERROR Invalid upload request"), addr)
                        continue
                    
                    filename = parts[1]
                    filesize = int(parts[2])
                    
                    # Check if client already has active session
                    with clients_lock:
                        if session_id in active_clients:
                            main_sock.sendto(encrypt_data(b"ERROR Session already active"), addr)
                            continue
                        active_clients[session_id] = True
                    
                    # Start client handler thread (upload)
                    t = threading.Thread(target=handle_upload, args=(addr, filename, filesize))
                    t.daemon = True
                    t.start()
                
                else:
                    main_sock.sendto(encrypt_data(b"ERROR Unknown command"), addr)
                    
            except socket.timeout:
                continue
                
    except KeyboardInterrupt:
        print("\n[SERVER] Shutdown requested...")
        running = False
    finally:
        main_sock.close()
        print("[SERVER] Shutdown complete")


if __name__ == "__main__":
    main()