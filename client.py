"""
Reliable FTP Client - UDP with Encryption, Resumable Transfers, and Pause/Resume support
"""

import socket
import os
import sys
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

SERVER_IP = "127.0.0.1"
MAIN_PORT = 9000

def download_file(filename):
    """Download a file from the server with resume capability"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    os.makedirs("downloads", exist_ok=True)
    filepath = f"downloads/{filename}"
    
    # 1. Check for partial files to resume
    resume_seq = 0
    file_mode = "wb"
    if os.path.exists(filepath):
        existing_size = os.path.getsize(filepath)
        resume_seq = existing_size // CHUNK_SIZE
        file_mode = "ab"
        print(f"\n[INFO] Partial file detected. Resuming from chunk {resume_seq}...")
    else:
        print(f"\n[CLIENT] Requesting new file: {filename}")
    
    # Send request with resume sequence
    request = encrypt_data(f"REQUEST {filename} {resume_seq}".encode())
    sock.sendto(request, (SERVER_IP, MAIN_PORT))
    
    try:
        data, server_addr = sock.recvfrom(65535)
        response = decrypt_data(data).decode()
        
        if response.startswith("ERROR"):
            print(f"[ERROR] {response}")
            sock.close()
            return False
            
        parts = response.split()
        filesize = int(parts[1])
        session_port = int(parts[2])
        session_addr = (SERVER_IP, session_port)
        
        sock.sendto(encrypt_data(b"READY"), session_addr)
        
        expected_seq = resume_seq
        received_bytes = resume_seq * CHUNK_SIZE
        
        # Open file in either Write ('wb') or Append ('ab') mode
        with open(filepath, file_mode) as f:
            print(f"\n[DOWNLOAD] Starting... (Press Ctrl+C to pause transfer)")
            
            try:
                while True:
                    try:
                        packet, addr = sock.recvfrom(65535)
                    except socket.timeout:
                        print("[TIMEOUT] Server not responding")
                        break
                    
                    try: decrypted = decrypt_data(packet)
                    except Exception: continue
                    
                    if decrypted == b"DONE":
                        print(f"\n[COMPLETE] Download successfully finished!")
                        sock.close()
                        return True
                    
                    if decrypted.startswith(b"ERROR"):
                        print(f"[ERROR] {decrypted.decode()}")
                        sock.close()
                        return False
                    
                    try:
                        parts = decrypted.split(b"|", 2)
                        seq, received_checksum, chunk = int(parts[0]), parts[1].decode(), parts[2]
                    except: continue
                    
                    # Sliding Window Receiver Logic
                    if seq == expected_seq:
                        if checksum(chunk) == received_checksum:
                            f.write(chunk)
                            received_bytes += len(chunk)
                            sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
                            
                            # Calculate and display progress cleanly
                            progress = (received_bytes / filesize) * 100 if filesize > 0 else 100
                            # Print on same line to avoid spamming the terminal for fast transfers
                            sys.stdout.write(f"\r[RECV] Chunk {seq} | Progress: {progress:.1f}% ✓")
                            sys.stdout.flush()
                            
                            expected_seq += 1
                        else:
                            sock.sendto(encrypt_data(f"NACK {seq}".encode()), session_addr)
                    elif seq < expected_seq:
                        # Duplicate packet from window overlap, re-ACK
                        sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
                        
            except KeyboardInterrupt:
                # Handle Ctrl+C for Pausing
                print(f"\n\n[PAUSED] Transfer manually paused.")
                print(f"[INFO] Saved {received_bytes} bytes. Run download again to resume from here.")
                
                # Notify server to stop sending the window
                stop_msg = encrypt_data(b"ERROR Client paused transfer")
                sock.sendto(stop_msg, session_addr)
                sock.close()
                return False

        sock.close()
        return False
        
    except socket.timeout:
        print("[ERROR] Server not responding")
        sock.close()
        return False


def upload_file(filepath):
    """Upload a file to the server (Standard Stop-and-Wait)"""
    # ... [Keep your existing upload_file function exactly as it was] ...
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return False
    
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    print(f"\n[CLIENT] Uploading file: {filename} ({filesize} bytes)")
    sock.sendto(encrypt_data(f"UPLOAD {filename} {filesize}".encode()), (SERVER_IP, MAIN_PORT))
    
    try:
        data, server_addr = sock.recvfrom(65535)
        response = decrypt_data(data).decode()
        
        if response.startswith("ERROR"): return False
        session_port = int(response.split()[1])
        session_addr = (SERVER_IP, session_port)
        
        sock.sendto(encrypt_data(b"READY"), session_addr)
        try:
            go_data, _ = sock.recvfrom(1024)
            if decrypt_data(go_data).decode() != "GO": return False
        except socket.timeout: return False
        
        print(f"\n[UPLOAD] Starting...")
        with open(filepath, "rb") as f:
            seq, sent_bytes, retries, max_retries = 0, 0, 0, 5
            
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk: break
                
                chunk_checksum = checksum(chunk)
                packet = encrypt_data(f"{seq}|{chunk_checksum}|".encode() + chunk)
                
                while retries < max_retries:
                    sock.sendto(packet, session_addr)
                    try:
                        ack_data, _ = sock.recvfrom(1024)
                        ack = decrypt_data(ack_data).decode()
                        if ack == f"ACK {seq}":
                            sent_bytes += len(chunk)
                            progress = (sent_bytes / filesize) * 100 if filesize > 0 else 100
                            sys.stdout.write(f"\r[SENT] Chunk {seq} | Progress: {progress:.1f}% ✓")
                            sys.stdout.flush()
                            retries = 0
                            break
                        elif ack.startswith("NACK"): retries += 1
                    except socket.timeout: retries += 1
                
                if retries >= max_retries: return False
                seq += 1
                
        sock.sendto(encrypt_data(b"DONE"), session_addr)
        try:
            confirm_data, _ = sock.recvfrom(1024)
            if decrypt_data(confirm_data).decode() == "OK":
                print(f"\n\n[COMPLETE] Uploaded successfully")
                return True
        except socket.timeout: pass
        
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()
    return False


def main():
    print("=" * 50)
    print("       Reliable FTP Client (UDP + Encrypted)")
    print("=" * 50)
    
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()
        if action == "download" and len(sys.argv) > 2: download_file(sys.argv[2])
        elif action == "upload" and len(sys.argv) > 2: upload_file(sys.argv[2])
    else:
        while True:
            print("\n" + "-" * 40)
            print("  1. Download file (Supports Pause/Resume)")
            print("  2. Upload file")
            print("  3. Exit")
            print("-" * 40)
            
            choice = input("Select option (1/2/3): ").strip()
            if choice == "1":
                filename = input("Enter filename to download: ").strip()
                if filename: download_file(filename)
            elif choice == "2":
                filepath = input("Enter path to file to upload: ").strip()
                if filepath: upload_file(filepath)
            elif choice == "3":
                print("\n[CLIENT] Goodbye!")
                break

if __name__ == "__main__":
    main()