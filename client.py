"""
Reliable FTP Client - UDP with Encryption and Integrity Checking
Security: Fernet symmetric encryption for all data
Integrity: MD5 checksum verification for each chunk
"""

import socket
import os
import sys
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

SERVER_IP = "127.0.0.1"
MAIN_PORT = 9000


def download_file(filename):
    """Download a file from the server with integrity checking"""
    
    # Create socket for initial request
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    print(f"\n[CLIENT] Requesting file: {filename}")
    
    # Send request to main server port
    request = encrypt_data(f"REQUEST {filename}".encode())
    sock.sendto(request, (SERVER_IP, MAIN_PORT))
    
    try:
        # Wait for response with session port
        data, server_addr = sock.recvfrom(65535)
        response = decrypt_data(data).decode()
        
        if response.startswith("ERROR"):
            print(f"[ERROR] {response}")
            sock.close()
            return False
        
        # Parse response: "OK filesize session_port"
        parts = response.split()
        if len(parts) < 3:
            print("[ERROR] Invalid server response")
            sock.close()
            return False
        
        filesize = int(parts[1])
        session_port = int(parts[2])
        
        print(f"[INFO] File size: {filesize} bytes")
        print(f"[INFO] Session port: {session_port}")
        
        # Send READY to session port
        session_addr = (SERVER_IP, session_port)
        sock.sendto(encrypt_data(b"READY"), session_addr)
        
        # Prepare to receive file
        os.makedirs("downloads", exist_ok=True)
        filepath = f"downloads/{filename}"
        
        expected_seq = 0
        received_bytes = 0
        corrupted_chunks = 0
        
        with open(filepath, "wb") as f:
            print(f"\n[DOWNLOAD] Starting...")
            
            while True:
                try:
                    packet, addr = sock.recvfrom(65535)
                except socket.timeout:
                    print("[TIMEOUT] Server not responding")
                    break
                
                try:
                    decrypted = decrypt_data(packet)
                except Exception as e:
                    print(f"[ERROR] Decryption failed: {e}")
                    continue
                
                # Check for completion
                if decrypted == b"DONE":
                    print(f"\n[COMPLETE] Downloaded {received_bytes} bytes")
                    print(f"[STATS] Corrupted chunks detected: {corrupted_chunks}")
                    sock.close()
                    return True
                
                # Check for error
                if decrypted.startswith(b"ERROR"):
                    print(f"[ERROR] {decrypted.decode()}")
                    sock.close()
                    return False
                
                # Parse packet: seq|checksum|data
                try:
                    parts = decrypted.split(b"|", 2)
                    seq = int(parts[0])
                    received_checksum = parts[1].decode()
                    chunk = parts[2]
                except:
                    print("[ERROR] Malformed packet")
                    continue
                
                if seq == expected_seq:
                    # Verify checksum
                    calculated_checksum = checksum(chunk)
                    
                    if calculated_checksum == received_checksum:
                        # Checksum valid - write chunk
                        f.write(chunk)
                        received_bytes += len(chunk)
                        
                        # Send ACK
                        ack = encrypt_data(f"ACK {seq}".encode())
                        sock.sendto(ack, session_addr)
                        
                        progress = (received_bytes / filesize) * 100 if filesize > 0 else 100
                        print(f"[RECV] Chunk {seq}: {len(chunk)} bytes ({progress:.1f}%) ✓")
                        
                        expected_seq += 1
                    else:
                        # Checksum mismatch - request retransmit
                        corrupted_chunks += 1
                        nack = encrypt_data(f"NACK {seq}".encode())
                        sock.sendto(nack, session_addr)
                        print(f"[NACK] Chunk {seq}: checksum mismatch!")
                
                elif seq < expected_seq:
                    # Duplicate packet - re-send ACK
                    ack = encrypt_data(f"ACK {seq}".encode())
                    sock.sendto(ack, session_addr)
        
        sock.close()
        return False
        
    except socket.timeout:
        print("[ERROR] Server not responding")
        sock.close()
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        sock.close()
        return False


