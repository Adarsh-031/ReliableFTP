"""
app.py — UDP File Transfer — Main GUI (tkinter)
Run: python app.py
"""
import os, sys, json, queue, threading, time, socket, select, uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Optional

# ── Protocol Imports ──────────────────────────────────────────────────────────
from utils import CHUNK_SIZE, checksum
from encryption import encrypt_data, decrypt_data

class TransferError(Exception): pass
class Cancelled(Exception): pass
def new_tid(): return uuid.uuid4().hex

# ══════════════════════════════════════════════════════════════════════════════
#  SERVER PROTOCOL ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class FileTransferServer:
    def __init__(self, files_dir, port, log_cb, transfer_cb):
        self.files_dir = files_dir
        self.port = port
        self.log_cb = log_cb
        self.transfer_cb = transfer_cb
        self._running = False
        self.main_sock = None
        self.active_clients = {}
        self.clients_lock = threading.Lock()
        os.makedirs(self.files_dir, exist_ok=True)

    def start(self):
        self._running = True
        self.main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.main_sock.bind(("0.0.0.0", self.port))
        self.main_sock.settimeout(1)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.log_cb(f"Server started on port {self.port}")

    def stop(self):
        self._running = False
        if self.main_sock:
            self.main_sock.close()
        self.log_cb("Server stopped")

    def _get_free_port(self):
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp_sock.bind(('', 0))
        port = temp_sock.getsockname()[1]
        temp_sock.close()
        return port

    def _accept_loop(self):
        while self._running:
            try:
                data, addr = self.main_sock.recvfrom(65535)
                try: message = decrypt_data(data).decode()
                except: continue

                session_id = f"{addr[0]}:{addr[1]}"

                if message.startswith("REQUEST"):
                    parts = message.split()
                    filename = parts[1]
                    resume_seq = int(parts[2]) if len(parts) > 2 else 0
                    
                    with self.clients_lock:
                        if session_id in self.active_clients: continue
                        self.active_clients[session_id] = True
                    
                    t = threading.Thread(target=self._handle_client, args=(addr, filename, resume_seq))
                    t.daemon = True
                    t.start()

                elif message.startswith("UPLOAD"):
                    parts = message.split()
                    filename, filesize = parts[1], int(parts[2])
                    
                    with self.clients_lock:
                        if session_id in self.active_clients: continue
                        self.active_clients[session_id] = True
                    
                    t = threading.Thread(target=self._handle_upload, args=(addr, filename, filesize))
                    t.daemon = True
                    t.start()

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

            except socket.timeout:
                continue
            except Exception as e:
                if self._running: self.log_cb(f"Server error: {e}", "err")

    def _handle_client(self, client_addr, filename, resume_seq=0):
        session_id = f"{client_addr[0]}:{client_addr[1]}"
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session_port = self._get_free_port()
        client_sock.bind(("0.0.0.0", session_port))
        client_sock.settimeout(30)

        try:
            filepath = os.path.join(self.files_dir, filename)
            if not os.path.exists(filepath):
                client_sock.sendto(encrypt_data(b"ERROR File not found"), client_addr)
                return

            filesize = os.path.getsize(filepath)
            response = f"OK {filesize} {session_port}"
            client_sock.sendto(encrypt_data(response.encode()), client_addr)

            try:
                ack_data, _ = client_sock.recvfrom(1024)
                if decrypt_data(ack_data).decode() != "READY": return
            except socket.timeout: return

            self.log_cb(f"Transfer {filename} -> {session_id}")
            self.transfer_cb({"tid": session_id, "event": "start", "filename": filename, "total": filesize, "sent": resume_seq * CHUNK_SIZE})

            with open(filepath, "rb") as f:
                seq = resume_seq
                if resume_seq > 0:
                    f.seek(resume_seq * CHUNK_SIZE)

                WINDOW_SIZE = 5
                unacked_packets = {}
                timeout_count = 0
                max_retries = 5
                sent_bytes = resume_seq * CHUNK_SIZE

                while self._running:
                    while len(unacked_packets) < WINDOW_SIZE:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk: break
                        
                        chunk_checksum = checksum(chunk)
                        packet = f"{seq}|{chunk_checksum}|".encode() + chunk
                        encrypted_packet = encrypt_data(packet)
                        
                        unacked_packets[seq] = encrypted_packet
                        client_sock.sendto(encrypted_packet, client_addr)
                        seq += 1

                    if not unacked_packets: break

                    ready = select.select([client_sock], [], [], 2)
                    if ready[0]:
                        try:
                            ack_data, _ = client_sock.recvfrom(1024)
                            ack = decrypt_data(ack_data).decode()
                            
                            if ack.startswith("ERROR"):
                                self.transfer_cb({"tid": session_id, "event": "error", "detail": "Client halted transfer"})
                                return
                                
                            if ack.startswith("ACK"):
                                ack_seq = int(ack.split()[1])
                                if ack_seq in unacked_packets:
                                    del unacked_packets[ack_seq]
                                    timeout_count = 0
                                    sent_bytes += CHUNK_SIZE
                                    self.transfer_cb({"tid": session_id, "event": "progress", "sent": sent_bytes})
                            elif ack.startswith("NACK"):
                                nack_seq = int(ack.split()[1])
                                if nack_seq in unacked_packets:
                                    client_sock.sendto(unacked_packets[nack_seq], client_addr)
                        except Exception: pass
                    else:
                        timeout_count += 1
                        for p_data in unacked_packets.values():
                            client_sock.sendto(p_data, client_addr)
                        
                        if timeout_count >= max_retries:
                            self.transfer_cb({"tid": session_id, "event": "error", "detail": "Max retries reached"})
                            return

            client_sock.sendto(encrypt_data(b"DONE"), client_addr)
            self.transfer_cb({"tid": session_id, "event": "done"})

        finally:
            client_sock.close()
            with self.clients_lock:
                if session_id in self.active_clients:
                    del self.active_clients[session_id]

    def _handle_upload(self, client_addr, filename, filesize):
        session_id = f"{client_addr[0]}:{client_addr[1]}"
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session_port = self._get_free_port()
        client_sock.bind(("0.0.0.0", session_port))
        client_sock.settimeout(30)

        try:
            filepath = os.path.join(self.files_dir, filename)
            response = f"OK {session_port}"
            client_sock.sendto(encrypt_data(response.encode()), client_addr)

            try:
                ready_data, _ = client_sock.recvfrom(1024)
                if decrypt_data(ready_data).decode() != "READY": return
            except socket.timeout: return

            client_sock.sendto(encrypt_data(b"GO"), client_addr)
            self.transfer_cb({"tid": session_id, "event": "start", "filename": filename, "total": filesize, "sent": 0})

            expected_seq = 0
            received_bytes = 0

            with open(filepath, "wb") as f:
                while self._running:
                    try:
                        packet, _ = client_sock.recvfrom(65535)
                    except socket.timeout: 
                        self.transfer_cb({"tid": session_id, "event": "error", "detail": "Timeout"})
                        return
                    
                    try: decrypted = decrypt_data(packet)
                    except: continue

                    if decrypted == b"DONE":
                        client_sock.sendto(encrypt_data(b"OK"), client_addr)
                        self.transfer_cb({"tid": session_id, "event": "done"})
                        return
                    
                    if decrypted.startswith(b"ERROR"):
                        self.transfer_cb({"tid": session_id, "event": "error", "detail": decrypted.decode()})
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
                            self.transfer_cb({"tid": session_id, "event": "progress", "sent": received_bytes})
                        else:
                            client_sock.sendto(encrypt_data(f"NACK {seq}".encode()), client_addr)
                    elif seq < expected_seq:
                        client_sock.sendto(encrypt_data(f"ACK {seq}".encode()), client_addr)

        finally:
            client_sock.close()
            with self.clients_lock:
                if session_id in self.active_clients:
                    del self.active_clients[session_id]

