# Reliable FTP - Custom File Transfer Protocol

A **reliable and secure file transfer system** built using UDP sockets with custom reliability mechanisms, encryption, and multi-client support.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Protocol Design](#protocol-design)
5. [Security Implementation](#security-implementation)
6. [Multi-Client Support](#multi-client-support)
7. [File Structure](#file-structure)
8. [Setup Instructions](#setup-instructions)
9. [Usage Guide](#usage-guide)
10. [Demo Scenarios](#demo-scenarios)
11. [Technical Details](#technical-details)

---

## Project Overview

### Problem Statement
Design and implement a **Reliable File Transfer Protocol** over UDP that provides:
- Chunk-based file transfer
- Integrity checking for data corruption detection
- Encrypted communication for security
- Support for multiple concurrent clients

### Why UDP?
UDP is connectionless and unreliable by default, but it offers:
- Lower overhead than TCP
- No connection setup delay
- Flexibility to implement custom reliability mechanisms

This project implements reliability **on top of UDP** using:
- Sequence numbers
- Acknowledgments (ACK/NACK)
- Retransmission on timeout
- Checksum verification

---

## Features

| Feature | Description |
|---------|-------------|
| **Chunk-based Transfer** | Files split into 4KB chunks for efficient transfer |
| **Reliable Delivery** | ACK-based protocol with retransmission on failure |
| **Integrity Checking** | MD5 checksum on each chunk to detect corruption |
| **Encryption** | Fernet symmetric encryption (AES-128-CBC + HMAC) |
| **Multi-Client** | Each client gets dedicated socket/port for isolation |
| **Upload & Download** | Bidirectional file transfer supported |
| **Interactive Client** | Menu-driven interface with persistent session |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         SERVER (server.py)                       │
│  ┌─────────────────┐                                             │
│  │  Main Socket    │  Port 9000 - Accepts initial requests       │
│  │  (UDP)          │                                             │
│  └────────┬────────┘                                             │
│           │                                                      │
│           │  On REQUEST/UPLOAD → Spawn Thread                    │
│           ▼                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Client Handler  │  │ Client Handler  │  │ Client Handler  │  │
│  │ Thread 1        │  │ Thread 2        │  │ Thread 3        │  │
│  │ Port: 50001     │  │ Port: 50002     │  │ Port: 50003     │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│    Client 1      │  │    Client 2      │  │    Client 3      │
│   (client.py)    │  │   (client.py)    │  │   (client.py)    │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Communication Flow

**Download Flow:**
```
Client                          Server (Port 9000)
  │                                   │
  │──── REQUEST filename ────────────>│
  │                                   │ (Spawn thread, assign port 50001)
  │<──── OK filesize 50001 ───────────│
  │                                   │
  │                          Server (Port 50001)
  │──── READY ───────────────────────>│
  │<──── [seq|checksum|chunk] ────────│
  │──── ACK seq ─────────────────────>│
  │<──── [seq|checksum|chunk] ────────│
  │──── ACK seq ─────────────────────>│
  │         ... (repeat) ...          │
  │<──── DONE ────────────────────────│
  │                                   │
```

**Upload Flow:**
```
Client                          Server (Port 9000)
  │                                   │
  │──── UPLOAD filename size ────────>│
  │                                   │ (Spawn thread, assign port 50002)
  │<──── OK 50002 ────────────────────│
  │                                   │
  │                          Server (Port 50002)
  │──── READY ───────────────────────>│
  │<──── GO ──────────────────────────│
  │──── [seq|checksum|chunk] ────────>│
  │<──── ACK seq ─────────────────────│
  │──── [seq|checksum|chunk] ────────>│
  │<──── ACK seq ─────────────────────│
  │         ... (repeat) ...          │
  │──── DONE ────────────────────────>│
  │<──── OK ──────────────────────────│
  │                                   │
```

---

## Protocol Design

### Packet Format

All packets are **encrypted** before transmission.

#### Data Packet (after decryption)
```
┌─────────────┬──────────────────────┬────────────────────┐
│ Sequence #  │   MD5 Checksum       │      Data          │
│  (integer)  │   (32 hex chars)     │   (up to 4KB)      │
└─────────────┴──────────────────────┴────────────────────┘
     |                   |                    |
     └───────── Separated by "|" ─────────────┘

Example: "0|a1b2c3d4e5f6...|<binary data>"
```

#### Control Messages
| Message | Direction | Description |
|---------|-----------|-------------|
| `REQUEST filename` | Client → Server | Request to download a file |
| `UPLOAD filename size` | Client → Server | Request to upload a file |
| `OK filesize port` | Server → Client | Download approved |
| `OK port` | Server → Client | Upload approved |
| `READY` | Client → Server | Client ready to receive/send |
| `GO` | Server → Client | Server ready to receive upload |
| `ACK seq` | Both | Acknowledgment for chunk |
| `NACK seq` | Both | Negative ACK (checksum failed) |
| `DONE` | Both | Transfer complete |
| `ERROR message` | Server → Client | Error occurred |

### Reliability Mechanism

1. **Sequence Numbers**: Each chunk has a sequence number (0, 1, 2, ...)
2. **Acknowledgments**: Receiver sends ACK for each chunk
3. **Timeout**: Sender waits 2 seconds for ACK
4. **Retransmission**: If no ACK received, resend the chunk
5. **Max Retries**: Give up after 5 failed attempts

```
Sender                              Receiver
   │                                    │
   │──── Chunk 0 ──────────────────────>│
   │                                    │ (Verify checksum)
   │<─────────────────────── ACK 0 ─────│
   │                                    │
   │──── Chunk 1 ──────────────────────>│
   │              (lost/corrupted)      │
   │        (2 sec timeout)             │
   │──── Chunk 1 ──────────────────────>│  (Retransmit)
   │<─────────────────────── ACK 1 ─────│
   │                                    │
```

---

## Security Implementation

### Encryption: Fernet (from `cryptography` library)

Fernet provides **authenticated encryption**:

| Component | Purpose |
|-----------|---------|
| **AES-128-CBC** | Symmetric encryption for confidentiality |
| **HMAC-SHA256** | Message authentication (detects tampering) |
| **Timestamp** | Prevents replay attacks |

### How It Works

```python
from cryptography.fernet import Fernet

# Same key on client and server (pre-shared)
KEY = b'Zr5rj1L6wF1c4z9sH0K2mYk7TqP8xA3vB6D9uE2nC4g='
cipher = Fernet(KEY)

# Encryption
encrypted = cipher.encrypt(b"Hello World")
# Output: b'gAAAAABn...' (base64 encoded)

# Decryption
decrypted = cipher.decrypt(encrypted)
# Output: b"Hello World"
```

### Security Properties

1. **Confidentiality**: Data is encrypted; attackers cannot read it
2. **Integrity**: HMAC detects any modification to ciphertext
3. **Authentication**: Only parties with the key can encrypt/decrypt
4. **Per-chunk checksum**: Additional MD5 verification layer

---

## Multi-Client Support

### The Problem
UDP is connectionless. If multiple clients connect, how does the server know which ACK belongs to which client?

### The Solution: Dynamic Port Assignment

1. Server listens on **main port (9000)** for initial requests
2. For each client, server creates a **new socket on a random port**
3. Server tells client the new port number
4. All subsequent communication happens on the dedicated port

```python
def get_free_port():
    """Find an available port"""
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(('', 0))  # OS assigns free port
    port = temp_sock.getsockname()[1]
    temp_sock.close()
    return port
```

### Thread Safety

```python
active_clients = {}
clients_lock = threading.Lock()

# Before starting a new client session:
with clients_lock:
    if session_id in active_clients:
        # Reject - client already has session
    active_clients[session_id] = True

# When session ends:
with clients_lock:
    del active_clients[session_id]
```

---

## File Structure

```
ReliableFTP/
│
├── server.py          # Main server application
│   ├── get_free_port()      - Finds available port
│   ├── handle_client()      - Handles file download
│   ├── handle_upload()      - Handles file upload
│   └── main()               - Main server loop
│
├── client.py          # Client application
│   ├── download_file()      - Downloads file from server
│   ├── upload_file()        - Uploads file to server
│   └── main()               - Interactive menu loop
│
├── encryption.py      # Encryption utilities
│   ├── KEY                  - Shared encryption key
│   ├── encrypt_data()       - Encrypts bytes using Fernet
│   └── decrypt_data()       - Decrypts bytes using Fernet
│
├── utils.py           # Utility functions
│   ├── CHUNK_SIZE           - 4096 bytes per chunk
│   ├── checksum()           - Calculates MD5 hash
│   └── verify_checksum()    - Compares checksums
│
├── requirements.txt   # Python dependencies
├── server_files/      # Files available for download
├── downloads/         # Downloaded files saved here
└── README.md          # This documentation
```

---

## Setup Instructions

### Prerequisites
- Python 3.8 or higher
- pip (Python package manager)

### Installation

```bash
# 1. Navigate to project directory
cd /path/to/ReliableFTP

# 2. Create virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

### Dependencies
```
cryptography>=46.0.0   # For Fernet encryption
```

---

## Usage Guide

### Starting the Server

```bash
# Terminal 1
python3 server.py
```

Output:
```
==================================================
       Reliable FTP Server (UDP + Encrypted)
       Listening on port: 9000
       Press Ctrl+C to stop server
==================================================

[SERVER] Ready to accept connections...
```

### Running the Client

#### Interactive Mode
```bash
# Terminal 2
python3 client.py
```

Output:
```
==================================================
       Reliable FTP Client (UDP + Encrypted)
==================================================

----------------------------------------
Options:
  1. Download file
  2. Upload file
  3. Exit
----------------------------------------
Select option (1/2/3): 
```

#### Command-Line Mode
```bash
# Download a file
python3 client.py download filename.pdf

# Upload a file
python3 client.py upload /path/to/file.pdf
```

### Adding Files to Server

Place files in the `server_files/` directory:
```bash
cp myfile.pdf server_files/
```

---

## Demo Scenarios

### Demo 1: Basic File Download

**Setup:**
1. Put a file in `server_files/` (e.g., `test.pdf`)
2. Start server: `python3 server.py`
3. Start client: `python3 client.py`

**Steps:**
1. Select option `1` (Download)
2. Enter filename: `test.pdf`
3. Watch the progress
4. Check `downloads/test.pdf`

**Expected Output (Client):**
```
[CLIENT] Requesting file: test.pdf
[INFO] File size: 1048576 bytes
[INFO] Session port: 45123

[DOWNLOAD] Starting...
[RECV] Chunk 0: 4096 bytes (0.4%) ✓
[RECV] Chunk 1: 4096 bytes (0.8%) ✓
...
[COMPLETE] Downloaded 1048576 bytes
[SUCCESS] File saved to downloads/test.pdf
```

### Demo 2: File Upload

**Steps:**
1. Select option `2` (Upload)
2. Enter path: `downloads/test.pdf`
3. File is uploaded to `server_files/`

### Demo 3: Multiple Concurrent Clients

**Setup:**
1. Start server: `python3 server.py`
2. Open 2-3 terminals

**Steps:**
1. In each terminal, run: `python3 client.py download test.pdf`
2. All downloads run simultaneously

**Expected Server Output:**
```
[REQUEST] 127.0.0.1:54321 -> REQUEST test.pdf
[SESSION] 127.0.0.1:54321 assigned to port 45001

[REQUEST] 127.0.0.1:54322 -> REQUEST test.pdf
[SESSION] 127.0.0.1:54322 assigned to port 45002

[REQUEST] 127.0.0.1:54323 -> REQUEST test.pdf
[SESSION] 127.0.0.1:54323 assigned to port 45003
```

**Key Point:** Each client gets a **different session port**, proving multi-client support.

### Demo 4: Integrity Verification

```bash
# After download, verify files are identical
diff server_files/test.pdf downloads/test.pdf
echo $?  # Should print 0 (no difference)

# Or use checksums
md5sum server_files/test.pdf downloads/test.pdf
# Both should show same hash
```

### Demo 5: Security - Encrypted Traffic

You can demonstrate that traffic is encrypted by capturing packets:
```bash
# Using tcpdump (requires sudo)
sudo tcpdump -i lo port 9000 -X
```
The captured data will show encrypted (unreadable) content.

---

## Technical Details

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAIN_PORT` | 9000 | Server's main listening port |
| `CHUNK_SIZE` | 4096 | Bytes per chunk (4 KB) |
| `TIMEOUT` | 5 sec | Client socket timeout |
| `MAX_RETRIES` | 5 | Max retransmission attempts |
| `SESSION_TIMEOUT` | 30 sec | Inactive client timeout |

### Socket Configuration

```python
# UDP Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# AF_INET     = IPv4
# SOCK_DGRAM  = UDP (datagram)
```

### Threading Model

- **Main Thread**: Listens for new connections on port 9000
- **Worker Threads**: One per client, handles full file transfer
- **Thread-safe**: Uses `threading.Lock()` for shared data

### Error Handling

| Error | Handling |
|-------|----------|
| File not found | Server sends `ERROR File not found` |
| Checksum mismatch | Receiver sends `NACK`, sender retransmits |
| Timeout | Sender retries up to 5 times |
| Client disconnect | Session timeout after 30 seconds |

---

## Evaluation Checklist ✅

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| UDP Sockets | ✅ | `socket.SOCK_DGRAM` |
| Chunk-based Transfer | ✅ | 4KB chunks with sequence numbers |
| Integrity Checking | ✅ | MD5 checksum per chunk |
| Security/Encryption | ✅ | Fernet (AES + HMAC) |
| Multiple Clients | ✅ | Dynamic port + threading |
| Upload & Download | ✅ | Bidirectional transfer |
| Reliable Delivery | ✅ | ACK/NACK + retransmission |

---

## Troubleshooting

### "Server not responding"
- Ensure server is running
- Check if port 9000 is available: `netstat -tulpn | grep 9000`

### "File not found"
- Ensure file exists in `server_files/` directory
- Check filename spelling

### "Decryption failed"
- Client and server must have same encryption key
- Check `encryption.py` on both sides

---

## Future Improvements

- Resume interrupted transfers
- Diffie-Hellman key exchange (instead of pre-shared key)
- Transfer progress bar
- File listing command
- Compression before transfer

---

## Author

Computer Networks Project - Reliable FTP Implementation

---

## License

This project is for educational purposes.
