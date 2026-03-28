# Reliable FTP - Technical Implementation Documentation

This document provides an in-depth technical explanation of every component, function, and code block in the Reliable FTP project.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Socket Programming Fundamentals](#2-socket-programming-fundamentals)
3. [encryption.py - Detailed Breakdown](#3-encryptionpy---detailed-breakdown)
4. [utils.py - Detailed Breakdown](#4-utilspy---detailed-breakdown)
5. [server.py - Detailed Breakdown](#5-serverpy---detailed-breakdown)
6. [client.py - Detailed Breakdown](#6-clientpy---detailed-breakdown)
7. [Protocol State Machine](#7-protocol-state-machine)
8. [Concurrency Model](#8-concurrency-model)
9. [Error Handling Deep Dive](#9-error-handling-deep-dive)
10. [Network Byte Flow](#10-network-byte-flow)

---

## 1. System Architecture

### 1.1 High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            APPLICATION LAYER                             │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Reliable FTP Protocol                         │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │    │
│  │  │  Commands   │  │  Chunking   │  │  Sequence & ACK Logic   │  │    │
│  │  │  REQUEST    │  │  4KB blocks │  │  Stop-and-Wait ARQ      │  │    │
│  │  │  UPLOAD     │  │             │  │                         │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────────────────┤
│                            SECURITY LAYER                                │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Fernet Encryption                             │    │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │    │
│  │  │  AES-128-CBC    │  │  HMAC-SHA256    │  │  MD5 Checksum   │  │    │
│  │  │  Confidentiality│  │  Authentication │  │  Integrity      │  │    │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────────────────┤
│                            TRANSPORT LAYER                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                         UDP Sockets                              │    │
│  │  ┌─────────────────────────────────────────────────────────┐    │    │
│  │  │  socket.SOCK_DGRAM | Connectionless | Unreliable       │    │    │
│  │  └─────────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Why Each Layer Matters

| Layer | Purpose | Without It |
|-------|---------|------------|
| Application | File transfer logic | No way to request/send files |
| Security | Encryption + Integrity | Data readable by attackers, tampering possible |
| Transport | UDP communication | No network communication |

---

## 2. Socket Programming Fundamentals

### 2.1 UDP Socket Creation

```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
```

**Parameter Breakdown:**
- `socket.AF_INET` - Address Family: IPv4
  - Other options: `AF_INET6` (IPv6), `AF_UNIX` (local)
- `socket.SOCK_DGRAM` - Socket Type: Datagram (UDP)
  - Alternative: `SOCK_STREAM` for TCP

### 2.2 Socket Operations Used

| Operation | Code | Description |
|-----------|------|-------------|
| **Bind** | `sock.bind((IP, PORT))` | Associate socket with IP:PORT |
| **Send** | `sock.sendto(data, addr)` | Send data to specific address |
| **Receive** | `sock.recvfrom(buffer_size)` | Receive data and sender address |
| **Timeout** | `sock.settimeout(seconds)` | Set blocking timeout |
| **Close** | `sock.close()` | Release socket resources |

### 2.3 UDP vs TCP Comparison

```
UDP (What we use):
┌─────────┐         ┌─────────┐
│ Client  │──DATA──>│ Server  │    No connection setup
│         │<──DATA──│         │    No guaranteed delivery
└─────────┘         └─────────┘    No ordering guarantee

TCP (Alternative):
┌─────────┐         ┌─────────┐
│ Client  │──SYN───>│ Server  │    3-way handshake
│         │<SYN-ACK─│         │    Guaranteed delivery
│         │──ACK───>│         │    Ordered delivery
│         │──DATA──>│         │    More overhead
└─────────┘         └─────────┘
```

We chose UDP and built reliability ourselves to demonstrate protocol design.

---

## 3. encryption.py - Detailed Breakdown

### 3.1 Complete Code with Annotations

```python
"""
Encryption module for Reliable FTP
Security: Fernet symmetric encryption (AES-128-CBC with HMAC)
"""

from cryptography.fernet import Fernet
```

**Import Explanation:**
- `cryptography` - Industry-standard crypto library
- `Fernet` - High-level symmetric encryption class

### 3.2 The Encryption Key

```python
# Shared encryption key (in production, use key exchange protocol)
KEY = b'Zr5rj1L6wF1c4z9sH0K2mYk7TqP8xA3vB6D9uE2nC4g='
```

**Key Details:**
- **Type**: `bytes` (indicated by `b'...'`)
- **Length**: 32 bytes (256 bits), base64-encoded
- **Format**: URL-safe base64
- **Generation**: Created via `Fernet.generate_key()`

**Security Note:** In production:
- Use Diffie-Hellman key exchange
- Or use TLS/DTLS for key negotiation
- Never hardcode keys in source code

### 3.3 Cipher Object

```python
cipher = Fernet(KEY)
```

**What this creates:**
- A Fernet cipher object initialized with the key
- Can be reused for multiple encrypt/decrypt operations
- Thread-safe for concurrent use

### 3.4 Encryption Function

```python
def encrypt_data(data):
    """Encrypt data using Fernet (AES-128-CBC + HMAC)"""
    return cipher.encrypt(data)
```

**Input:** `bytes` (raw binary data)
**Output:** `bytes` (encrypted, base64-encoded)

**What Fernet.encrypt() does internally:**

```
Input: b"Hello World"
           │
           ▼
┌──────────────────────────────────┐
│ 1. Generate random IV (16 bytes) │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 2. Pad data to 16-byte boundary │
│    (PKCS7 padding)               │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 3. Encrypt with AES-128-CBC      │
│    Key: first 16 bytes of KEY    │
│    IV: random from step 1        │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 4. Add timestamp (8 bytes)       │
│    Current time for expiry check │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 5. Compute HMAC-SHA256           │
│    Over: version + timestamp +   │
│          IV + ciphertext         │
│    Key: last 16 bytes of KEY     │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 6. Concatenate all parts         │
│    version(1) + timestamp(8) +   │
│    IV(16) + ciphertext + HMAC(32)│
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 7. Base64 encode                 │
└──────────────────────────────────┘
           │
           ▼
Output: b"gAAAAABn..." (base64 string)
```

### 3.5 Decryption Function

```python
def decrypt_data(data):
    """Decrypt data using Fernet"""
    return cipher.decrypt(data)
```

**What Fernet.decrypt() does internally:**

```
Input: b"gAAAAABn..." (encrypted)
           │
           ▼
┌──────────────────────────────────┐
│ 1. Base64 decode                 │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 2. Extract components:           │
│    - Version (1 byte)            │
│    - Timestamp (8 bytes)         │
│    - IV (16 bytes)               │
│    - Ciphertext (variable)       │
│    - HMAC (32 bytes)             │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 3. Verify HMAC-SHA256            │
│    If mismatch → InvalidToken    │
│    (Detects tampering)           │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 4. Check timestamp (optional)    │
│    Can reject expired tokens     │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 5. Decrypt with AES-128-CBC      │
│    Using extracted IV            │
└──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│ 6. Remove PKCS7 padding          │
└──────────────────────────────────┘
           │
           ▼
Output: b"Hello World" (original)
```

---

## 4. utils.py - Detailed Breakdown

### 4.1 Complete Code with Annotations

```python
"""
Utility functions for Reliable FTP
"""

import hashlib
```

**Import Explanation:**
- `hashlib` - Python's built-in cryptographic hash library
- Provides MD5, SHA-1, SHA-256, etc.

### 4.2 Chunk Size Constant

```python
# Chunk size for file transfer (4KB)
CHUNK_SIZE = 4096
```

**Why 4096 bytes?**

| Consideration | Explanation |
|---------------|-------------|
| **UDP MTU** | Max UDP payload ~65,507 bytes, but network MTU is typically 1500 bytes. 4KB leaves room for headers after fragmentation. |
| **Memory** | Small enough to buffer many chunks in RAM |
| **Efficiency** | Large enough to reduce per-chunk overhead |
| **Common size** | Matches filesystem block size on most systems |

**Packet Size Calculation:**
```
Raw chunk:                    4096 bytes
+ Sequence number (~3 bytes): 4099 bytes
+ Checksum (32 bytes):        4131 bytes
+ Delimiters (2 bytes):       4133 bytes
After Fernet encryption:      ~5600 bytes (base64 + overhead)
```

### 4.3 Checksum Function

```python
def checksum(data):
    """Calculate MD5 checksum for data integrity verification"""
    return hashlib.md5(data).hexdigest()
```

**How MD5 Works:**

```
Input: b"Hello World" (any length)
           │
           ▼
┌──────────────────────────────────┐
│ MD5 Algorithm (128-bit hash)     │
│ - Processes data in 512-bit     │
│   blocks                         │
│ - 4 rounds of 16 operations     │
│ - Produces fixed 128-bit output │
└──────────────────────────────────┘
           │
           ▼
Output: "b10a8db164e0754105b7a99be72e3fe5"
        (32 hex characters = 128 bits)
```

**Why MD5 for integrity (not security)?**
- Fast to compute
- 128-bit output is sufficient for error detection
- We're not using it for security (Fernet handles that)
- Probability of accidental collision: 1 in 2^128

### 4.4 Verify Checksum Function

```python
def verify_checksum(data, expected_checksum):
    """Verify data integrity by comparing checksums"""
    return checksum(data) == expected_checksum
```

**String comparison in Python:**
- Compares character by character
- Returns `True` if all characters match
- Returns `False` on first mismatch

---

## 5. server.py - Detailed Breakdown

### 5.1 Imports and Constants

```python
import socket          # Low-level networking
import os              # File system operations
import threading       # Multi-client support
import select          # Non-blocking I/O multiplexing
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

SERVER_IP = "0.0.0.0"  # Listen on all interfaces
MAIN_PORT = 9000       # Main listening port
```

**Why "0.0.0.0"?**
```
0.0.0.0 = Listen on ALL network interfaces:
  - localhost (127.0.0.1)
  - LAN IP (192.168.x.x)
  - Public IP (if any)

vs 127.0.0.1 = Only localhost connections
```

### 5.2 Global State Variables

```python
active_clients = {}      # Dictionary: session_id -> True
clients_lock = threading.Lock()  # Mutex for thread safety
running = True           # Server running flag
```

**Why we need a lock:**
```
Without lock (RACE CONDITION):
Thread 1: reads active_clients["127.0.0.1:5000"]  # Empty
Thread 2: reads active_clients["127.0.0.1:5000"]  # Empty
Thread 1: writes active_clients["127.0.0.1:5000"] = True
Thread 2: writes active_clients["127.0.0.1:5000"] = True
# PROBLEM: Two threads serving same client!

With lock (SAFE):
Thread 1: acquires lock, reads, writes, releases lock
Thread 2: waits for lock, then reads correct value
```

### 5.3 get_free_port() Function

```python
def get_free_port():
    """Find an available port for client session"""
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(('', 0))      # '' = 0.0.0.0, 0 = let OS choose
    port = temp_sock.getsockname()[1]  # Get assigned port
    temp_sock.close()
    return port
```

**How OS port assignment works:**
```
1. Request: bind(('', 0))
2. OS finds unused port in ephemeral range (49152-65535)
3. OS binds socket to that port
4. getsockname() returns ('0.0.0.0', 54321)
5. We extract port number: 54321
6. Close socket, freeing port for later use

NOTE: Tiny race condition exists between close() and
      re-bind(), but extremely unlikely in practice.
```

### 5.4 handle_client() Function - Download Handler

```python
def handle_client(client_addr, filename):
    """Handle file transfer for a single client on dedicated socket"""
    client_ip, client_port = client_addr  # Tuple unpacking
    session_id = f"{client_ip}:{client_port}"
```

**Tuple unpacking:**
```python
client_addr = ('127.0.0.1', 54321)
client_ip, client_port = client_addr
# client_ip = '127.0.0.1'
# client_port = 54321
```

#### 5.4.1 Creating Dedicated Session Socket

```python
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session_port = get_free_port()
    client_sock.bind((SERVER_IP, session_port))
    client_sock.settimeout(30)
```

**Why dedicated socket?**
```
Main socket (port 9000):
  - Receives all initial requests
  - Cannot distinguish ACKs from different clients

Session socket (port 54321):
  - Only this client communicates here
  - ACKs unambiguously belong to this transfer
```

#### 5.4.2 File Validation

```python
    filepath = "server_files/" + filename
    
    if not os.path.exists(filepath):
        error_msg = encrypt_data(b"ERROR File not found")
        client_sock.sendto(error_msg, client_addr)
        return
```

**Security consideration:**
```python
# VULNERABLE to path traversal:
filename = "../etc/passwd"
filepath = "server_files/" + "../etc/passwd"
# filepath = "server_files/../etc/passwd" = "/etc/passwd" !!!

# FIX (not implemented but should be):
filename = os.path.basename(filename)  # Removes path components
```

#### 5.4.3 Handshake: Sending Metadata

```python
    filesize = os.path.getsize(filepath)
    response = f"OK {filesize} {session_port}"
    client_sock.sendto(encrypt_data(response.encode()), client_addr)
```

**Response format:** `"OK <filesize> <session_port>"`
**Example:** `"OK 1048576 54321"` (1MB file, session port 54321)

#### 5.4.4 Waiting for Client Ready

```python
    try:
        ack_data, addr = client_sock.recvfrom(1024)
        ack = decrypt_data(ack_data).decode()
        if ack != "READY":
            return
    except socket.timeout:
        return
```

**Why this handshake?**
```
Server                              Client
   │                                   │
   │←── (sending to client's port) ────│
   │──── OK 1048576 54321 ────────────>│
   │                                   │
   │     Client now knows to send      │
   │     future messages to port 54321 │
   │                                   │
   │<──── READY (to port 54321) ───────│
   │                                   │
   │     Server confirms client        │
   │     received session port         │
```

#### 5.4.5 Chunk Reading and Packet Construction

```python
    with open(filepath, "rb") as f:
        seq = 0
        
        while running:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break  # End of file
            
            chunk_checksum = checksum(chunk)
            
            # Packet: "seq|checksum|binary_data"
            packet = f"{seq}|{chunk_checksum}|".encode() + chunk
            encrypted_packet = encrypt_data(packet)
```

**Packet construction visualized:**
```
seq = 5
chunk = b"\x89PNG\r\n..." (4096 bytes of binary)
checksum = "a1b2c3d4..."

Step 1: f"{seq}|{chunk_checksum}|"
        = "5|a1b2c3d4...|"

Step 2: .encode()
        = b"5|a1b2c3d4...|"

Step 3: + chunk
        = b"5|a1b2c3d4...|" + b"\x89PNG..."
        = b"5|a1b2c3d4...|\x89PNG..." (4133 bytes)

Step 4: encrypt_data(packet)
        = b"gAAAAABn..." (~5600 bytes)
```

#### 5.4.6 Reliable Delivery with ACK

```python
            while running and retries < max_retries:
                client_sock.sendto(encrypted_packet, client_addr)
                
                ready = select.select([client_sock], [], [], 2)
                
                if ready[0]:
                    ack_data, _ = client_sock.recvfrom(1024)
                    ack = decrypt_data(ack_data).decode()
                    
                    if ack == f"ACK {seq}":
                        retries = 0
                        break
                    elif ack.startswith("NACK"):
                        retries += 1
                else:
                    retries += 1  # Timeout
```

**select.select() explained:**
```python
select.select([sock], [], [], timeout)
#             ↑       ↑   ↑   ↑
#             │       │   │   └── Timeout in seconds
#             │       │   └────── Exception sockets (unused)
#             │       └────────── Writable sockets (unused)
#             └────────────────── Readable sockets

# Returns: ([readable], [writable], [exception])

# If data available: ready[0] = [sock]
# If timeout:        ready[0] = []
```

**Why select() instead of blocking recv()?**
- `recv()` blocks indefinitely without timeout
- `settimeout()` + `recv()` requires exception handling
- `select()` is more explicit and efficient for this pattern

#### 5.4.7 Stop-and-Wait ARQ Visualization

```
┌──────────────────────────────────────────────────────────┐
│                    STOP-AND-WAIT ARQ                     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Sender                              Receiver            │
│    │                                    │                │
│    │──── [Chunk 0] ────────────────────>│                │
│    │            (wait for ACK)          │                │
│    │<────────────────────── [ACK 0] ────│                │
│    │                                    │                │
│    │──── [Chunk 1] ────────────────────>│                │
│    │            (wait for ACK)          │                │
│    │              (TIMEOUT!)            │                │
│    │──── [Chunk 1] ────────────────────>│ (retransmit)  │
│    │<────────────────────── [ACK 1] ────│                │
│    │                                    │                │
│    │──── [Chunk 2] ────────────────────>│                │
│    │            (NACK received)         │                │
│    │<───────────────────── [NACK 2] ────│ (bad checksum)│
│    │──── [Chunk 2] ────────────────────>│ (retransmit)  │
│    │<────────────────────── [ACK 2] ────│                │
│    │                                    │                │
│    │──── [DONE] ───────────────────────>│                │
│    │                                    │                │
└──────────────────────────────────────────────────────────┘
```

#### 5.4.8 Cleanup with finally

```python
    finally:
        client_sock.close()
        with clients_lock:
            if session_id in active_clients:
                del active_clients[session_id]
```

**Why `finally`?**
- Executes regardless of how the function exits
- Normal return, exception, or early return all trigger it
- Guarantees resource cleanup

### 5.5 handle_upload() Function

(Similar structure to handle_client, but receives data instead of sending)

Key differences:
- Expects `UPLOAD filename size` command
- Sends `GO` signal to start receiving
- Calls `recvfrom()` to get chunks
- Sends `ACK`/`NACK` responses

### 5.6 main() Function - Event Loop

```python
def main():
    global running
    
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((SERVER_IP, MAIN_PORT))
    main_sock.settimeout(1)  # 1 second timeout for Ctrl+C handling
```

**Why 1-second timeout?**
```python
# Without timeout:
data, addr = main_sock.recvfrom(65535)  # Blocks forever
# Ctrl+C cannot be caught until data arrives

# With timeout:
while running:
    try:
        data, addr = main_sock.recvfrom(65535)
    except socket.timeout:
        continue  # Check 'running' flag every 1 second
```

#### 5.6.1 Main Event Loop

```python
    while running:
        try:
            data, addr = main_sock.recvfrom(65535)
            
            message = decrypt_data(data).decode()
            
            if message.startswith("REQUEST"):
                # ... spawn download thread
            elif message.startswith("UPLOAD"):
                # ... spawn upload thread
```

**Threading model:**
```
Main Thread                Worker Threads
     │
     │ recvfrom()
     │<────────────────── Request from Client 1
     │
     │ spawn thread ──────────────────────────> Thread 1: handle_client()
     │                                                │
     │ recvfrom()                                     │ (running)
     │<────────────────── Request from Client 2       │
     │                                                │
     │ spawn thread ──────────────────────────> Thread 2: handle_client()
     │                                                │         │
     │ recvfrom()                                     │         │
     │    ...                                         ▼         ▼
```

---

## 6. client.py - Detailed Breakdown

### 6.1 download_file() Function

#### 6.1.1 Initial Request

```python
def download_file(filename):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    
    request = encrypt_data(f"REQUEST {filename}".encode())
    sock.sendto(request, (SERVER_IP, MAIN_PORT))
```

**Encryption flow:**
```
"REQUEST test.pdf"
        │
        ▼ .encode()
b"REQUEST test.pdf"
        │
        ▼ encrypt_data()
b"gAAAAABn..."
        │
        ▼ sendto()
[Network] ──────────> Server
```

#### 6.1.2 Parsing Server Response

```python
    data, server_addr = sock.recvfrom(65535)
    response = decrypt_data(data).decode()
    
    # response = "OK 1048576 54321"
    parts = response.split()
    filesize = int(parts[1])     # 1048576
    session_port = int(parts[2]) # 54321
```

#### 6.1.3 Chunk Reception Loop

```python
    expected_seq = 0
    
    while True:
        packet, addr = sock.recvfrom(65535)
        decrypted = decrypt_data(packet)
        
        if decrypted == b"DONE":
            return True
        
        # Parse: b"5|a1b2c3...|<binary>"
        parts = decrypted.split(b"|", 2)
        seq = int(parts[0])
        received_checksum = parts[1].decode()
        chunk = parts[2]
```

**split(b"|", 2) explained:**
```python
data = b"5|a1b2c3|hello|world"

data.split(b"|")      # [b"5", b"a1b2c3", b"hello", b"world"]
data.split(b"|", 2)   # [b"5", b"a1b2c3", b"hello|world"]
                      #                    ↑ binary data preserved
```

**Why maxsplit=2?**
- The binary chunk might contain `|` byte (0x7C)
- Without maxsplit, we'd incorrectly split binary data
- With maxsplit=2, everything after second `|` stays intact

#### 6.1.4 Checksum Verification

```python
        if seq == expected_seq:
            calculated_checksum = checksum(chunk)
            
            if calculated_checksum == received_checksum:
                f.write(chunk)
                sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
                expected_seq += 1
            else:
                sock.sendto(encrypt_data(f"NACK {seq}".encode()), session_addr)
```

**Checksum verification visualized:**
```
Received packet:
  seq = 5
  received_checksum = "a1b2c3d4..."
  chunk = b"\x89PNG..."

Calculate locally:
  calculated_checksum = md5(b"\x89PNG...") = "a1b2c3d4..."

Compare:
  "a1b2c3d4..." == "a1b2c3d4..."  ✓ MATCH → Send ACK
  "a1b2c3d4..." != "x9y8z7w6..."  ✗ MISMATCH → Send NACK
```

#### 6.1.5 Handling Duplicate Packets

```python
        elif seq < expected_seq:
            # Already received this chunk, but ACK was lost
            # Re-send ACK so server can proceed
            sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
```

**Why this happens:**
```
Server                              Client
   │                                   │
   │──── Chunk 5 ─────────────────────>│
   │<────────────────────────── ACK 5 ─│
   │         (ACK 5 lost in network)   │
   │                                   │
   │     (timeout, retransmit)         │
   │──── Chunk 5 ─────────────────────>│
   │     Client already has chunk 5,   │
   │<────────────────────────── ACK 5 ─│  (expected_seq is now 6)
   │                                   │
   │──── Chunk 6 ─────────────────────>│
```

### 6.2 upload_file() Function

(Mirror of download logic, but client sends chunks and waits for ACKs)

### 6.3 main() Function - Interactive Menu

```python
def main():
    if len(sys.argv) > 1:
        # Command-line mode
        action = sys.argv[1].lower()
        if action == "download":
            download_file(sys.argv[2])
    else:
        # Interactive mode
        while True:
            choice = input("Select option (1/2/3): ")
            if choice == "3":
                break
```

---

## 7. Protocol State Machine

### 7.1 Download State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLIENT STATE MACHINE                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│    ┌───────────┐                                                │
│    │   IDLE    │                                                │
│    └─────┬─────┘                                                │
│          │ send REQUEST                                         │
│          ▼                                                      │
│    ┌───────────────┐                                            │
│    │ WAIT_METADATA │──── timeout ───> [ERROR]                   │
│    └───────┬───────┘                                            │
│            │ recv OK                                            │
│            ▼                                                    │
│    ┌───────────────┐                                            │
│    │  SEND_READY   │                                            │
│    └───────┬───────┘                                            │
│            │ send READY                                         │
│            ▼                                                    │
│    ┌───────────────┐                                            │
│    │ WAIT_CHUNK    │──── timeout ───> [ERROR]                   │
│    └───────┬───────┘                                            │
│            │ recv chunk                                         │
│            ▼                                                    │
│    ┌───────────────┐  checksum fail                             │
│    │ VERIFY_CHUNK  │───────────────> SEND_NACK ─┐               │
│    └───────┬───────┘                            │               │
│            │ checksum OK                        │               │
│            ▼                                    │               │
│    ┌───────────────┐                            │               │
│    │   SEND_ACK    │<───────────────────────────┘               │
│    └───────┬───────┘                                            │
│            │                                                    │
│            ├───── more chunks ────> WAIT_CHUNK                  │
│            │                                                    │
│            └───── recv DONE ────> [SUCCESS]                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Server Download State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                     SERVER STATE MACHINE                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│    ┌───────────┐                                                │
│    │ LISTENING │ <──────────────────────────────────────────┐   │
│    └─────┬─────┘                                             │   │
│          │ recv REQUEST                                      │   │
│          ▼                                                   │   │
│    ┌───────────────┐                                         │   │
│    │ CHECK_FILE    │──── not found ───> SEND_ERROR ──────────┘   │
│    └───────┬───────┘                                             │
│            │ file exists                                         │
│            ▼                                                     │
│    ┌───────────────┐                                             │
│    │ SEND_METADATA │                                             │
│    └───────┬───────┘                                             │
│            │ send OK + session_port                              │
│            ▼                                                     │
│    ┌───────────────┐                                             │
│    │ WAIT_READY    │──── timeout ───> [ABORT] ───────────────────┤
│    └───────┬───────┘                                             │
│            │ recv READY                                          │
│            ▼                                                     │
│    ┌───────────────┐                                             │
│    │  READ_CHUNK   │──── EOF ───> SEND_DONE ─────────────────────┤
│    └───────┬───────┘                                             │
│            │ chunk available                                     │
│            ▼                                                     │
│    ┌───────────────┐                                             │
│    │  SEND_CHUNK   │                                             │
│    └───────┬───────┘                                             │
│            │                                                     │
│            ▼                                                     │
│    ┌───────────────┐  timeout or NACK                           │
│    │   WAIT_ACK    │──────────────────> RETRY ──┐               │
│    └───────┬───────┘                            │               │
│            │ recv ACK                           │               │
│            │                                    │               │
│            └────────────────────────────────────┴──> READ_CHUNK │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. Concurrency Model

### 8.1 Thread Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         PROCESS                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    MAIN THREAD                           │    │
│  │                                                          │    │
│  │  main_sock (port 9000)                                   │    │
│  │       │                                                  │    │
│  │       │ recvfrom() ─── blocking (with 1s timeout)       │    │
│  │       │                                                  │    │
│  │       ├──── REQUEST ───> spawn Thread A                 │    │
│  │       ├──── UPLOAD ────> spawn Thread B                 │    │
│  │       └──── REQUEST ───> spawn Thread C                 │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐    │
│  │    THREAD A     │ │    THREAD B     │ │    THREAD C     │    │
│  │                 │ │                 │ │                 │    │
│  │ client_sock     │ │ client_sock     │ │ client_sock     │    │
│  │ (port 50001)    │ │ (port 50002)    │ │ (port 50003)    │    │
│  │                 │ │                 │ │                 │    │
│  │ handle_client() │ │ handle_upload() │ │ handle_client() │    │
│  │                 │ │                 │ │                 │    │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   SHARED RESOURCES                       │    │
│  │                                                          │    │
│  │  active_clients = {                                      │    │
│  │      "127.0.0.1:54321": True,  # Thread A               │    │
│  │      "127.0.0.1:54322": True,  # Thread B               │    │
│  │      "127.0.0.1:54323": True,  # Thread C               │    │
│  │  }                                                       │    │
│  │                                                          │    │
│  │  clients_lock = threading.Lock()  # Protects dictionary │    │
│  │                                                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Thread Lifecycle

```python
# Thread creation
t = threading.Thread(target=handle_client, args=(addr, filename))
t.daemon = True   # Thread dies when main thread exits
t.start()         # Begin execution
```

**Daemon thread behavior:**
```
NON-DAEMON (default):
  Main thread exits → Waits for worker threads to complete
  
DAEMON (t.daemon = True):
  Main thread exits → Worker threads immediately terminated
  
We use daemon threads because:
  - If user presses Ctrl+C, server should exit immediately
  - Ongoing transfers are abandoned (acceptable behavior)
```

### 8.3 Lock Usage Pattern

```python
# CORRECT - Context manager (auto-release)
with clients_lock:
    if session_id in active_clients:
        return  # Lock automatically released
    active_clients[session_id] = True
# Lock released here

# INCORRECT - Manual (error-prone)
clients_lock.acquire()
if session_id in active_clients:
    clients_lock.release()  # Easy to forget!
    return
active_clients[session_id] = True
clients_lock.release()
```

---

## 9. Error Handling Deep Dive

### 9.1 Error Types and Handling

| Error Type | Cause | Handling |
|------------|-------|----------|
| `socket.timeout` | No response within timeout | Retry or abort |
| `FileNotFoundError` | File doesn't exist | Send ERROR message |
| `cryptography.fernet.InvalidToken` | Decryption failed | Log warning, ignore packet |
| `ValueError` | Malformed packet | Ignore packet |
| `KeyboardInterrupt` | User pressed Ctrl+C | Graceful shutdown |

### 9.2 Exception Handling Pattern

```python
try:
    # Main operation
    packet = sock.recvfrom(65535)
    decrypted = decrypt_data(packet)
    
except socket.timeout:
    # Expected: no data within timeout
    # Action: retry or continue loop
    continue
    
except cryptography.fernet.InvalidToken:
    # Possible attack or corruption
    # Action: log and ignore
    print("[WARN] Decryption failed")
    continue
    
except Exception as e:
    # Unexpected error
    # Action: log and abort
    print(f"[ERROR] {e}")
    return False
    
finally:
    # Cleanup: always executed
    sock.close()
```

### 9.3 Graceful Shutdown

```python
running = True

def main():
    global running
    
    try:
        while running:
            # ... main loop
            
    except KeyboardInterrupt:
        print("\n[SERVER] Shutdown requested...")
        running = False  # Signal threads to stop
        
    finally:
        main_sock.close()
        # Daemon threads auto-terminate
```

---

## 10. Network Byte Flow

### 10.1 Complete Download Sequence

```
Time  Client                    Network                    Server
─────────────────────────────────────────────────────────────────────
  │
  │   ┌─────────────────┐
  │   │ sock.sendto()   │
t1│   │ "REQUEST test"  │ ──UDP──[encrypted]──────────> Port 9000
  │   └─────────────────┘                               │
  │                                                     ▼
  │                                              ┌─────────────┐
  │                                              │ recvfrom()  │
  │                                              │ decrypt()   │
  │                                              │ spawn thread│
  │                                              └─────────────┘
  │                                                     │
  │                                              Port 54321 created
  │                                                     │
  │   ┌─────────────────┐                              ▼
t2│   │ recvfrom()      │ <──UDP──[encrypted]── "OK 1024 54321"
  │   │ parse response  │
  │   └─────────────────┘
  │           │
  │           ▼
  │   ┌─────────────────┐
t3│   │ sendto(54321)   │ ──UDP──[encrypted]──────────> Port 54321
  │   │ "READY"         │                               │
  │   └─────────────────┘                              ▼
  │                                              ┌─────────────┐
  │                                              │ recvfrom()  │
  │                                              │ open file   │
  │                                              │ read chunk  │
  │                                              └─────────────┘
  │                                                     │
  │   ┌─────────────────┐                              ▼
t4│   │ recvfrom()      │ <──UDP──[encrypted]── "0|checksum|data"
  │   │ verify checksum │
  │   │ write to file   │
  │   └─────────────────┘
  │           │
  │           ▼
  │   ┌─────────────────┐
t5│   │ sendto(54321)   │ ──UDP──[encrypted]──────────> Port 54321
  │   │ "ACK 0"         │                               │
  │   └─────────────────┘                              ▼
  │                                              ┌─────────────┐
  │                                              │ recvfrom()  │
  │   ... (repeat for each chunk) ...            │ read next   │
  │                                              └─────────────┘
  │                                                     │
  │   ┌─────────────────┐                              ▼
t6│   │ recvfrom()      │ <──UDP──[encrypted]───────── "DONE"
  │   │ close file      │
  │   └─────────────────┘
  │
  ▼   [COMPLETE]
```

### 10.2 Packet Contents at Each Layer

```
Application Data: b"REQUEST test.pdf"

After encryption (Fernet):
┌────────────────────────────────────────────────────────┐
│ Version │ Timestamp │    IV    │ Ciphertext │  HMAC   │
│  (1B)   │   (8B)    │  (16B)   │   (var)    │  (32B)  │
└────────────────────────────────────────────────────────┘
│<────────────────── Base64 encoded ───────────────────>│

After UDP encapsulation:
┌────────────────────────────────────────────────────────┐
│ UDP Header │           Encrypted Payload               │
│   (8B)     │              (~100 bytes)                 │
├────────────┼───────────────────────────────────────────┤
│ Src Port   │ gAAAAABn...                               │
│ Dst Port   │                                           │
│ Length     │                                           │
│ Checksum   │                                           │
└────────────┴───────────────────────────────────────────┘

After IP encapsulation:
┌────────────────────────────────────────────────────────┐
│ IP Header  │ UDP Header │     Encrypted Payload       │
│  (20B)     │   (8B)     │        (~100 bytes)         │
├────────────┼────────────┼─────────────────────────────┤
│ Src IP     │ Src Port   │ gAAAAABn...                 │
│ Dst IP     │ Dst Port   │                             │
│ Protocol=17│ Length     │                             │
│ TTL, etc.  │ Checksum   │                             │
└────────────┴────────────┴─────────────────────────────┘
```

---

## Summary

This documentation covers:

1. **Socket Programming** - UDP socket creation, binding, sending, receiving
2. **Encryption** - Fernet's AES-128-CBC + HMAC-SHA256 implementation
3. **Integrity** - MD5 checksum calculation and verification
4. **Reliability** - Stop-and-Wait ARQ with ACK/NACK/retransmission
5. **Multi-client** - Threading model with dynamic port assignment
6. **Protocol** - State machines and message formats
7. **Error Handling** - Exception handling patterns
8. **Byte Flow** - Complete network packet visualization

This should give you everything you need to explain the technical implementation during your demo!