# ══════════════════════════════════════════════════════════════════════════════
#  CLIENT PROTOCOL ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class FileTransferClient:
    def __init__(self, host, port, download_dir, log_cb):
        self.host = host
        self.port = port
        self.download_dir = download_dir
        self.log_cb = log_cb
        self.connected = False
        os.makedirs(self.download_dir, exist_ok=True)

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        try:
            sock.sendto(encrypt_data(b"LIST"), (self.host, self.port))
            data, _ = sock.recvfrom(65535)
            msg = decrypt_data(data).decode()
            if msg.startswith("FILELIST"):
                self.connected = True
            else:
                raise Exception("Invalid server response")
        except Exception as e:
            raise Exception(f"Connection failed: {e}")
        finally:
            sock.close()

    def disconnect(self):
        self.connected = False

    def list_files(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        try:
            sock.sendto(encrypt_data(b"LIST"), (self.host, self.port))
            data, _ = sock.recvfrom(65535)
            msg = decrypt_data(data).decode()
            if msg.startswith("FILELIST "):
                return json.loads(msg[9:])
            return []
        finally:
            sock.close()

    def download(self, filename, prog_cb, cancel_evt, pause_evt):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        filepath = os.path.join(self.download_dir, filename)

        resume_seq = 0
        file_mode = "wb"
        if os.path.exists(filepath):
            existing_size = os.path.getsize(filepath)
            resume_seq = existing_size // CHUNK_SIZE
            file_mode = "ab"

        request = encrypt_data(f"REQUEST {filename} {resume_seq}".encode())
        sock.sendto(request, (self.host, self.port))

        try:
            data, _ = sock.recvfrom(65535)
            response = decrypt_data(data).decode()
            
            if response.startswith("ERROR"):
                raise Exception(response)

            parts = response.split()
            filesize = int(parts[1])
            session_port = int(parts[2])
            session_addr = (self.host, session_port)

            sock.sendto(encrypt_data(b"READY"), session_addr)

            expected_seq = resume_seq
            received_bytes = resume_seq * CHUNK_SIZE
            start_time = time.time()
            last_update = start_time

            with open(filepath, file_mode) as f:
                while True:
                    if cancel_evt.is_set():
                        sock.sendto(encrypt_data(b"ERROR Client cancelled"), session_addr)
                        raise Cancelled("Cancelled by user")
                    if pause_evt.is_set():
                        sock.sendto(encrypt_data(b"ERROR Client paused transfer"), session_addr)
                        raise Cancelled("Paused by user")

                    try:
                        packet, _ = sock.recvfrom(65535)
                    except socket.timeout:
                        raise Exception("Server not responding")

                    try: decrypted = decrypt_data(packet)
                    except Exception: continue

                    if decrypted == b"DONE":
                        # Compute final throughput
                        total_time = max(0.001, time.time() - start_time)
                        final_speed = (received_bytes / 1024 / 1024) / total_time
                        prog_cb(1.0, final_speed)
                        return filepath
                        
                    if decrypted.startswith(b"ERROR"):
                        raise Exception(decrypted.decode())

                    try:
                        parts = decrypted.split(b"|", 2)
                        seq, received_checksum, chunk = int(parts[0]), parts[1].decode(), parts[2]
                    except: continue

                    if seq == expected_seq:
                        if checksum(chunk) == received_checksum:
                            f.write(chunk)
                            received_bytes += len(chunk)
                            sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)
                            expected_seq += 1

                            now = time.time()
                            if now - last_update > 0.1: 
                                speed = (received_bytes / 1024 / 1024) / max(0.001, now - start_time)
                                frac = received_bytes / filesize if filesize > 0 else 1.0
                                prog_cb(frac, speed)
                                last_update = now
                        else:
                            sock.sendto(encrypt_data(f"NACK {seq}".encode()), session_addr)
                    elif seq < expected_seq:
                        sock.sendto(encrypt_data(f"ACK {seq}".encode()), session_addr)

        finally:
            sock.close()

    def upload(self, filepath, prog_cb, cancel_evt, pause_evt):
        if not os.path.exists(filepath):
            raise Exception("File not found")

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)

        sock.sendto(encrypt_data(f"UPLOAD {filename} {filesize}".encode()), (self.host, self.port))

        try:
            data, _ = sock.recvfrom(65535)
            response = decrypt_data(data).decode()
            if response.startswith("ERROR"):
                raise Exception(response)

            session_port = int(response.split()[1])
            session_addr = (self.host, session_port)

            sock.sendto(encrypt_data(b"READY"), session_addr)

            try:
                go_data, _ = sock.recvfrom(1024)
                if decrypt_data(go_data).decode() != "GO":
                    raise Exception("Handshake failed")
            except socket.timeout:
                raise Exception("Server not responding")

            with open(filepath, "rb") as f:
                seq, sent_bytes, retries, max_retries = 0, 0, 0, 5
                start_time = time.time()
                last_update = start_time

                while True:
                    if cancel_evt.is_set():
                        sock.sendto(encrypt_data(b"ERROR Cancelled"), session_addr)
                        raise Cancelled("Cancelled by user")
                    if pause_evt.is_set():
                        sock.sendto(encrypt_data(b"ERROR Client paused transfer"), session_addr)
                        raise Cancelled("Paused by user")

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
                                now = time.time()
                                if now - last_update > 0.1:
                                    speed = (sent_bytes / 1024 / 1024) / max(0.001, now - start_time)
                                    frac = sent_bytes / filesize if filesize > 0 else 1.0
                                    prog_cb(frac, speed)
                                    last_update = now
                                retries = 0
                                break
                            elif ack.startswith("NACK"):
                                retries += 1
                            elif ack.startswith("ERROR"):
                                raise Exception(ack)
                        except socket.timeout:
                            retries += 1

                    if retries >= max_retries:
                        raise Exception("Max retries reached")
                    seq += 1

            sock.sendto(encrypt_data(b"DONE"), session_addr)
            
            # Compute final throughput
            total_time = max(0.001, time.time() - start_time)
            final_speed = (sent_bytes / 1024 / 1024) / total_time
            prog_cb(1.0, final_speed)
            
            try:
                confirm_data, _ = sock.recvfrom(2000)
                if decrypt_data(confirm_data).decode() == "OK":
                    return
            except socket.timeout:
                pass 

        finally:
            sock.close()

