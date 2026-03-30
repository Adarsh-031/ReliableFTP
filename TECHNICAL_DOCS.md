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
7. [app.py - Protocol Adapters & GUI](#7-apppy---protocol-adapters--gui)
8. [Protocol State Machine](#8-protocol-state-machine)
9. [Concurrency Model](#9-concurrency-model)
10. [Error Handling Deep Dive](#10-error-handling-deep-dive)
11. [Network Byte Flow](#11-network-byte-flow)

---

## 1. System Architecture

### 1.1 High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              GUI LAYER                                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  app.py  ─  Tkinter (HomeScreen / ServerScreen / ClientScreen)   │   │
│  │  TransferRow | LogBox | ProgressBar | Pause/Cancel Events        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                              ▲         ▲                                 │
│                   log_cb / transfer_cb │ prog_cb(frac, speed)           │
│                              │         │                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │   PROTOCOL ADAPTERS  (also in app.py)                            │   │
│  │   FileTransferServer          FileTransferClient                 │   │
│  │   ─ _accept_loop()            ─ connect() / disconnect()         │   │
│  │   ─ _handle_client()          ─ list_files()                     │   │
│  │   ─ _handle_upload()          ─ download()                       │   │
│  │                               ─ upload()                         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────┤
│                          APPLICATION LAYER                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    Reliable FTP Protocol                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │   │
│  │  │  Commands    │  │  Chunking    │  │  Sliding Window ARQ   │  │   │
│  │  │  REQUEST     │  │  4KB blocks  │  │  Window size = 5      │  │   │
│  │  │  UPLOAD      │  │              │  │  Go-Back-N retransmit │  │   │
│  │  │  LIST        │  │              │  │                       │  │   │
│  │  └──────────────┘  └──────────────┘  └───────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────┤
│                           SECURITY LAYER                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    Fernet Encryption                              │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │   │
│  │  │  AES-128-CBC    │  │  HMAC-SHA256    │  │  MD5 Checksum   │  │   │
│  │  │  Confidentiality│  │  Authentication │  │  Integrity      │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────┤
│                          TRANSPORT LAYER                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                        UDP Sockets                                │   │
│  │  ┌────────────────────────────────────────────────────────┐      │   │
│  │  │  socket.SOCK_DGRAM | Connectionless | Unreliable       │      │   │
│  │  └────────────────────────────────────────────────────────┘      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Why Each Layer Matters

| Layer | Purpose | Without It |
|-------|---------|------------|
| GUI | User interaction, progress feedback, pause/cancel | No visual interface |
| Protocol Adapters | Class-based bridge between GUI and network | GUI cannot talk to network |
| Application | File transfer logic, commands, window management | No way to request/send files reliably |
| Security | Encryption + Integrity | Data readable by attackers, tampering possible |
| Transport | UDP communication | No network communication |

### 1.3 Dual-Mode Architecture

The project supports **two usage modes** from the same codebase:

```
Mode 1 — Graphical (app.py):
  python app.py
    └─> Launches Tkinter GUI
    └─> Uses FileTransferServer / FileTransferClient class adapters
    └─> Progress via callbacks, pause/cancel via threading.Event

Mode 2 — Command-Line (server.py / client.py):
  python server.py        python client.py download myfile.zip
    └─> Standalone CLI      └─> Standalone CLI
    └─> Global state        └─> KeyboardInterrupt for pause
    └─> print() output      └─> sys.stdout progress bar
```

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
| **Select** | `select.select([sock], [], [], t)` | Non-blocking readiness check |
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

We chose UDP and built reliability ourselves (via Sliding Window ARQ) to demonstrate protocol design without TCP overhead.

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
│ 2. Pad data to 16-byte boundary  │
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

### 4.1 Complete Code

```python
"""
Utility functions for Reliable FTP
"""

import hashlib

# Chunk size for file transfer (4KB)
CHUNK_SIZE = 4096

def checksum(data):
    """Calculate MD5 checksum for data integrity verification"""
    return hashlib.md5(data).hexdigest()

def verify_checksum(data, expected_checksum):
    """Verify data integrity by comparing checksums"""
    return checksum(data) == expected_checksum
```

**Import Explanation:**
- `hashlib` - Python's built-in cryptographic hash library
- Provides MD5, SHA-1, SHA-256, etc.

### 4.2 Chunk Size Constant

```python
CHUNK_SIZE = 4096
```

**Why 4096 bytes?**

| Consideration | Explanation |
|---------------|-------------|
| **UDP MTU** | Max UDP payload ~65,507 bytes, but network MTU is typically 1500 bytes. 4KB leaves room for headers after fragmentation. |
| **Memory** | Small enough to buffer an entire window (5 × 4KB = 20KB) easily in RAM |
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

### 4.3 checksum() Function

```python
def checksum(data):
    return hashlib.md5(data).hexdigest()
```

**How MD5 Works:**

```
Input: b"Hello World" (any length)
           │
           ▼
┌──────────────────────────────────┐
│ MD5 Algorithm (128-bit hash)     │
│ - Processes data in 512-bit      │
│   blocks                         │
│ - 4 rounds of 16 operations      │
│ - Produces fixed 128-bit output  │
└──────────────────────────────────┘
           │
           ▼
Output: "b10a8db164e0754105b7a99be72e3fe5"
        (32 hex characters = 128 bits)
```

**Why MD5 for integrity (not security)?**
- Fast to compute
- 128-bit output is sufficient for transmission error detection
- Security is handled separately by Fernet
- Probability of accidental collision: 1 in 2^128

### 4.4 verify_checksum() Function

```python
def verify_checksum(data, expected_checksum):
    return checksum(data) == expected_checksum
```

A convenience wrapper used internally to compare a freshly computed checksum against the one embedded in a received packet. Returns `True` if the chunk is intact, `False` if corrupted.

> **Note:** In practice, the inline expression `checksum(chunk) == received_checksum` is used directly inside `server.py`, `client.py`, and `app.py`. `verify_checksum()` provides a cleaner API for future callers.

---

## 5. server.py - Detailed Breakdown

> `server.py` is the **standalone CLI server**. The GUI version wraps the same logic inside the `FileTransferServer` class in `app.py`. Both share identical protocol logic.

### 5.1 Imports and Constants

```python
import socket
import os
import threading
import select
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

SERVER_IP = "0.0.0.0"  # Listen on all interfaces
MAIN_PORT = 9000       # Main listening port
```

**Why `"0.0.0.0"`?**
```
0.0.0.0 = Listen on ALL network interfaces:
  - localhost (127.0.0.1)
  - LAN IP (192.168.x.x)
  - Public IP (if any)

vs 127.0.0.1 = Only localhost connections
```

### 5.2 Global State Variables

```python
active_clients = {}
clients_lock = threading.Lock()
running = True
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

> **GUI difference:** In `app.py`, these are instance attributes on `FileTransferServer` (`self.active_clients`, `self.clients_lock`, `self._running`), not globals.

### 5.3 get_free_port() Function

```python
def get_free_port():
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(('', 0))
    port = temp_sock.getsockname()[1]
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
```

### 5.4 handle_client() — Download Handler with Sliding Window

This is the most significant change from the original design. The server now uses a **Go-Back-N Sliding Window** instead of Stop-and-Wait for downloads.

```python
def handle_client(client_addr, filename, resume_seq=0):
```

**New `resume_seq` parameter:** Tells the server which chunk to start from. On a fresh download this is `0`; on a resume it is `existing_file_bytes // CHUNK_SIZE`.

#### 5.4.1 Resume Logic

```python
seq = resume_seq
if resume_seq > 0:
    f.seek(resume_seq * CHUNK_SIZE)
    print(f"[RESUME] Skipping to chunk {resume_seq} for {session_id}")
```

`f.seek()` jumps the file pointer directly to the byte offset where the partial download ended. No bytes are re-read or re-sent.

#### 5.4.2 Sliding Window (Go-Back-N ARQ)

```python
WINDOW_SIZE = 5
unacked_packets = {}   # {seq: encrypted_packet}
timeout_count = 0
max_retries = 5

while running:
    # Fill the window up to WINDOW_SIZE outstanding packets
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
        break  # All acknowledged, transfer complete

    ready = select.select([client_sock], [], [], 2)
    ...
```

**Window visualized:**

```
─── Sliding Window (size = 5) ───────────────────────────────────

Sequence:  0    1    2    3    4    5    6    7    8
           ■    ■    ■    ■    ■    □    □    □    □
           ▲                   ▲
     window base          window edge
     (oldest unacked)     (newest sent)

When ACK 0 arrives:
           0    1    2    3    4    5    6    7    8
                ■    ■    ■    ■    ■    □    □    □
                ▲                   ▲
          window advances,    new chunk 5 can now be sent
```

**unacked_packets dictionary:** Maps sequence numbers to their encrypted payloads. Packets stay here until their ACK arrives. On timeout, everything in the dictionary is retransmitted (Go-Back-N).

#### 5.4.3 ACK Processing with select()

```python
ready = select.select([client_sock], [], [], 2)

if ready[0]:
    ack_data, _ = client_sock.recvfrom(1024)
    ack = decrypt_data(ack_data).decode()

    if ack.startswith("ERROR"):
        # Client paused or cancelled
        print(f"[PAUSED] Client {session_id} halted transfer.")
        return

    if ack.startswith("ACK"):
        ack_seq = int(ack.split()[1])
        if ack_seq in unacked_packets:
            del unacked_packets[ack_seq]
            timeout_count = 0

    elif ack.startswith("NACK"):
        nack_seq = int(ack.split()[1])
        if nack_seq in unacked_packets:
            client_sock.sendto(unacked_packets[nack_seq], client_addr)
else:
    # Timeout: retransmit entire window
    timeout_count += 1
    for p_seq, p_data in unacked_packets.items():
        client_sock.sendto(p_data, client_addr)
    if timeout_count >= max_retries:
        ...  # Abort
```

**`select.select()` explained:**
```python
select.select([sock], [], [], timeout)
#              ↑       ↑   ↑   ↑
#              │       │   │   └── Timeout in seconds (2s here)
#              │       │   └────── Exception sockets (unused)
#              │       └────────── Writable sockets (unused)
#              └────────────────── Readable sockets

# Returns: ([readable], [writable], [exception])
# Data available: ready[0] = [sock]
# Timeout:        ready[0] = []
```

**Why `select()` instead of blocking `recv()`?**
- The window sends multiple packets before waiting; we cannot afford blocking indefinitely
- `select()` with a 2-second timeout allows efficient retransmission of the whole window on silence
- More explicit and readable than `settimeout()` + exception handling inside a window loop

#### 5.4.4 Pause / Cancel Handling

When a client pauses or cancels (via GUI button or `KeyboardInterrupt`), it sends `ERROR Client paused transfer` or `ERROR Client cancelled`. The server detects any `ERROR`-prefixed message and exits the transfer thread cleanly, preserving the partial file on the client side for resumption.

### 5.5 handle_upload() — Upload Handler (Stop-and-Wait)

Uploads use the simpler **Stop-and-Wait** protocol since they originate from the client, which already has full flow control:

```
Client                            Server
  │                                  │
  │──── UPLOAD filename filesize ───>│  (main port 9000)
  │<─── OK session_port ─────────────│
  │──── READY (to session_port) ────>│
  │<─── GO ──────────────────────────│
  │──── seq|checksum|data ──────────>│
  │<─── ACK seq ─────────────────────│
  │──── seq|checksum|data ──────────>│
  │<─── ACK seq (or NACK) ───────────│
  │  ...                             │
  │──── DONE ───────────────────────>│
  │<─── OK ──────────────────────────│
```

**Handshake note:** The server sends `GO` after receiving `READY`. This confirms the session socket is bound and ready to receive chunks — preventing the client from sending data before the server is listening on the session port.

### 5.6 Main Loop

```python
def main():
    global running
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((SERVER_IP, MAIN_PORT))
    main_sock.settimeout(1)
    ...
    while running:
        try:
            data, addr = main_sock.recvfrom(65535)
            message = decrypt_data(data).decode()
            
            if message.startswith("REQUEST"):
                parts = message.split()
                filename = parts[1]
                resume_seq = int(parts[2]) if len(parts) > 2 else 0
                ...spawn thread → handle_client(addr, filename, resume_seq)

            elif message.startswith("UPLOAD"):
                ...spawn thread → handle_upload(addr, filename, filesize)
```

> **Note:** The `LIST` command is handled only in `app.py`'s `_accept_loop()`, not in the CLI `server.py`. The CLI server supports `REQUEST` and `UPLOAD` only.

---

## 6. client.py - Detailed Breakdown

> `client.py` is the **standalone CLI client**. For GUI use, `FileTransferClient` in `app.py` provides the same functionality with callback-based progress and threading.Event pause/cancel.

### 6.1 download_file() — Resumable Download

#### 6.1.1 Resume Detection

```python
resume_seq = 0
file_mode = "wb"
if os.path.exists(filepath):
    existing_size = os.path.getsize(filepath)
    resume_seq = existing_size // CHUNK_SIZE
    file_mode = "ab"
    print(f"[INFO] Partial file detected. Resuming from chunk {resume_seq}...")
```

**Integer division:** `existing_size // CHUNK_SIZE` gives the last fully received chunk number. Any trailing partial chunk is discarded (the file is opened in append mode, not truncated).

**File modes:**
| Mode | Meaning |
|------|---------|
| `"wb"` | Write binary — create new file or overwrite existing |
| `"ab"` | Append binary — write to end of existing file |

#### 6.1.2 Request with Resume Sequence

```python
request = encrypt_data(f"REQUEST {filename} {resume_seq}".encode())
sock.sendto(request, (SERVER_IP, MAIN_PORT))
```

The resume sequence is embedded in the `REQUEST` command. The server receives this and seeks directly to `resume_seq * CHUNK_SIZE` in the file.

#### 6.1.3 Sliding Window Receiver Logic

```python
if seq == expected_seq:
    if checksum(chunk) == received_checksum:
        f.write(chunk)
        received_bytes += len(chunk)
        sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
        expected_seq += 1
    else:
        sock.sendto(encrypt_data(f"NACK {seq}".encode()), session_addr)
elif seq < expected_seq:
    # Duplicate from window retransmission — re-ACK without writing
    sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
```

**Duplicate handling:** Because the server uses a sliding window, the client may receive a chunk it already wrote (if a timeout caused the whole window to be retransmitted). The `seq < expected_seq` branch catches this and re-ACKs without writing duplicate data.

#### 6.1.4 Pause via KeyboardInterrupt (CLI)

```python
except KeyboardInterrupt:
    print(f"\n[PAUSED] Transfer manually paused.")
    stop_msg = encrypt_data(b"ERROR Client paused transfer")
    sock.sendto(stop_msg, session_addr)
    sock.close()
    return False
```

The partial file remains on disk. The next invocation of `download_file()` will detect it, compute `resume_seq`, and continue from where it left off.

### 6.2 upload_file() — Stop-and-Wait Upload

Uploads use classic Stop-and-Wait with up to 5 retries per chunk:

```python
while retries < max_retries:
    sock.sendto(packet, session_addr)
    try:
        ack_data, _ = sock.recvfrom(1024)
        ack = decrypt_data(ack_data).decode()
        if ack == f"ACK {seq}":
            retries = 0
            break
        elif ack.startswith("NACK"):
            retries += 1
    except socket.timeout:
        retries += 1
```

If `max_retries` is exceeded, the upload is aborted and `False` is returned.

---

## 7. app.py — Protocol Adapters & GUI

`app.py` is the main entry point for the graphical application. It contains three logical sections:

1. **`FileTransferServer`** — class-based server adapter
2. **`FileTransferClient`** — class-based client adapter
3. **GUI layer** — Tkinter screen and widget classes

### 7.1 FileTransferServer Class

```python
class FileTransferServer:
    def __init__(self, files_dir, port, log_cb, transfer_cb):
        self.files_dir = files_dir
        self.port = port
        self.log_cb = log_cb          # fn(message, tag?) → update log UI
        self.transfer_cb = transfer_cb # fn(event_dict)   → update progress UI
        self._running = False
        ...
```

**Callbacks instead of print():**

| Callback | Signature | Used for |
|----------|-----------|----------|
| `log_cb` | `(msg: str, tag: str = "info")` | Server log messages |
| `transfer_cb` | `(event: dict)` | Progress updates to GUI |

**Transfer event dict structure:**
```python
# Transfer starting
{"tid": session_id, "event": "start",    "filename": ..., "total": ..., "sent": ...}
# Progress update
{"tid": session_id, "event": "progress", "sent": bytes_sent_so_far}
# Transfer complete
{"tid": session_id, "event": "done"}
# Transfer failed
{"tid": session_id, "event": "error",    "detail": "...reason..."}
```

#### 7.1.1 LIST Command (New in dev branch)

```python
elif message == "LIST":
    files = []
    for f in os.listdir(self.files_dir):
        p = os.path.join(self.files_dir, f)
        if os.path.isfile(p):
            files.append({
                "name": f,
                "size": os.path.getsize(p),
                "modified": os.path.getmtime(p)
            })
    resp = "FILELIST " + json.dumps(files)
    self.main_sock.sendto(encrypt_data(resp.encode()), addr)
```

`LIST` serves two purposes:
1. **File browser** — the GUI uses it to populate the download table
2. **Connection test** — `FileTransferClient.connect()` sends `LIST` and checks for a `FILELIST` response to confirm the server is reachable

**Response format:** `FILELIST [{"name": "...", "size": N, "modified": N}, ...]`

### 7.2 FileTransferClient Class

```python
class FileTransferClient:
    def __init__(self, host, port, download_dir, log_cb):
        ...
        self.connected = False
```

**Key methods:**

| Method | Description |
|--------|-------------|
| `connect()` | Sends `LIST`, verifies `FILELIST` response, sets `self.connected = True` |
| `disconnect()` | Sets `self.connected = False` |
| `list_files()` | Returns `List[dict]` of file metadata from server |
| `download(filename, prog_cb, cancel_evt, pause_evt)` | Resumable download with GUI callbacks |
| `upload(filepath, prog_cb, cancel_evt, pause_evt)` | Stop-and-Wait upload with GUI callbacks |

#### 7.2.1 Pause and Cancel via threading.Event

Unlike the CLI which uses `KeyboardInterrupt`, the GUI uses `threading.Event` objects that the GUI thread can set at any time:

```python
def download(self, filename, prog_cb, cancel_evt, pause_evt):
    ...
    while True:
        if cancel_evt.is_set():
            sock.sendto(encrypt_data(b"ERROR Client cancelled"), session_addr)
            raise Cancelled("Cancelled by user")
        if pause_evt.is_set():
            sock.sendto(encrypt_data(b"ERROR Client paused transfer"), session_addr)
            raise Cancelled("Paused by user")
        ...
```

**Event flow:**
```
GUI Thread                         Transfer Thread
    │                                    │
    │  User clicks ⏸ Pause              │
    │─── pause_evt.set() ───────────────>│
    │                                    │  (checks at top of recv loop)
    │                                    │──> sends ERROR to server
    │                                    │──> raises Cancelled
    │                                    │
    │  Thread exits, partial file saved  │
    │  Next download auto-resumes        │
```

#### 7.2.2 Real-Time Progress Callback

```python
now = time.time()
if now - last_update > 0.1:   # throttle to max 10 updates/sec
    speed = (received_bytes / 1024 / 1024) / max(0.001, now - start_time)
    frac = received_bytes / filesize if filesize > 0 else 1.0
    prog_cb(frac, speed)
    last_update = now
```

`prog_cb(frac, speed)` is called at most 10 times per second to avoid flooding the Tkinter event loop. `frac` is `0.0 – 1.0`, `speed` is MB/s.

## 7.3 GUI
**Thread-safe GUI updates (queue pattern):**

The transfer threads run in background threads and cannot touch Tkinter widgets directly (Tkinter is not thread-safe). Instead they push events onto a `queue.Queue`, which is drained by `_poll()` running every 100ms on the main thread via `self.after(100, self._poll)`.

```
Transfer Thread                  Main (GUI) Thread
      │                                │
      │ self._q.put(("progress", ...)) │
      │                                │
      │                         _poll() fires every 100ms
      │                                │
      │                    item = self._q.get_nowait()
      │                    row.update_progress(frac, speed)
```

---

## 8. Protocol State Machine

### 8.1 Complete Command Reference

| Command | Direction | Format | Response |
|---------|-----------|--------|----------|
| `LIST` | Client → Server | `LIST` | `FILELIST <json>` |
| `REQUEST` | Client → Server | `REQUEST <filename> <resume_seq>` | `OK <filesize> <session_port>` or `ERROR ...` |
| `UPLOAD` | Client → Server | `UPLOAD <filename> <filesize>` | `OK <session_port>` or `ERROR ...` |
| `READY` | Client → Server | `READY` | (triggers data flow) |
| `GO` | Server → Client | `GO` | (upload only, confirms session ready) |
| `ACK` | Bidirectional | `ACK <seq>` | — |
| `NACK` | Bidirectional | `NACK <seq>` | — |
| `DONE` | Sender → Receiver | `DONE` | `OK` (upload only) |
| `ERROR` | Either | `ERROR <reason>` | — |

### 8.2 Download State Machine

```
Client State                          Server State
────────────────────────────────────────────────────
IDLE                                  LISTENING (port 9000)
  │                                       │
  │── REQUEST filename resume_seq ───────>│
  │                                  DISPATCHING
  │                                  (spawn thread, bind session port)
  │                                       │
  │<── OK filesize session_port ──────────│
HANDSHAKING                          SESSION_READY (port N)
  │                                       │
  │── READY (to session port) ───────────>│
  │                                  SENDING
  │                                  (fill window, send chunks)
RECEIVING                                 │
  │<── seq|checksum|chunk ────────────────│  (up to 5 in-flight)
  │── ACK seq ───────────────────────────>│
  │  ... (window slides) ...              │
  │── ERROR (if paused/cancelled) ───────>│ → ABORTED
  │<── DONE ──────────────────────────────│
COMPLETE                             COMPLETE
```

### 8.3 Upload State Machine

```
Client State                          Server State
────────────────────────────────────────────────────
IDLE                                  LISTENING (port 9000)
  │                                       │
  │── UPLOAD filename filesize ──────────>│
  │                                  DISPATCHING
  │<── OK session_port ───────────────────│
  │                                  SESSION_READY (port N)
  │── READY ─────────────────────────────>│
  │<── GO ────────────────────────────────│
SENDING                              RECEIVING
  │── seq|checksum|chunk ───────────────>│  (Stop-and-Wait)
  │<── ACK seq ──────────────────────────│
  │── seq|checksum|chunk ───────────────>│
  │<── NACK seq (retry) ─────────────────│
  │  ...                                 │
  │── DONE ─────────────────────────────>│
  │<── OK ───────────────────────────────│
COMPLETE                             COMPLETE
```

---

## 9. Concurrency Model

### 9.1 Thread Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Main / GUI Thread                        │
│                                                              │
│  Tkinter mainloop()  ←──── _poll() every 100ms              │
│                                  │                           │
│                            queue.Queue                       │
│                                  ▲                           │
│  ┌───────────────────────────────│─────────────────────────┐ │
│  │           Background Threads (all daemon=True)           │ │
│  │                                                          │ │
│  │  _accept_loop   handle_client  handle_upload  _dl_thread │ │
│  │  (server loop)  (one per DL)   (one per UL)  (GUI DL)   │ │
│  │                                                          │ │
│  └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 Thread Lifecycle

```python
t = threading.Thread(target=handle_client, args=(addr, filename, resume_seq))
t.daemon = True    # Thread dies when main thread exits
t.start()
```

**Daemon thread behaviour:**
```
NON-DAEMON (default):
  Main thread exits → Waits for worker threads to complete

DAEMON (t.daemon = True):
  Main thread exits → Worker threads immediately terminated

We use daemon threads because:
  - If user closes the window, all transfers are abandoned
  - Prevents zombie processes hanging after GUI exit
```

### 9.3 Lock Usage Pattern

```python
# CORRECT - Context manager (auto-release on exception too)
with clients_lock:
    if session_id in active_clients:
        return
    active_clients[session_id] = True
# Lock released here automatically

# INCORRECT - Manual (error-prone, easy to miss release on early return)
clients_lock.acquire()
if session_id in active_clients:
    clients_lock.release()  # Easy to forget!
    return
active_clients[session_id] = True
clients_lock.release()
```

### 9.4 threading.Event for Pause / Cancel (GUI)

```python
cancel_evt = threading.Event()
pause_evt  = threading.Event()

# GUI thread sets the event:
cancel_evt.set()   # signals transfer thread to abort
pause_evt.set()    # signals transfer thread to pause

# Transfer thread checks atomically:
if cancel_evt.is_set(): raise Cancelled(...)
if pause_evt.is_set():  raise Cancelled(...)
```

`threading.Event` is thread-safe by design — no lock needed. `is_set()` returns `True` immediately after `set()` is called from any thread.

---

## 10. Error Handling Deep Dive

### 10.1 Error Types and Handling

| Error Type | Cause | Handling |
|------------|-------|----------|
| `socket.timeout` | No response within timeout | Retry or abort |
| `FileNotFoundError` | File doesn't exist on server | Send `ERROR File not found` |
| `cryptography.fernet.InvalidToken` | Decryption failed (corrupt or wrong key) | Silently `continue` (ignore packet) |
| `ValueError` | Malformed packet (bad split) | Silently `continue` |
| `Cancelled` | User pause/cancel via Event | Save state, notify server via `ERROR` |
| `KeyboardInterrupt` (CLI) | User pressed Ctrl+C during download | Save partial file, send `ERROR` to server |
| `OSError` | Socket closed mid-transfer | Caught in outer `except Exception` |

### 10.2 Exception Handling Pattern

```python
try:
    packet = sock.recvfrom(65535)
    decrypted = decrypt_data(packet)

except socket.timeout:
    # Expected: no data within timeout
    continue

except cryptography.fernet.InvalidToken:
    # Possible corruption or wrong key
    continue

except Exception as e:
    print(f"[ERROR] {e}")
    return False

finally:
    sock.close()   # Always executed, even on exception
```

### 10.3 Graceful Shutdown (CLI Server)

```python
running = True

try:
    while running:
        ...  # main loop

except KeyboardInterrupt:
    print("\n[SERVER] Shutdown requested...")
    running = False

finally:
    main_sock.close()
    # Daemon threads auto-terminate
```

### 10.4 Graceful Shutdown (GUI Server)

```python
def stop(self):
    self._running = False      # Signals _accept_loop and _handle_client to exit
    if self.main_sock:
        self.main_sock.close() # Unblocks recvfrom()
    self.log_cb("Server stopped")
```

`self._running = False` is checked at the top of every `while` loop in server threads, ensuring clean exit without abrupt socket closure.

---

## 11. Network Byte Flow

### 11.1 Complete Download Sequence (Sliding Window)

```
Time  Client                    Network                    Server
─────────────────────────────────────────────────────────────────────
  │
  │   ┌─────────────────────┐
  │   │ "REQUEST test.zip 0"│
t1│   │  (or resume_seq > 0)│──UDP──[encrypted]──────────> Port 9000
  │   └─────────────────────┘                              │
  │                                                        ▼
  │                                               ┌─────────────────┐
  │                                               │ decrypt, parse  │
  │                                               │ spawn thread    │
  │                                               │ bind port 54321 │
  │                                               └─────────────────┘
  │   ┌─────────────────────┐                             │
t2│   │ recvfrom()          │<──UDP──[encrypted]── "OK 2097152 54321"
  │   │ parse filesize+port │
  │   └─────────────────────┘
  │           │
  │           ▼
  │   ┌─────────────────────┐
t3│   │ "READY" → port 54321│──UDP──[encrypted]──────────> Port 54321
  │   └─────────────────────┘                              │
  │                                                        ▼
  │                                               ┌─────────────────┐
  │                                               │ fill window (5) │
  │                                               │ send chunks 0-4 │
  │                                               └─────────────────┘
  │   ┌─────────────────────┐                             │
t4│   │ recv "0|chksum|data"│<──────────────────── Chunk 0│
  │   │ recv "1|chksum|data"│<──────────────────── Chunk 1│  (5 in-flight)
  │   │ recv "2|chksum|data"│<──────────────────── Chunk 2│
  │   │ recv "3|chksum|data"│<──────────────────── Chunk 3│
  │   │ recv "4|chksum|data"│<──────────────────── Chunk 4│
  │   └─────────────────────┘
  │           │
  │    verify checksum
  │    write to file
  │           │
  │   ┌─────────────────────┐
t5│   │ "ACK 0"             │──────────────────────────────> Port 54321
  │   │ "ACK 1"             │──────────────────────────────>
  │   │  ...                │
  │   └─────────────────────┘                              │
  │                                                   window slides
  │                                               ┌─────────────────┐
  │                                               │ send chunks 5-9 │
  │                                               └─────────────────┘
  │     ... (repeat for each window) ...
  │
  │   ┌─────────────────────┐                             │
t6│   │ recv "DONE"         │<──UDP──[encrypted]──────────┘
  │   │ close file          │
  │   │ prog_cb(1.0, speed) │
  │   └─────────────────────┘
  │
  ▼   [COMPLETE]
```

### 11.2 Packet Contents at Each Layer

```
Application Data: b"REQUEST test.zip 0"

After encryption (Fernet):
┌────────────────────────────────────────────────────────┐
│ Version │ Timestamp │    IV    │ Ciphertext │  HMAC   │
│  (1B)   │   (8B)    │  (16B)   │   (var)    │  (32B)  │
└────────────────────────────────────────────────────────┘
│<────────────────── Base64 encoded ───────────────────>│

Data chunk packet (before encryption):
┌────────────────────────────────────────────────────────┐
│  seq  │  |  │     MD5 checksum (32 hex)    │  |  │ data│
│ "42"  │ "|" │  "a1b2c3d4e5f6..."          │ "|" │ 4KB │
└────────────────────────────────────────────────────────┘
      split(b"|", 2) → [b"42", b"a1b2...", b"<data>"]

After UDP encapsulation:
┌────────────────────────────────────────────────────────┐
│ UDP Header │           Encrypted Payload               │
│   (8B)     │              (~5600 bytes)                │
├────────────┼───────────────────────────────────────────┤
│ Src Port   │ gAAAAABn...                               │
│ Dst Port   │                                           │
│ Length     │                                           │
│ Checksum   │                                           │
└────────────┴───────────────────────────────────────────┘

After IP encapsulation:
┌────────────────────────────────────────────────────────┐
│ IP Header  │ UDP Header │     Encrypted Payload       │
│  (20B)     │   (8B)     │        (~5600 bytes)        │
├────────────┼────────────┼─────────────────────────────┤
│ Src IP     │ Src Port   │ gAAAAABn...                 │
│ Dst IP     │ Dst Port   │                             │
│ Protocol=17│ Length     │                             │
│ TTL, etc.  │ Checksum   │                             │
└────────────┴────────────┴─────────────────────────────┘
```

---

## Summary

This documentation covers the full implementation:

1. **Socket Programming** — UDP socket creation, binding, sending, receiving, `select()`
2. **Encryption** — Fernet's AES-128-CBC + HMAC-SHA256, shared key lifecycle
3. **Integrity** — MD5 checksum calculation, verification, and `verify_checksum()` helper
4. **Reliability** — Sliding Window Go-Back-N ARQ (server → client downloads) and Stop-and-Wait ARQ (client → server uploads)
5. **Resume** — `REQUEST <filename> <resume_seq>`, server `f.seek()`, client append mode
6. **Pause/Cancel** — `threading.Event` in GUI mode, `KeyboardInterrupt` in CLI mode; both send `ERROR` to peer
7. **File Listing** — `LIST` / `FILELIST <json>` command for browsing and connection testing
8. **Multi-client** — Per-session daemon threads, ephemeral port assignment, `threading.Lock` for session registry
9. **GUI Architecture** — Class-based adapters, queue-based thread-safe UI updates, `_poll()` pattern, `TransferRow` with live progress
10. **Protocol** — Full command reference, state machines for download and upload flows
11. **Byte Flow** — Complete network packet visualization for the sliding window download sequence