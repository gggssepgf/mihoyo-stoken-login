#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mihoyo-stoken-login GUI —— 米游社官方扫码登录（图形界面版）

界面流程：
    打开窗口 → 自动申请二维码并显示在窗口里 → 用米游社 App 扫码并确认
    → 自动取到 stoken / ltoken / cookie_token，显示在下方文本框里
    → 自动把 stoken 复制到剪贴板，并用状态文字 + 提示音通知你

用法：
    python stoken_login_gui.py
    python stoken_login_gui.py --config /path/to/config.yaml --write   # 成功后顺手写进配置
    python stoken_login_gui.py --png qr.png                            # 同时把二维码存成图片

只依赖标准库 tkinter（Python 自带）与 httpx/PyYAML/qrcode/pypng（见 requirements.txt）。
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional

import stoken_login as core

__version__ = core.__version__

STATUS_INFO = "#1a73e8"
STATUS_OK = "#188038"
STATUS_ERR = "#d93025"

TEXT_PROMPT = "请用【米游社 App】扫描上方二维码，并在手机上点击确认"


def format_result(result: Dict[str, Any]) -> str:
    """把结果排版成方便复制的一段文本。

    只输出两段，不重复：
      1) cookies 与各字段（cookies 就是要复制去用的那串）
      2) 可直接粘进 MihoyoBBSTools config/config.yaml 的 account 段
    注意：不要再单独列一行 cookie=…（和 account.cookie 是同一个值，重复）。
    """
    cookies = result.get("cookies") or core.build_stoken_cookie(
        str(result.get("uid", "")), str(result.get("stoken", "")),
        str(result.get("ltoken", "") or ""), str(result.get("mid", "")))
    lines = [
        "cookies      = " + cookies,
        "stuid        = " + str(result.get("uid", "")),
        "mid          = " + str(result.get("mid", "")),
        "stoken       = " + str(result.get("stoken", "")),
        "ltoken       = " + str(result.get("ltoken", "") or "(未取到)"),
        "cookie_token = " + str(result.get("cookie_token", "") or "(未取到)"),
        "",
        "# MihoyoBBSTools 的 config/config.yaml 用下面这段（account 段）",
        "account:",
        f"  cookie: {result.get('cookie', '')}",
        f"  stuid: '{result.get('uid', '')}'",
        f"  stoken: {result.get('stoken', '')}",
        f"  mid: {result.get('mid', '')}",
    ]
    return "\n".join(lines)


def load_qr_image(path: str):
    """把 PNG 读成 Tk 可显示的图片（Tk 8.6 原生支持 PNG），失败返回 None"""
    try:
        import tkinter as tk
        return tk.PhotoImage(file=path)
    except Exception:
        pass
    try:  # 退路：有 Pillow 就用 Pillow
        from PIL import Image, ImageTk
        return ImageTk.PhotoImage(Image.open(path))
    except Exception:
        return None