# ── Palette / fonts ───────────────────────────────────────────────────────────
BG = "#0e1117"
SURFACE = "#161b22"
BORDER = "#30363d"
ACCENT = "#58a6ff"
SUCCESS = "#3fb950"
WARN = "#d29922"
DANGER = "#f85149"
TEXT = "#e6edf3"
MUTED = "#8b949e"
MONO = ("Consolas", 9)
SANS = ("Segoe UI", 10) if sys.platform == "win32" else ("SF Pro Text", 10)
TITLE = ("Segoe UI Semibold", 11) if sys.platform == "win32" else ("SF Pro Text", 11)

def _ts():
    return datetime.now().strftime("%H:%M:%S")

def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ── base styled frame ─────────────────────────────────────────────────────────

class Card(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(
            parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, **kw
        )


class Label(tk.Label):
    def __init__(self, parent, text="", muted=False, bold=False, **kw):
        fg = MUTED if muted else TEXT
        font = (SANS[0], SANS[1], "bold") if bold else SANS
        super().__init__(
            parent, text=text, fg=fg, bg=kw.pop("bg", SURFACE), font=font, **kw
        )


class Entry(tk.Entry):
    def __init__(self, parent, textvariable=None, width=20, **kw):
        super().__init__(
            parent,
            textvariable=textvariable,
            width=width,
            bg="#21262d",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=SANS,
            highlightbackground=BORDER,
            highlightthickness=1,
            **kw,
        )


class Btn(tk.Button):
    STYLES = {
        "primary": (ACCENT, "#1f6feb", TEXT),
        "success": (SUCCESS, "#2ea043", "#0e1117"),
        "danger": (DANGER, "#b91c1c", TEXT),
        "warn": (WARN, "#b45309", "#0e1117"),
        "ghost": (SURFACE, "#21262d", TEXT),
    }

    def __init__(self, parent, text, style="primary", command=None, **kw):
        bg, act, fg = self.STYLES.get(style, self.STYLES["primary"])
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=bg,
            activebackground=act,
            fg=fg,
            activeforeground=fg,
            relief="flat",
            cursor="hand2",
            font=SANS,
            padx=12,
            pady=5,
            **kw,
        )

    def configure(self, **kw):
        if "style" in kw:
            style = kw.pop("style")
            bg, act, fg = self.STYLES.get(style, self.STYLES["primary"])
            kw["bg"] = bg
            kw["activebackground"] = act
            kw["fg"] = fg
            kw["activeforeground"] = fg
        super().configure(**kw)

    def config(self, **kw):
        self.configure(**kw)


# ── Log widget ────────────────────────────────────────────────────────────────