def upload_file(filepath):
    """Upload a file to the server with integrity checking"""
    
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return False
    
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    
    # Create socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    print(f"\n[CLIENT] Uploading file: {filename} ({filesize} bytes)")
    
    # Send upload request
    request = encrypt_data(f"UPLOAD {filename} {filesize}".encode())
    sock.sendto(request, (SERVER_IP, MAIN_PORT))
    
    try:
        # Wait for response with session port
        data, server_addr = sock.recvfrom(65535)
        response = decrypt_data(data).decode()
        
        if response.startswith("ERROR"):
            print(f"[ERROR] {response}")
            sock.close()
            return False
        
        # Parse response: "OK session_port"
        parts = response.split()
        if len(parts) < 2:
            print("[ERROR] Invalid server response")
            sock.close()
            return False
        
        session_port = int(parts[1])
        print(f"[INFO] Session port: {session_port}")
        
        session_addr = (SERVER_IP, session_port)
        
        # Send READY
        sock.sendto(encrypt_data(b"READY"), session_addr)
        
        # Wait for GO signal
        try:
            go_data, _ = sock.recvfrom(1024)
            go = decrypt_data(go_data).decode()
            if go != "GO":
                print("[ERROR] Server not ready")
                sock.close()
                return False
        except socket.timeout:
            print("[TIMEOUT] Server not responding")
            sock.close()
            return False
        
        print(f"\n[UPLOAD] Starting...")
        
        with open(filepath, "rb") as f:
            seq = 0
            sent_bytes = 0
            retries = 0
            max_retries = 5
            
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                
                # Calculate checksum
                chunk_checksum = checksum(chunk)
                
                # Packet format: seq|checksum|data
                packet = f"{seq}|{chunk_checksum}|".encode() + chunk
                encrypted_packet = encrypt_data(packet)
                
                # Reliable delivery with ACK
                while retries < max_retries:
                    sock.sendto(encrypted_packet, session_addr)
                    
                    try:
                        ack_data, _ = sock.recvfrom(1024)
                        ack = decrypt_data(ack_data).decode()
                        
                        if ack == f"ACK {seq}":
                            sent_bytes += len(chunk)
                            progress = (sent_bytes / filesize) * 100 if filesize > 0 else 100
                            print(f"[SENT] Chunk {seq}: {len(chunk)} bytes ({progress:.1f}%) ✓")
                            retries = 0
                            break
                        elif ack.startswith("NACK"):
                            print(f"[NACK] Chunk {seq}: retransmitting...")
                            retries += 1
                    except socket.timeout:
                        print(f"[RETRY] Chunk {seq} (attempt {retries + 1})")
                        retries += 1
                
                if retries >= max_retries:
                    print(f"[FAILED] Max retries reached")
                    sock.close()
                    return False
                
                seq += 1
        
        # Send completion signal
        sock.sendto(encrypt_data(b"DONE"), session_addr)
        
        # Wait for confirmation
        try:
            confirm_data, _ = sock.recvfrom(1024)
            confirm = decrypt_data(confirm_data).decode()
            if confirm == "OK":
                print(f"\n[COMPLETE] Uploaded {sent_bytes} bytes")
                sock.close()
                return True
        except socket.timeout:
            pass
        
        sock.close()
        return False
        
    except socket.timeout:
        print("[ERROR] Server not responding")
        sock.close()
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        sock.close()
        return False


def main():
    print("=" * 50)
    print("       Reliable FTP Client (UDP + Encrypted)")
    print("=" * 50)
    
    if len(sys.argv) > 1:
        # Command-line mode (single operation)
        action = sys.argv[1].lower()
        
        if action == "download" and len(sys.argv) > 2:
            filename = sys.argv[2]
            success = download_file(filename)
            if success:
                print(f"\n[SUCCESS] File saved to downloads/{filename}")
            else:
                print("\n[FAILED] Download failed")
        
        elif action == "upload" and len(sys.argv) > 2:
            filepath = sys.argv[2]
            success = upload_file(filepath)
            if success:
                print(f"\n[SUCCESS] File uploaded to server")
            else:
                print("\n[FAILED] Upload failed")
        
        else:
            print("\nUsage:")
            print("  python3 client.py download <filename>")
            print("  python3 client.py upload <filepath>")
    else:
        # Interactive mode (loop until exit)
        while True:
            print("\n" + "-" * 40)
            print("Options:")
            print("  1. Download file")
            print("  2. Upload file")
            print("  3. Exit")
            print("-" * 40)
            
            choice = input("Select option (1/2/3): ").strip()
            
            if choice == "1":
                filename = input("Enter filename to download: ").strip()
                if filename:
                    success = download_file(filename)
                    if success:
                        print(f"\n[SUCCESS] File saved to downloads/{filename}")
                    else:
                        print("\n[FAILED] Download failed")
                else:
                    print("[ERROR] No filename provided")
            
            elif choice == "2":
                filepath = input("Enter path to file to upload: ").strip()
                if filepath:
                    success = upload_file(filepath)
                    if success:
                        print(f"\n[SUCCESS] File uploaded to server")
                    else:
                        print("\n[FAILED] Upload failed")
                else:
                    print("[ERROR] No filepath provided")
            
            elif choice == "3":
                print("\n[CLIENT] Goodbye!")
                break
            
            else:
                print("[ERROR] Invalid option. Please enter 1, 2, or 3.")


if __name__ == "__main__":
    main()