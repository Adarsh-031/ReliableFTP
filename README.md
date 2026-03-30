```markdown
# Reliable FTP - Secure UDP File Transfer

A high-performance, reliable, and secure file transfer application built over UDP sockets. This project implements a custom chunk-based protocol with Stop-and-Wait/Sliding Window ARQ mechanisms, ensuring guaranteed delivery without the overhead of TCP. It features a modern, concurrent Tkinter GUI and supports secure, multi-client communication.

## ✨ Features

* **Modern Unified GUI**: A dark-themed, responsive Tkinter interface managing both Client and Server modes with real-time progress bars and throughput metrics.
* **Custom Reliability over UDP**: Implements sequence numbers, ACK/NACK responses, and automatic timeout retransmissions to guarantee packet delivery.
* **Extreme Concurrency**: Multi-threaded server architecture that dynamically assigns ephemeral ports, allowing simultaneous, non-blocking transfers for multiple clients.
* **End-to-End Security**: All network traffic is encrypted using Fernet (AES-128-CBC + HMAC-SHA256) to ensure confidentiality and prevent replay attacks.
* **Data Integrity**: Every 4KB chunk is validated using MD5 checksums to detect and reject corrupted packets.
* **Resumable Transfers**: Automatically detects partial downloads and resumes from the last successfully received sequence, saving time and bandwidth.

---

## 🛠 Prerequisites & Setup

This application requires **Python 3.12+**. Clone or extract the repository files to your local machine before proceeding.

You can set up the project using either the modern, ultra-fast `uv` package manager (recommended) or standard `pip`.

### Option A: Using `uv` (Recommended)

Since this project includes `pyproject.toml` and `uv.lock` files, `uv` is the most efficient way to get up and running.

1. **Install uv** (if not already installed):

   * **macOS/Linux**:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   * **Windows (PowerShell)**:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```

2. **Sync the project dependencies**:

   This automatically resolves dependencies from the lockfile and creates an isolated environment.
   ```bash
   uv sync
   ```

> **Note:** When using `uv`, you do not need to manually activate the virtual environment. You can use the `uv run` command as shown in the Usage section below.

---

### Option B: Using standard `pip`

1. **Create a virtual environment**:

   * macOS/Linux:
     ```bash
     python3 -m venv .venv
     ```
   * Windows:
     ```bat
     python -m venv .venv
     ```

2. **Activate the environment**:

   * macOS/Linux:
     ```bash
     source .venv/bin/activate
     ```
   * Windows:
     ```bat
     .venv\Scripts\activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🚀 Usage Instructions

The entire application—both client and server—is driven by a single unified graphical interface.

### Starting the Application

If you are using **uv** (Option A), run the application directly. `uv run` will automatically use the correct virtual environment:
```bash
uv run app.py
```

If you are using **pip** (Option B), ensure your virtual environment is activated, then run:
```bash
python app.py
```

---

### Running the Server (Host)

1. Select **Start Server** from the home screen.
2. **Port**: Leave the default (`9000`) or enter a custom listening port.
3. **Files Dir**: Click **Browse** to select the directory you want to share with clients (e.g., `./server_files`).
4. Click **▶ Start Server**.
5. The server will now run in the background, listening for incoming connections. Active transfers and connection events will stream into the **Server Log**.

---

### Running the Client (Connect)

1. Select **Open Client** from the home screen.

2. **Configuration**:
   * **Host**: Enter the server's IP address (use `127.0.0.1` if testing locally).
   * **Port**: Enter the server's listening port (`9000`).
   * **DL Dir**: Select where downloaded files should be saved.

3. Click **⚡ Connect**. The client will authenticate and retrieve the server's file list.

4. **Downloading**:
   * Navigate to the **⬇ Download** tab.
   * Select a file from the server list and click **Download Selected**.
   * You can pause/resume the transfer at any time. The UI will display real-time throughput (MB/s).

5. **Uploading**:
   * Navigate to the **⬆ Upload** tab.
   * Click **Browse...** to select a local file.
   * Click **Upload File** to securely send it to the server.

---

## 🧠 System Architecture

This protocol achieves scalability and performance by decoupling the initial handshake from the data stream:

1. **Main Listener (Port 9000)**: The server continuously polls this port. When a `REQUEST` or `UPLOAD` command is received, it validates the request and spawns a daemon thread.

2. **Dynamic Session Ports**: The spawned thread binds to a new, randomly assigned ephemeral port (e.g., `54321`) and replies to the client with this port.

3. **Isolated Transfer Streams**: The client switches its target to the new session port. This ensures that massive file chunks do not clog the main listener, allowing the server to handle high concurrency efficiently.

---
---

## 📝 Notes for Beginners

Ensure Python 3.12+ is installed before setup
If GUI fails, install dependencies manually using pip
Use localhost (127.0.0.1) for testing on same machine

## 📁 File Structure

| File | Description |
|------|-------------|
| `app.py` | The main GUI application containing the `FileTransferServer` and `FileTransferClient` protocol adapters. |
| `encryption.py` | Manages the Fernet symmetric encryption lifecycle and keys. |
| `utils.py` | Contains core constants (`CHUNK_SIZE`) and MD5 checksum hashing functions. |
| `pyproject.toml` / `uv.lock` / `requirements.txt` | Dependency tracking for standard and modern package managers. |
```