class LogBox(tk.Frame):
    def __init__(self, parent, height=10, **kw):
        super().__init__(parent, bg=SURFACE, **kw)
        self._txt = tk.Text(
            self,
            bg="#0d1117",
            fg=TEXT,
            font=MONO,
            height=height,
            state="disabled",
            relief="flat",
            wrap="word",
            selectbackground=ACCENT,
            insertbackground=TEXT,
        )
        sb = ttk.Scrollbar(self, orient="vertical", command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._txt.pack(side="left", fill="both", expand=True)

    def append(self, msg: str, tag: str = "info"):
        COLOR = {
            "info": MUTED,
            "ok": SUCCESS,
            "warn": WARN,
            "err": DANGER,
            "accent": ACCENT,
        }
        fg = COLOR.get(tag, TEXT)
        self._txt.configure(state="normal")
        self._txt.tag_configure(tag, foreground=fg)
        self._txt.insert("end", f"[{_ts()}] {msg}\n", tag)
        self._txt.see("end")
        self._txt.configure(state="disabled")


# ── TransferRow — one entry in the transfer list ──────────────────────────────

class TransferRow(tk.Frame):
    def __init__(self, parent, filename, direction, on_pause, on_cancel):
        super().__init__(
            parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1
        )
        self._paused = False
        self._last_speed = 0.0

        icon = "⬇" if direction == "dl" else "⬆"
        Label(self, text=f"{icon}  {filename}", bold=True).pack(
            side="left", padx=8, pady=6
        )

        self._speed_lbl = Label(self, text="", muted=True)
        self._speed_lbl.pack(side="left", padx=4)

        self._cancel_btn = Btn(self, "✕", style="danger", command=on_cancel)
        self._cancel_btn.pack(side="right", padx=4, pady=4)

        self._pause_btn = Btn(
            self, "⏸", style="warn", command=self._toggle_pause(on_pause)
        )
        self._pause_btn.pack(side="right", padx=2, pady=4)

        self._bar_var = tk.DoubleVar(value=0.0)
        self._bar = ttk.Progressbar(
            self,
            variable=self._bar_var,
            maximum=1.0,
            length=220,
            style="Accent.Horizontal.TProgressbar",
        )
        self._bar.pack(side="right", padx=8)
        self._pct_lbl = Label(self, text="0 %", muted=True, width=5, anchor="e")
        self._pct_lbl.pack(side="right")

    def _toggle_pause(self, callback):
        def inner():
            self._paused = not self._paused
            self._pause_btn.configure(text="▶" if self._paused else "⏸")
            callback(self._paused)

        return inner

    def update_progress(self, fraction: float, speed: float):
        self._bar_var.set(fraction)
        self._pct_lbl.configure(text=f"{fraction*100:.0f} %")
        self._last_speed = speed
        self._speed_lbl.configure(text=f"{speed:.2f} MB/s")

    def mark_done(self):
        self._bar_var.set(1.0)
        self._pct_lbl.configure(text="100 %", fg=SUCCESS)
        self._speed_lbl.configure(text=f"✓ Done ({self._last_speed:.2f} MB/s)")
        self._pause_btn.configure(state="disabled")
        self._cancel_btn.configure(state="disabled")

    def mark_error(self, msg=""):
        self._pct_lbl.configure(text="ERR", fg=DANGER)
        self._speed_lbl.configure(text=msg[:30])
        self._pause_btn.configure(state="disabled")
        self._cancel_btn.configure(state="disabled")


# ══════════════════════════════════════════════════════════════════════════════
#  HOME SCREEN
# ══════════════════════════════════════════════════════════════════════════════

class HomeScreen(tk.Frame):
    def __init__(self, parent, on_server, on_client):
        super().__init__(parent, bg=BG)

        tk.Frame(self, bg=BG, height=60).pack()

        tk.Label(self, text="🔒", bg=BG, fg=ACCENT, font=("Segoe UI", 48)).pack()
        tk.Label(
            self,
            text="UDP File Transfer",
            bg=BG,
            fg=TEXT,
            font=(
                ("Segoe UI Semibold", 22)
                if sys.platform == "win32"
                else ("SF Pro Display", 22)
            ),
        ).pack()
        tk.Label(
            self, text="Reliable · Resumable · Encrypted", bg=BG, fg=MUTED, font=SANS
        ).pack(pady=(4, 40))

        card = Card(self)
        card.pack(ipadx=40, ipady=30)

        tk.Label(card, text="Choose your role", bg=SURFACE, fg=MUTED, font=SANS).pack(
            pady=(0, 16)
        )

        row = tk.Frame(card, bg=SURFACE)
        row.pack()

        server_card = tk.Frame(
            row,
            bg="#1c2128",
            highlightbackground=BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        server_card.grid(row=0, column=0, padx=12, ipadx=20, ipady=20)
        tk.Label(server_card, text="🖥", bg="#1c2128", font=("Segoe UI", 32)).pack()
        tk.Label(
            server_card,
            text="Server",
            bg="#1c2128",
            fg=TEXT,
            font=(SANS[0], 13, "bold"),
        ).pack()
        tk.Label(
            server_card, text="Host & serve files", bg="#1c2128", fg=MUTED, font=SANS
        ).pack()
        Btn(server_card, "Start Server", command=on_server).pack(pady=(12, 0))

        client_card = tk.Frame(
            row,
            bg="#1c2128",
            highlightbackground=BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        client_card.grid(row=0, column=1, padx=12, ipadx=20, ipady=20)
        tk.Label(client_card, text="💻", bg="#1c2128", font=("Segoe UI", 32)).pack()
        tk.Label(
            client_card,
            text="Client",
            bg="#1c2128",
            fg=TEXT,
            font=(SANS[0], 13, "bold"),
        ).pack()
        tk.Label(
            client_card, text="Upload & Download", bg="#1c2128", fg=MUTED, font=SANS
        ).pack()
        Btn(client_card, "Open Client", style="success", command=on_client).pack(
            pady=(12, 0)
        )

# ══════════════════════════════════════════════════════════════════════════════
#  SERVER SCREEN
# ══════════════════════════════════════════════════════════════════════════════

class ServerScreen(tk.Frame):
    def __init__(self, parent, go_home):
        super().__init__(parent, bg=BG)
        self._go_home = go_home
        self._server: Optional[FileTransferServer] = None
        self._q: queue.Queue = queue.Queue()

        self._build_ui()
        self._poll()

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=16, pady=12)
        Btn(top, "← Home", style="ghost", command=self._home).pack(side="left")
        tk.Label(top, text="Server", bg=BG, fg=TEXT, font=(SANS[0], 14, "bold")).pack(
            side="left", padx=12
        )
        self._status_lbl = tk.Label(top, text="● Stopped", bg=BG, fg=DANGER, font=MONO)
        self._status_lbl.pack(side="right")

        cfg = Card(self)
        cfg.pack(fill="x", padx=16, pady=(0, 8), ipadx=8, ipady=8)

        row1 = tk.Frame(cfg, bg=SURFACE)
        row1.pack(fill="x", padx=8, pady=4)

        Label(row1, text="Port", muted=True).grid(row=0, column=0, sticky="w", padx=4)
        self._port_var = tk.StringVar(value="9000")
        Entry(row1, textvariable=self._port_var, width=8).grid(row=0, column=1, padx=4)

        Label(row1, text="Files Dir", muted=True).grid(
            row=0, column=2, sticky="w", padx=12
        )
        self._dir_var = tk.StringVar(value="./server_files")
        Entry(row1, textvariable=self._dir_var, width=24).grid(row=0, column=3, padx=4)
        Btn(row1, "Browse", style="ghost", command=self._browse_dir).grid(
            row=0, column=4, padx=4
        )

        self._start_btn = Btn(
            cfg, "▶  Start Server", style="success", command=self._toggle_server
        )
        self._start_btn.pack(pady=6)

        xfr_card = Card(self)
        xfr_card.pack(fill="x", padx=16, pady=(0, 8), ipadx=8, ipady=4)
        Label(xfr_card, text="Active Transfers", bold=True).pack(
            anchor="w", padx=8, pady=(4, 2)
        )
        self._xfr_frame = tk.Frame(xfr_card, bg=SURFACE)
        self._xfr_frame.pack(fill="x", padx=8)
        self._xfr_rows = {}

        Label(xfr_card, text="No active transfers", muted=True).pack(
            anchor="w", padx=8, pady=4
        )
        self._no_xfr_lbl = xfr_card.winfo_children()[-1]

        log_card = Card(self)
        log_card.pack(fill="both", expand=True, padx=16, pady=(0, 12), ipadx=6, ipady=6)
        Label(log_card, text="Server Log", bold=True).pack(
            anchor="w", padx=8, pady=(4, 2)
        )
        self._log = LogBox(log_card, height=14)
        self._log.pack(fill="both", expand=True, padx=6)

    def _toggle_server(self):
        if self._server and self._server._running:
            self._server.stop()
            self._server = None
            self._start_btn.configure(text="▶  Start Server", style="success")
            self._status_lbl.configure(text="● Stopped", fg=DANGER)
        else:
            self._start_server()

    def _start_server(self):
        try:
            port = int(self._port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be an integer")
            return
        self._server = FileTransferServer(
            files_dir=self._dir_var.get(),
            port=port,
            log_cb=lambda m, tag="info": self._q.put(("log", m, tag)),
            transfer_cb=self._on_transfer,
        )
        self._server.start()
        self._start_btn.configure(text="⏹  Stop Server", style="danger")
        self._status_lbl.configure(text="● Running", fg=SUCCESS)

    def _on_transfer(self, info: dict):
        self._q.put(("transfer", info))

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == "log":
                    _, msg, tag = item
                    self._log.append(msg, tag)
                elif item[0] == "transfer":
                    self._handle_transfer_event(item[1])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _handle_transfer_event(self, info: dict):
        tid = info["tid"]
        evt = info.get("event", "")
        fn = info.get("filename", "?")
        tot = info.get("total", 1)
        sent = info.get("sent", 0)

        if evt == "start":
            self._no_xfr_lbl.pack_forget()
            row = TransferRow(
                self._xfr_frame,
                fn,
                "dl" if "download" in fn else "ul", 
                on_pause=lambda p: None,
                on_cancel=lambda: None,
            )
            row.pack(fill="x", pady=2)
            self._xfr_rows[tid] = {"row": row, "tot": tot, "start_time": time.time()}

        elif evt == "progress":
            if tid in self._xfr_rows:
                tot = self._xfr_rows[tid]["tot"]
                start_t = self._xfr_rows[tid].get("start_time", time.time())
                elapsed = max(0.001, time.time() - start_t)
                frac = sent / tot if tot else 0
                speed = (sent / 1024 / 1024) / elapsed
                self._xfr_rows[tid]["row"].update_progress(frac, speed)

        elif evt in ("done", "cancel", "error"):
            if tid in self._xfr_rows:
                row = self._xfr_rows[tid]["row"]
                if evt == "done":
                    tot = self._xfr_rows[tid]["tot"]
                    start_t = self._xfr_rows[tid].get("start_time", time.time())
                    elapsed = max(0.001, time.time() - start_t)
                    final_speed = (tot / 1024 / 1024) / elapsed
                    row.update_progress(1.0, final_speed)
                    row.mark_done()
                    # Pop from tracking dictionary to free memory, but DO NOT destroy UI element
                    self._xfr_rows.pop(tid)
                else:
                    row.mark_error(info.get("detail", evt))
                    self._xfr_rows.pop(tid)
                    # Automatically remove canceled or errored rows after a delay
                    self.after(4000, row.destroy)
            
            # Repack the "No active transfers" label if all child components (rows) are cleared
            if len(self._xfr_frame.winfo_children()) == 0:
                self._no_xfr_lbl.pack(anchor="w", padx=8, pady=4)

    def _home(self):
        if self._server and self._server._running:
            if not messagebox.askyesno(
                "Stop server?", "Server is running. Stop and go home?"
            ):
                return
            self._server.stop()
            self._server = None
        self._go_home()

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self._dir_var.set(d)


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENT SCREEN
# ══════════════════════════════════════════════════════════════════════════════

class ClientScreen(tk.Frame):
    def __init__(self, parent, go_home):
        super().__init__(parent, bg=BG)
        self._go_home = go_home
        self._client: Optional[FileTransferClient] = None
        self._q: queue.Queue = queue.Queue()
        self._active_transfers = {}

        self._build_ui()
        self._poll()

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=16, pady=12)
        Btn(top, "← Home", style="ghost", command=self._home).pack(side="left")
        tk.Label(top, text="Client", bg=BG, fg=TEXT, font=(SANS[0], 14, "bold")).pack(
            side="left", padx=12
        )
        self._conn_lbl = tk.Label(
            top, text="● Disconnected", bg=BG, fg=DANGER, font=MONO
        )
        self._conn_lbl.pack(side="right")

        conn_card = Card(self)
        conn_card.pack(fill="x", padx=16, pady=(0, 8), ipadx=8, ipady=8)

        row = tk.Frame(conn_card, bg=SURFACE)
        row.pack(fill="x", padx=8)

        Label(row, text="Host").grid(row=0, column=0, padx=4, sticky="w")
        self._host_var = tk.StringVar(value="127.0.0.1")
        Entry(row, textvariable=self._host_var, width=16).grid(row=0, column=1, padx=4)

        Label(row, text="Port").grid(row=0, column=2, padx=8, sticky="w")
        self._port_var = tk.StringVar(value="9000")
        Entry(row, textvariable=self._port_var, width=7).grid(row=0, column=3, padx=4)

        Label(row, text="DL Dir").grid(row=0, column=4, padx=8, sticky="w")
        self._dldir_var = tk.StringVar(value="./downloads")
        Entry(row, textvariable=self._dldir_var, width=20).grid(row=0, column=5, padx=4)
        Btn(row, "…", style="ghost", width=3, command=self._browse_dldir).grid(
            row=0, column=6, padx=2
        )

        self._conn_btn = Btn(conn_card, "⚡  Connect", command=self._toggle_connect)
        self._conn_btn.pack(pady=(6, 2))

        nb_frame = tk.Frame(self, bg=BG)
        nb_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=SURFACE,
            foreground=MUTED,
            padding=[16, 8],
            font=SANS,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=BORDER,
            background=ACCENT,
            thickness=6,
        )

        self._nb = ttk.Notebook(nb_frame)
        self._nb.pack(fill="both", expand=True)

        dl_frame = tk.Frame(self._nb, bg=BG)
        ul_frame = tk.Frame(self._nb, bg=BG)
        self._nb.add(dl_frame, text="  ⬇  Download  ")
        self._nb.add(ul_frame, text="  ⬆  Upload  ")

        self._build_download_tab(dl_frame)
        self._build_upload_tab(ul_frame)

    def _build_download_tab(self, parent):
        list_card = Card(parent)
        list_card.pack(fill="x", padx=0, pady=(8, 4), ipadx=6, ipady=6)

        hdr = tk.Frame(list_card, bg=SURFACE)
        hdr.pack(fill="x", padx=8, pady=(4, 6))
        Label(hdr, text="Server Files", bold=True).pack(side="left")
        self._refresh_btn = Btn(
            hdr, "↺  Refresh", style="ghost", command=self._refresh_files
        )
        self._refresh_btn.pack(side="right")

        cols = ("name", "size", "modified")
        self._file_tree = ttk.Treeview(
            list_card, columns=cols, show="headings", height=8, selectmode="browse"
        )
        style = ttk.Style()
        style.configure(
            "Treeview",
            background="#161b22",
            foreground=TEXT,
            fieldbackground="#161b22",
            rowheight=26,
            borderwidth=0,
            font=SANS,
        )
        style.configure(
            "Treeview.Heading",
            background=SURFACE,
            foreground=MUTED,
            relief="flat",
            font=SANS,
        )
        style.map(
            "Treeview",
            background=[("selected", "#1f6feb")],
            foreground=[("selected", TEXT)],
        )

        for col, w, anchor in [
            ("name", 240, "w"),
            ("size", 90, "e"),
            ("modified", 160, "w"),
        ]:
            self._file_tree.heading(col, text=col.capitalize())
            self._file_tree.column(col, width=w, anchor=anchor)

        sb = ttk.Scrollbar(list_card, orient="vertical", command=self._file_tree.yview)
        self._file_tree.configure(yscrollcommand=sb.set)
        self._file_tree.pack(side="left", fill="both", expand=True, padx=(8, 0))
        sb.pack(side="left", fill="y")

        Btn(list_card, "⬇  Download Selected", command=self._start_download).pack(
            anchor="e", padx=8, pady=6
        )

        act_card = Card(parent)
        act_card.pack(fill="both", expand=True, padx=0, pady=(4, 0), ipadx=6, ipady=6)
        Label(act_card, text="Active Downloads", bold=True).pack(
            anchor="w", padx=8, pady=(4, 2)
        )
        self._dl_rows_frame = tk.Frame(act_card, bg=SURFACE)
        self._dl_rows_frame.pack(fill="x", padx=8)
        Label(act_card, text="No active downloads", muted=True).pack(
            anchor="w", padx=8, pady=4
        )
        self._no_dl_lbl = act_card.winfo_children()[-1]

        log_card = Card(parent)
        log_card.pack(fill="x", padx=0, pady=(4, 0), ipadx=6, ipady=4)
        self._dl_log = LogBox(log_card, height=6)
        self._dl_log.pack(fill="x", padx=6)

    def _build_upload_tab(self, parent):
        pick_card = Card(parent)
        pick_card.pack(fill="x", padx=0, pady=(8, 4), ipadx=8, ipady=8)

        Label(pick_card, text="Upload Files", bold=True).pack(
            anchor="w", padx=8, pady=(4, 8)
        )

        row = tk.Frame(pick_card, bg=SURFACE)
        row.pack(fill="x", padx=8)
        Label(row, text="File:", muted=True).pack(side="left")
        self._ul_path_var = tk.StringVar(value="")
        Entry(row, textvariable=self._ul_path_var, width=38).pack(side="left", padx=6)
        Btn(row, "Browse…", style="ghost", command=self._browse_upload).pack(
            side="left"
        )

        Btn(
            pick_card, "⬆  Upload File", style="success", command=self._start_upload
        ).pack(anchor="e", padx=8, pady=8)

        act_card = Card(parent)
        act_card.pack(fill="both", expand=True, padx=0, pady=(4, 0), ipadx=6, ipady=6)
        Label(act_card, text="Active Uploads", bold=True).pack(
            anchor="w", padx=8, pady=(4, 2)
        )
        self._ul_rows_frame = tk.Frame(act_card, bg=SURFACE)
        self._ul_rows_frame.pack(fill="x", padx=8)
        Label(act_card, text="No active uploads", muted=True).pack(
            anchor="w", padx=8, pady=4
        )
        self._no_ul_lbl = act_card.winfo_children()[-1]

        log_card = Card(parent)
        log_card.pack(fill="x", padx=0, pady=(4, 0), ipadx=6, ipady=4)
        self._ul_log = LogBox(log_card, height=6)
        self._ul_log.pack(fill="x", padx=6)

    def _toggle_connect(self):
        if self._client and self._client.connected:
            self._client.disconnect()
            self._client = None
            self._conn_btn.configure(text="⚡  Connect")
            self._conn_lbl.configure(text="● Disconnected", fg=DANGER)
        else:
            self._do_connect()

    def _do_connect(self):
        try:
            port = int(self._port_var.get())
        except ValueError:
            messagebox.showerror("Bad port", "Port must be an integer")
            return
        self._client = FileTransferClient(
            host=self._host_var.get(),
            port=port,
            download_dir=self._dldir_var.get(),
            log_cb=lambda m, tag="info": self._q.put(("log_both", m, tag)),
        )
        threading.Thread(target=self._connect_thread, daemon=True).start()

    def _connect_thread(self):
        try:
            self._client.connect()
            self._q.put(("connected",))
        except Exception as exc:
            self._q.put(("log_both", f"Connect failed: {exc}", "err"))
            self._client = None

    def _refresh_files(self):
        if not self._client or not self._client.connected:
            messagebox.showwarning("Not connected", "Connect to server first")
            return
        threading.Thread(target=self._list_thread, daemon=True).start()

    def _list_thread(self):
        try:
            files = self._client.list_files()
            self._q.put(("file_list", files))
        except Exception as exc:
            self._q.put(("log_dl", f"List error: {exc}", "err"))

    def _start_download(self):
        sel = self._file_tree.selection()
        if not sel:
            messagebox.showinfo("Select a file", "Click a file first")
            return
        if not self._client or not self._client.connected:
            messagebox.showwarning("Not connected", "Connect first")
            return
        values = self._file_tree.item(sel[0], "values")
        filename = values[0]
        self._launch_transfer("dl", filename)

    def _launch_transfer(self, direction: str, filename: str):
        tid = new_tid()
        cancel_evt = threading.Event()
        pause_evt = threading.Event()

        rows_frame = self._dl_rows_frame if direction == "dl" else self._ul_rows_frame
        no_lbl = self._no_dl_lbl if direction == "dl" else self._no_ul_lbl
        no_lbl.pack_forget()

        row = TransferRow(
            rows_frame,
            filename,
            direction,
            on_pause=lambda paused: pause_evt.set() if paused else pause_evt.clear(),
            on_cancel=lambda: cancel_evt.set(),
        )
        row.pack(fill="x", pady=2)

        self._active_transfers[tid] = {
            "cancel": cancel_evt,
            "pause": pause_evt,
            "row": row,
            "direction": direction,
            "filename": filename,
        }

        def prog_cb(frac, speed):
            self._q.put(("progress", tid, frac, speed))

        def log_cb_dl(msg, tag="info"):
            self._q.put(("log_dl", msg, tag))

        def log_cb_ul(msg, tag="info"):
            self._q.put(("log_ul", msg, tag))

        if direction == "dl":
            t = threading.Thread(
                target=self._download_thread,
                args=(tid, filename, prog_cb, cancel_evt, pause_evt, log_cb_dl),
                daemon=True,
            )
        else:
            t = threading.Thread(
                target=self._upload_thread,
                args=(tid, filename, prog_cb, cancel_evt, pause_evt, log_cb_ul),
                daemon=True,
            )

        self._active_transfers[tid]["thread"] = t
        t.start()

    def _download_thread(self, tid, filename, prog_cb, cancel_evt, pause_evt, log_cb):
        try:
            if not self._client.connected:
                self._client.connect()
            path = self._client.download(filename, prog_cb, cancel_evt, pause_evt)
            self._q.put(("xfr_done", tid, f"Saved to {path}"))
        except Cancelled:
            paused = pause_evt.is_set()
            self._q.put(("xfr_paused" if paused else "xfr_cancel", tid))
            if paused:
                log_cb(f"'{filename}' paused — state saved", "warn")
            else:
                log_cb(f"'{filename}' cancelled", "warn")
        except Exception as exc:
            self._q.put(("xfr_error", tid, str(exc)))
            log_cb(f"Download error: {exc}", "err")

    def _upload_thread(self, tid, filepath, prog_cb, cancel_evt, pause_evt, log_cb):
        try:
            if not self._client.connected:
                self._client.connect()
            self._client.upload(filepath, prog_cb, cancel_evt, pause_evt)
            self._q.put(("xfr_done", tid, "Upload complete"))
        except Cancelled:
            paused = pause_evt.is_set()
            self._q.put(("xfr_paused" if paused else "xfr_cancel", tid))
            fn = os.path.basename(filepath)
            if paused:
                log_cb(f"'{fn}' paused — state saved", "warn")
            else:
                log_cb(f"'{fn}' cancelled", "warn")
        except Exception as exc:
            self._q.put(("xfr_error", tid, str(exc)))
            log_cb(f"Upload error: {exc}", "err")

    def _start_upload(self):
        path = self._ul_path_var.get().strip()
        if not path:
            messagebox.showinfo("No file", "Browse a file to upload")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Not found", f"File not found:\n{path}")
            return
        if not self._client or not self._client.connected:
            messagebox.showwarning("Not connected", "Connect first")
            return
        self._launch_transfer("ul", path)

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                tag = item[0]

                if tag == "connected":
                    self._conn_btn.configure(text="⏏  Disconnect")
                    self._conn_lbl.configure(text="● Connected", fg=SUCCESS)
                    self._dl_log.append("Connected to server", "ok")
                    self._ul_log.append("Connected to server", "ok")

                elif tag == "log_both":
                    _, msg, ltag = item
                    self._dl_log.append(msg, ltag)
                    self._ul_log.append(msg, ltag)

                elif tag == "log_dl":
                    _, msg, ltag = item
                    self._dl_log.append(msg, ltag)

                elif tag == "log_ul":
                    _, msg, ltag = item
                    self._ul_log.append(msg, ltag)

                elif tag == "file_list":
                    _, files = item
                    for child in self._file_tree.get_children():
                        self._file_tree.delete(child)
                    for f in files:
                        mod = datetime.fromtimestamp(f["modified"]).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        self._file_tree.insert(
                            "", "end", values=(f["name"], fmt_size(f["size"]), mod)
                        )
                    self._dl_log.append(f"Listed {len(files)} file(s)", "ok")

                elif tag == "progress":
                    _, tid, frac, speed = item
                    if tid in self._active_transfers:
                        self._active_transfers[tid]["row"].update_progress(frac, speed)

                elif tag == "xfr_done":
                    _, tid, msg = item
                    if tid in self._active_transfers:
                        self._active_transfers[tid]["row"].mark_done()
                        d = self._active_transfers[tid]["direction"]
                        (self._dl_log if d == "dl" else self._ul_log).append(msg, "ok")
                        # Pop item to stop memory leak tracking, but do NOT execute _cleanup_row which destroys the UI component.
                        self._active_transfers.pop(tid, None)

                elif tag in ("xfr_cancel", "xfr_paused"):
                    if item[1] in self._active_transfers:
                        d = self._active_transfers[item[1]]["direction"]
                        txt = "Paused" if tag == "xfr_paused" else "Cancelled"
                        self._active_transfers[item[1]]["row"].mark_error(txt)
                        (self._dl_log if d == "dl" else self._ul_log).append(
                            txt, "warn"
                        )
                        self._cleanup_row(item[1], delay=3000)

                elif tag == "xfr_error":
                    _, tid, msg = item
                    if tid in self._active_transfers:
                        d = self._active_transfers[tid]["direction"]
                        self._active_transfers[tid]["row"].mark_error(msg)
                        (self._dl_log if d == "dl" else self._ul_log).append(
                            f"Error: {msg}", "err"
                        )
                        self._cleanup_row(tid)

        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _cleanup_row(self, tid, delay=5000):
        if tid not in self._active_transfers:
            return
        row = self._active_transfers[tid]["row"]
        direction = self._active_transfers[tid]["direction"]
        self._active_transfers.pop(tid, None)

        def remove():
            try:
                row.destroy()
            except:
                pass
            rows_frame = (
                self._dl_rows_frame if direction == "dl" else self._ul_rows_frame
            )
            no_lbl = self._no_dl_lbl if direction == "dl" else self._no_ul_lbl
            if not rows_frame.winfo_children():
                no_lbl.pack(anchor="w", padx=8, pady=4)

        self.after(delay, remove)

    def _home(self):
        if self._client and self._client.connected:
            if not messagebox.askyesno("Disconnect?", "Disconnect and go home?"):
                return
            self._client.disconnect()
        self._go_home()

    def _browse_dldir(self):
        d = filedialog.askdirectory()
        if d:
            self._dldir_var.set(d)

    def _browse_upload(self):
        f = filedialog.askopenfilename()
        if f:
            self._ul_path_var.set(f)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UDP File Transfer")
        self.configure(bg=BG)
        self.geometry("860x680")
        self.minsize(760, 580)
        self.resizable(True, True)

        try:
            self.iconbitmap("icon.ico")
        except:
            pass

        self._current: Optional[tk.Frame] = None
        self._show_home()

    def _clear(self):
        if self._current:
            self._current.pack_forget()
            self._current.destroy()
            self._current = None

    def _show_home(self):
        self._clear()
        self._current = HomeScreen(
            self,
            on_server=self._show_server,
            on_client=self._show_client,
        )
        self._current.pack(fill="both", expand=True)

    def _show_server(self):
        self._clear()
        self._current = ServerScreen(self, go_home=self._show_home)
        self._current.pack(fill="both", expand=True)

    def _show_client(self):
        self._clear()
        self._current = ClientScreen(self, go_home=self._show_home)
        self._current.pack(fill="both", expand=True)


if __name__ == "__main__":
    app = App()
    app.mainloop()