class App:
    def __init__(self, root: Any, config_path: str = "", auto_write: bool = False,
                 png: str = "", timeout: int = 300, interval: int = 3) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.config_path = config_path
        self.auto_write = auto_write
        self.png = png
        self.timeout = timeout
        self.interval = interval
        self.queue: "queue.Queue[Any]" = queue.Queue()
        self.qr_photo = None
        self.last_copied = ""
        self.running = False

        self.qr_target = 400  # 二维码最大边长（像素）

        root.title("米游社 stoken 获取工具")
        root.geometry("700x940")
        root.minsize(620, 820)

        # ---- 状态 ----
        self.status_var = tk.StringVar(value="准备中…")
        tk.Label(root, textvariable=self.status_var, fg=STATUS_INFO,
                 font=("Microsoft YaHei UI", 12, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 4))

        # ---- 二维码 ----
        self.qr_frame = tk.Frame(root, bg="#ffffff", width=320, height=320)
        self.qr_frame.pack(pady=6)
        self.qr_frame.pack_propagate(False)
        self.qr_label = tk.Label(self.qr_frame, text="正在申请二维码…", bg="#ffffff", fg="#5f6368")
        self.qr_label.pack(expand=True)

        self.tip_var = tk.StringVar(value=TEXT_PROMPT)
        tk.Label(root, textvariable=self.tip_var, fg="#5f6368",
                 font=("Microsoft YaHei UI", 10)).pack(pady=(0, 6))

        # ---- 二维码链接（可复制，方便在手机上直接打开）----
        link_row = tk.Frame(root)
        link_row.pack(fill="x", padx=14)
        self.url_var = tk.StringVar()
        entry = tk.Entry(link_row, textvariable=self.url_var, state="readonly", fg="#5f6368")
        entry.pack(side="left", fill="x", expand=True)
        tk.Button(link_row, text="复制链接", command=lambda: self.copy(self.url_var.get(), "二维码链接")
                  ).pack(side="left", padx=(6, 0))

        # ---- 结果 ----
        tk.Label(root, text="登录结果（可自行选中复制）", anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=14, pady=(12, 2))
        text_frame = tk.Frame(root)
        text_frame.pack(fill="both", expand=True, padx=14)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        self.text = tk.Text(text_frame, height=13, wrap="none", font=("Consolas", 10))
        yscroll = tk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        xscroll = tk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        # ---- 按钮 ----
        btns = tk.Frame(root)
        btns.pack(fill="x", padx=14, pady=10)
        self.btn_cookies = tk.Button(btns, text="复制 cookies", state="disabled",
                                     command=lambda: self.copy(self.result.get("cookies", ""), "cookies"))
        self.btn_cookies.pack(side="left")
        self.btn_all = tk.Button(btns, text="复制全部", state="disabled",
                                 command=lambda: self.copy(self.text.get("1.0", "end").strip(), "全部结果"))
        self.btn_all.pack(side="left", padx=6)
        self.btn_again = tk.Button(btns, text="重新申请二维码", command=self.start)
        self.btn_again.pack(side="left", padx=6)

        self.topmost = tk.BooleanVar(value=True)
        tk.Checkbutton(btns, text="窗口置顶", variable=self.topmost,
                       command=self._apply_topmost).pack(side="right")

        self.result: Dict[str, Any] = {}
        self._apply_topmost()
        self.root.after(100, self._drain)
        self.root.after(300, self.start)

    # ------------------------------------------------------------------ #
    def _apply_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.topmost.get()))
        except Exception:
            pass

    def _set_status(self, text: str, color: str = STATUS_INFO) -> None:
        self.status_var.set(text)

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.result = {}
        self.qr_photo = None
        self.text.delete("1.0", "end")
        self.url_var.set("")
        self.qr_label.configure(image="", text="正在申请二维码…", fg="#5f6368",
                                font=("Microsoft YaHei UI", 10))
        self._fit_frame(self.qr_target, self.qr_target)
        self.tip_var.set(TEXT_PROMPT)
        self.btn_cookies.configure(state="disabled")
        self.btn_all.configure(state="disabled")
        self._set_status("正在申请二维码…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            result = core.qr_login_flow(
                timeout=self.timeout,
                interval=self.interval,
                on_qr=lambda url, ticket: self.queue.put(("qr", (url, ticket))),
                on_status=lambda msg: self.queue.put(("status", msg)),
            )
        except Exception as exc:  # RuntimeError / TimeoutError / 网络异常
            self.queue.put(("error", str(exc)))
        else:
            self.queue.put(("done", result))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "qr":
                    self._show_qr(*payload)
                elif kind == "status":
                    self._set_status(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    # ------------------------------------------------------------------ #
    def _fit_frame(self, width: int, height: int) -> None:
        """让容器跟着图片尺寸走（Tk 不会缩放图片，框小了就会把二维码裁掉）"""
        self.qr_frame.configure(width=max(120, int(width) + 8), height=max(80, int(height) + 8))

    def _show_qr(self, url: str, ticket: str) -> None:
        self.url_var.set(url)
        path = self.png or os.path.join(tempfile.gettempdir(), "mihoyo-stoken-login-qr.png")
        if core.save_png(url, path, target_px=self.qr_target):
            photo = load_qr_image(path)
            if photo is not None:
                self.qr_photo = photo  # 必须留引用，否则被 GC
                self._fit_frame(photo.width(), photo.height())
                self.qr_label.configure(image=photo, text="")
        self._set_status("请扫码，并在手机上确认登录", STATUS_INFO)

    def _on_done(self, result: Dict[str, Any]) -> None:
        self.running = False
        self.result = result
        # 二维码用完了，收起来给结果腾地方
        self.qr_photo = None
        self.qr_label.configure(image="", text="✅ 登录成功\ncookies 已自动复制到剪贴板",
                                fg=STATUS_OK, font=("Microsoft YaHei UI", 12, "bold"))
        self._fit_frame(300, 110)
        self.tip_var.set("已登录成功，内容在下方文本框；cookies 已进剪贴板，可直接粘贴使用")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", format_result(result))
        self.btn_cookies.configure(state="normal")
        self.btn_all.configure(state="normal")

        # 自动复制 cookies + 提示
        self.copy(result.get("cookies", ""), "cookies", silent=True)
        self._set_status("✅ 登录成功，cookies 已自动复制到剪贴板", STATUS_OK)
        try:
            self.root.bell()
        except Exception:
            pass
        try:  # Windows 提示音
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass

        if self.config_path and self.auto_write:
            try:
                backup = core.write_config(self.config_path, result["uid"], result["stoken"],
                                           result["mid"], result.get("ltoken", ""),
                                           result.get("cookie_token", ""))
                self._set_status(f"✅ cookies 已复制，并写入 {self.config_path}"
                                 f"（备份 {os.path.basename(backup)}）", STATUS_OK)
            except SystemExit as exc:
                self._set_status(f"⚠️ 已复制到剪贴板，但写配置失败：{exc}", STATUS_ERR)

    def _on_error(self, message: str) -> None:
        self.running = False
        self.qr_photo = None
        self.qr_label.configure(image="", text="❌ 二维码已失效\n点下面「重新申请二维码」重试",
                                fg=STATUS_ERR, font=("Microsoft YaHei UI", 11, "bold"))
        self._fit_frame(300, 110)
        self.tip_var.set(TEXT_PROMPT)
        self._set_status(f"❌ {message}", STATUS_ERR)

    # ------------------------------------------------------------------ #
    def copy(self, value: str, what: str = "内容", silent: bool = False) -> None:
        if not value:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update()  # 让剪贴板在窗口失焦后仍然有效
        self.last_copied = value
        if not silent:
            self._set_status(f"📋 已复制{what}到剪贴板（{len(value)} 字符）", STATUS_OK)


def main(argv: Optional[list] = None) -> int:
    # PyInstaller --windowed 打包后没有控制台，sys.stdout/stderr 会是 None，
    # 那时 print/argparse 会直接抛异常。这里兜一下，避免 --version 之类的调用炸掉。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    parser = argparse.ArgumentParser(prog="stoken_login_gui",
                                     description="米游社 stoken 获取工具（图形界面）")
    parser.add_argument("--config", default="", metavar="PATH",
                        help="MihoyoBBSTools 的 config.yaml 路径（配合 --write 使用）")
    parser.add_argument("--write", action="store_true",
                        help="登录成功后自动写入 --config 指定的配置（会先备份）")
    parser.add_argument("--png", default="", metavar="PATH", help="同时把二维码保存成 PNG")
    parser.add_argument("--timeout", type=int, default=300, help="轮询等待秒数（默认 300）")
    parser.add_argument("--interval", type=int, default=3, help="轮询间隔秒数（默认 3）")
    parser.add_argument("--version", action="version", version=f"mihoyo-stoken-login-gui {__version__}")
    args = parser.parse_args(argv)

    try:
        import tkinter as tk
    except ImportError:
        print("当前 Python 没有 tkinter，请改用命令行版：python stoken_login.py")
        return 1
    try:
        root = tk.Tk()
    except Exception as exc:  # 无桌面环境
        print(f"无法创建窗口（{exc}）。无桌面环境请用命令行版：python stoken_login.py")
        return 1

    App(root, config_path=args.config, auto_write=args.write,
        png=args.png, timeout=args.timeout, interval=args.interval)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
