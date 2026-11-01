"""Local-only native supervisor for LAN pairing; no HTTP approval endpoint."""

import base64
import gc
import io
import socket
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlencode
import webbrowser

ONLINE_URL = "https://zzzccchhh.github.io/TCP2COM/"


def pairing_link(address, invite="", online=True):
    # Fragment stays in the browser; no LAN address is sent to the static host.
    return (ONLINE_URL if online else address.rstrip('/') + '/') + "#" + urlencode({"service": address, "invite": invite})


def service_addresses(host, port):
    if host == "0.0.0.0":
        try:
            addresses = sorted({row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                                if not row[4][0].startswith("127.")})
        except OSError:
            addresses = []
        addresses.append("127.0.0.1")
    else:
        addresses = [host]
    return [f"http://{address}:{port}/" for address in addresses]


class Manager:
    def __init__(self, root, server, adapter):
        import qrcode  # Installation errors are reported by the launcher.
        self.qrcode = qrcode
        self.root, self.server, self.adapter = root, server, adapter
        self.closed = False
        self.previous = None
        self.online_link = tk.StringVar()
        root.title("Wi-Fi Bridge · 局域网服务管理")
        root.geometry(f"1080x{min(900, max(640, root.winfo_screenheight() - 100))}")
        root.minsize(860, 600)
        root.configure(bg="#f1f0ea")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f1f0ea")
        style.configure("TLabel", background="#f1f0ea", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 10))
        canvas = tk.Canvas(root, background="#f1f0ea", highlightthickness=0)
        scroll = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        box = ttk.Frame(canvas, padding=24)
        self.body = box
        window = canvas.create_window(0, 0, window=box, anchor="nw")
        box.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        root.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units") if not isinstance(event.widget, (ttk.Treeview, ttk.Combobox)) else None)
        ttk.Label(box, text="Wi-Fi Bridge · 服务管理", style="Title.TLabel").pack(anchor="w")
        self.status = tk.StringVar(value="服务运行中 · 等待网页申请配对")
        ttk.Label(box, textvariable=self.status).pack(anchor="w", pady=(6, 16))
        self.notice = tk.StringVar(value="打开网页 / 复制网址 / 扫码均自动连接，无需请求或允许；不会自动启动串口或 USB。")
        self.address_row(box, "在线工作台", self.online_link)
        addresses = service_addresses(*server.server_address)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="局域网地址", width=12).pack(side="left")
        self.address = tk.StringVar(value=addresses[0])
        self.selector = ttk.Combobox(row, textvariable=self.address, values=addresses, state="readonly", font=("Consolas", 11))
        self.selector.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="复制地址", command=lambda: self.copy(self.link.get())).pack(side="left", padx=3)
        ttk.Button(row, text="本地工作台", command=lambda: webbrowser.open(self.link.get())).pack(side="left", padx=3)
        sharing = ttk.Frame(box)
        sharing.pack(fill="x", pady=8)
        self.qr = ttk.Label(sharing)
        self.qr.pack(side="right", padx=(20, 0))
        info = ttk.Frame(sharing)
        info.pack(side="left", fill="both", expand=True)
        ttk.Label(info, text="扫码直接连接局域网工作台", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=4)
        ttk.Label(info, text="手机扫码后自动连接当前服务，不依赖外网。\n多网卡时请选择手机可访问的地址。\n127.0.0.1 仅供这台电脑使用。", wraplength=650).pack(anchor="w")
        self.link = tk.StringVar()
        ttk.Entry(info, textvariable=self.link, state="readonly").pack(fill="x", pady=8)
        buttons = ttk.Frame(info)
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="复制扫码连接链接", command=lambda: self.copy(self.link.get())).pack(side="left")
        ttk.Button(buttons, text="刷新连接链接 / 二维码", command=self.rotate_links).pack(side="left", padx=8)
        self.selector.bind("<<ComboboxSelected>>", lambda event: self.update_qr())
        self.update_qr()
        ttk.Label(box, textvariable=self.notice, wraplength=950).pack(anchor="w", pady=(3, 12))
        self.legacy_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="显示旧版网页的兼容审批（新链接无需审批）", variable=self.legacy_visible, command=self.toggle_legacy).pack(anchor="w", pady=6)
        self.legacy = ttk.Frame(box)
        self.pending = self.tree(self.legacy, ("编号", "终端名称", "来源 IP", "申请时间"), 3)
        actions = ttk.Frame(self.legacy)
        actions.pack(fill="x", pady=6)
        ttk.Button(actions, text="允许配对", command=lambda: self.decide(True)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="拒绝申请", command=lambda: self.decide(False)).pack(side="left")
        self.peers_heading = ttk.Label(box, text="已连接终端", font=("Microsoft YaHei UI", 11, "bold"))
        self.peers_heading.pack(anchor="w", pady=(8, 0))
        self.peers = self.tree(box, ("编号", "终端名称", "来源 IP"), 3)
        ttk.Button(box, text="撤销选中终端", command=self.revoke).pack(anchor="w", pady=8)
        ttk.Label(box, text="关闭窗口停止服务。重启后请重新打开连接链接；刷新链接不影响已连接终端。", wraplength=950).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def address_row(self, parent, title, value):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text=title, width=12).pack(side="left")
        field = ttk.Entry(row, textvariable=value, font=("Consolas", 11), state="readonly")
        field.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="复制网址", command=lambda: self.copy(value.get())).pack(side="left", padx=3)
        ttk.Button(row, text="打开网页", command=lambda: webbrowser.open(value.get())).pack(side="left", padx=3)

    def tree(self, parent, columns, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=6)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode="browse")
        for name in columns:
            tree.heading(name, text=name)
            tree.column(name, width=160, minwidth=80, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def update_qr(self):
        self.link.set(pairing_link(self.address.get(), self.adapter.invite, online=False))
        self.online_link.set(pairing_link(self.address.get(), self.adapter.invite))
        qr = self.qrcode.QRCode(box_size=4, border=4)
        qr.add_data(self.link.get())
        qr.make(fit=True)
        buffer = io.BytesIO()
        qr.make_image(fill_color="#282c25", back_color="#ffffff").save(buffer, format="PNG")
        self.photo = tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))
        self.qr.configure(image=self.photo)

    def rotate_links(self):
        self.adapter.rotate_invite()
        self.update_qr()
        self.notice.set("连接链接和二维码已更新；旧链接失效，已连接终端不受影响。")

    def toggle_legacy(self):
        if self.legacy_visible.get():
            self.legacy.pack(fill="x", before=self.peers_heading)
        else:
            self.legacy.pack_forget()

    def copy(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.notice.set("已复制：" + value)

    def decide(self, allow):
        selected = self.pending.selection()
        if not selected:
            self.notice.set("请先选择一条待审批申请")
            return
        try:
            self.adapter.decide(selected[0], allow)
            self.notice.set("已允许配对，等待网页自动连接" if allow else "已拒绝申请")
        except ValueError as exc:
            self.notice.set(str(exc))
        self.refresh(schedule=False)

    def revoke(self):
        selected = self.peers.selection()
        if selected:
            self.adapter.revoke_client(selected[0])
            self.notice.set("已撤销终端配对")
            self.refresh(schedule=False)
        else:
            self.notice.set("请先选择一个已配对终端")

    def refresh(self, schedule=True):
        if self.closed:
            return
        pending, peers = self.adapter.management_snapshot()
        snapshot = (pending, peers)
        if snapshot != self.previous:
            for tree, items, columns in ((self.pending, pending, ("id", "name", "address", "created")), (self.peers, peers, ("id", "name", "address"))):
                selected = tree.selection()
                tree.delete(*tree.get_children())
                for item in items:
                    tree.insert("", "end", iid=item["id"], values=[str(item[c]).replace("\n", " ").replace("\r", " ") for c in columns])
                if selected and tree.exists(selected[0]):
                    tree.selection_set(selected)
            self.previous = snapshot
        self.status.set(f"服务运行中 · 端口 {self.server.server_port} · 自动接入已启用 · 已连接 {len(peers)}" + (f" · 旧版申请 {len(pending)}" if pending else ""))
        if schedule:
            self.root.after(500, self.refresh)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.status.set("正在停止服务并释放设备，请稍候…")
        done = threading.Event()
        def stop():
            try:
                self.server.shutdown()
                self.adapter.service._shutdown()
            finally:
                done.set()
        threading.Thread(target=stop, daemon=True).start()
        def check():
            if done.is_set():
                self.root.destroy()
            else:
                self.root.after(100, check)
        check()


def run_manager(server, adapter):
    root = tk.Tk()
    try:
        Manager(root, server, adapter)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        root.mainloop()
    finally:
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass  # Normal after the window's close handler destroys Tk.
        gc.collect()  # Release Tk callback cycles on the owning thread.


if __name__ == "__main__":
    try:
        from lan_service import main
        main()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Wi-Fi Bridge 服务启动失败", f"{exc}\n\n首次使用请运行：\npy -3 -m pip install -r requirements-lan.txt\n\n也可运行启动 BAT 查看诊断信息。", parent=root)
        root.destroy()
        raise SystemExit(1)
