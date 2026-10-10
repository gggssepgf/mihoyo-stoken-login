# -*- coding: utf-8 -*-
"""GUI 相关测试。

有桌面环境时会真正创建 Tk 窗口跑一遍核心交互（无网络）；
CI / 无桌面环境时自动跳过需要窗口的用例，纯逻辑用例仍然会跑。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stoken_login as core  # noqa: E402
import stoken_login_gui as gui  # noqa: E402


RESULT = {
    "uid": "6061062",
    "mid": "0px3aha5ri_mhy",
    "stoken": "v2_FAKE_STOKEN_CAE=",
    "ltoken": "LT_FAKE",
    "cookie_token": "CT_FAKE",
    "cookies": "stuid=6061062;stoken=v2_FAKE_STOKEN_CAE=;ltoken=LT_FAKE;mid=0px3aha5ri_mhy;",
    "cookie": "ltuid=6061062; ltoken=LT_FAKE; cookie_token=CT_FAKE; account_id=6061062;",
}


# --------------------------------------------------------------------------- #
# 纯逻辑（不需要窗口）
# --------------------------------------------------------------------------- #
def test_format_result_contains_all_fields():
    text = gui.format_result(RESULT)
    for key in ("cookies", "stuid", "mid", "stoken", "ltoken", "cookie_token", "cookie"):
        assert key in text
    assert RESULT["stoken"] in text
    assert RESULT["cookies"] in text
    assert RESULT["cookie"] in text


def test_format_result_has_no_redundant_lines():
    """回归：单独那行 cookie=… 与 account.cookie 重复，必须去掉"""
    text = gui.format_result(RESULT)
    assert "cookie       =" not in text, "多了一行与 account.cookie 重复的 cookie="
    # cookies 那行应该在最前面
    assert text.splitlines()[0].startswith("cookies      = ")
    # 每个字段只出现一次作为独立行
    assert sum(1 for l in text.splitlines() if l.startswith("stoken       = ")) == 1


def test_format_result_keeps_the_hint_comment():
    """那行说明注释是用户要求保留的"""
    text = gui.format_result(RESULT)
    assert "# MihoyoBBSTools 的 config/config.yaml 用下面这段（account 段）" in text
    lines = [l for l in text.splitlines() if l.startswith("#")]
    assert len(lines) == 1, "只应该有一行注释"


def test_format_result_builds_cookies_when_missing():
    """老结果里没有 cookies 字段时也要能拼出来"""
    text = gui.format_result({"uid": "1", "mid": "m", "stoken": "s", "ltoken": "l"})
    assert "cookies      = stuid=1;stoken=s;ltoken=l;mid=m;" in text


def test_format_result_paste_block_is_valid_yaml():
    import yaml
    text = gui.format_result(RESULT)
    block = text[text.index("account:"):]
    data = yaml.safe_load(block)
    assert data["account"]["stuid"] == RESULT["uid"]
    assert data["account"]["stoken"] == RESULT["stoken"]
    assert data["account"]["mid"] == RESULT["mid"]
    assert data["account"]["cookie"] == RESULT["cookie"]


def test_format_result_handles_missing_tokens():
    text = gui.format_result({"uid": "1", "mid": "m", "stoken": "s", "cookie": "c"})
    assert "(未取到)" in text


def test_qr_login_flow_callbacks(monkeypatch):
    """GUI 依赖的回调顺序：on_qr -> on_status -> on_token"""
    events = []
    monkeypatch.setattr(core, "create_qr",
                        lambda timeout=20.0: {"retcode": 0, "data": {"url": "https://x/qr?ticket=T1",
                                                                     "ticket": "T1"}})
    monkeypatch.setattr(core, "query_qr",
                        lambda ticket, timeout=20.0: {"retcode": 0,
                                                      "data": {"status": "Confirmed",
                                                               "user_info": {"aid": 6061062, "mid": "m_mhy"},
                                                               "tokens": [{"name": "stoken_v2",
                                                                           "token": "ST"}]}})
    monkeypatch.setattr(core, "ltoken_by_stoken", lambda *a, **k: "LT")
    monkeypatch.setattr(core, "cookie_token_by_stoken", lambda *a, **k: "CT")
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)

    result = core.qr_login_flow(
        interval=0,
        on_qr=lambda url, ticket: events.append(("qr", url, ticket)),
        on_status=lambda msg: events.append(("status", msg)),
        on_token=lambda res: events.append(("token", res["stoken"])),
    )
    kinds = [e[0] for e in events]
    assert kinds[0] == "status"          # 正在申请二维码…
    assert "qr" in kinds
    assert kinds[-1] == "token"
    assert events[kinds.index("qr")][1] == "https://x/qr?ticket=T1"
    assert result["stoken"] == "ST"
    assert result["cookie"] == "ltuid=6061062; ltoken=LT; cookie_token=CT; account_id=6061062;"


def test_poll_qr_reports_risk_message(monkeypatch):
    """passport 返回 -3503 时，状态回调要把原文带出来"""
    seen = []
    monkeypatch.setattr(core, "query_qr",
                        lambda ticket, timeout=20.0: {"retcode": -3503,
                                                      "message": "请求失败，当前设备或网络环境存在风险"})
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)
    clock = {"t": 0.0}

    def fake_time():  # 时钟要小步推进，保证轮询循环真的跑起来再超时
        clock["t"] += 0.1
        return clock["t"]

    monkeypatch.setattr(core.time, "time", fake_time)
    with pytest.raises(TimeoutError):
        core.poll_qr("T", timeout=1, interval=0, on_status=seen.append)
    assert any("3503" in s for s in seen), f"状态回调没有带出风控原文：{seen}"


# --------------------------------------------------------------------------- #
# 需要真实窗口的用例（无桌面环境自动跳过）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def root():
    tk = pytest.importorskip("tkinter")
    try:
        win = tk.Tk()
    except Exception as exc:  # pragma: no cover - 无显示环境
        pytest.skip(f"无桌面环境：{exc}")
    win.withdraw()
    yield win
    win.destroy()


def test_app_creates_widgets(root, monkeypatch):
    monkeypatch.setattr(core, "create_qr",
                        lambda timeout=20.0: {"retcode": 0, "data": {"url": "https://x/qr?ticket=T1",
                                                                     "ticket": "T1"}})
    app = gui.App(root, interval=0)
    assert app.status_var.get()
    assert app.url_var.get() == ""
    app.root.update()


def test_app_auto_copies_cookies_to_clipboard(root, monkeypatch):
    """核心诉求：扫码成功后 cookies 自动进剪贴板并有提示"""
    monkeypatch.setattr(core, "qr_login_flow", lambda **kwargs: RESULT)
    app = gui.App(root, interval=0)
    app.start()
    for _ in range(50):  # 等后台线程 + 队列刷新
        root.update()
        if app.result:
            break
        app._drain()
        core.time.sleep(0.02)
    assert app.result["cookies"] == RESULT["cookies"]
    assert app.last_copied == RESULT["cookies"]
    assert root.clipboard_get() == RESULT["cookies"]
    assert "cookies 已自动复制到剪贴板" in app.status_var.get()
    assert RESULT["cookies"] in app.text.get("1.0", "end")


def test_copy_cookies_button_uses_cookies(root, monkeypatch):
    monkeypatch.setattr(core, "qr_login_flow", lambda **kwargs: RESULT)
    app = gui.App(root, interval=0)
    app._on_done(RESULT)
    assert app.btn_cookies.cget("text") == "复制 cookies"
    assert str(app.btn_cookies.cget("state")) == "normal"
    app.btn_cookies.invoke()
    assert root.clipboard_get() == RESULT["cookies"]
    assert "已复制cookies到剪贴板" in app.status_var.get()


def test_text_has_horizontal_scrollbar(root):
    """长 token 不该只靠拖动窗口才能看全"""
    assert app_has_xscroll(root)


def app_has_xscroll(root):
    import importlib
    mod = importlib.import_module("stoken_login_gui")
    import tkinter as tk
    app = mod.App(root, interval=0)
    xscroll_ok = bool(app.text.cget("xscrollcommand"))
    has_hbar = any(isinstance(w, tk.Scrollbar) and str(w.cget("orient")) == "horizontal"
                   for w in app.text.master.winfo_children())
    return xscroll_ok and has_hbar


def test_app_copies_all(root, monkeypatch):
    monkeypatch.setattr(core, "qr_login_flow", lambda **kwargs: RESULT)
    app = gui.App(root, interval=0)
    app._on_done(RESULT)
    app.copy(app.text.get("1.0", "end").strip(), "全部结果")
    assert RESULT["stoken"] in root.clipboard_get()
    assert "已复制全部结果到剪贴板" in app.status_var.get()


def test_app_error_path(root, monkeypatch):
    def boom(**kwargs):
        raise TimeoutError("没有等到扫码确认，二维码已失效，请重新运行")

    monkeypatch.setattr(core, "qr_login_flow", boom)
    app = gui.App(root, interval=0)
    app.start()
    for _ in range(50):
        root.update()
        if gui.SYM_ERR in app.status_var.get():
            break
        app._drain()
        core.time.sleep(0.02)
    assert gui.SYM_ERR in app.status_var.get()
    assert app.running is False


def test_symbols_are_ascii_friendly():
    """Linux 上没装 emoji 字体会渲染成方块，所以非 Windows/macOS 用 ASCII 符号"""
    assert gui.platform_symbols("linux") == ("√", "×", "»")
    assert gui.platform_symbols("win32") == ("✅", "❌", "📋")
    assert gui.platform_symbols("darwin") == ("✅", "❌", "📋")
    # 当前平台取到的就是模块级符号
    assert (gui.SYM_OK, gui.SYM_ERR, gui.SYM_CLIP) == gui.platform_symbols(gui.sys.platform)


def test_app_writes_config_when_asked(root, monkeypatch, tmp_path):
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump({"enable": True, "account": {"cookie": "", "stuid": "",
                                                          "stoken": "StokenError", "mid": ""}},
                             Dumper=yaml.Dumper, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(core, "qr_login_flow", lambda **kwargs: RESULT)
    app = gui.App(root, config_path=str(cfg), auto_write=True, interval=0)
    app._on_done(RESULT)
    data = yaml.safe_load(open(cfg, encoding="utf-8"))
    assert data["account"]["stoken"] == RESULT["stoken"]
    assert "写入" in app.status_var.get()


def test_gui_module_has_main():
    assert callable(gui.main)
    assert gui.format_result(RESULT)


# --------------------------------------------------------------------------- #
# 字体：不能把 Windows 的字体名硬编码到 Linux/macOS 上
# --------------------------------------------------------------------------- #
def test_ui_font_is_platform_aware(monkeypatch):
    cases = {"win32": "Microsoft YaHei UI", "darwin": "PingFang SC", "linux": "Noto Sans CJK SC"}
    for platform, family in cases.items():
        monkeypatch.setattr(gui.sys, "platform", platform)
        assert gui.ui_font(10)[0] == family
        assert gui.ui_font(12, "bold") == (family, 12, "bold")
    monkeypatch.setattr(gui.sys, "platform", "win32")
    assert gui.mono_font(10) == ("Consolas", 10)


def test_no_hardcoded_windows_font_in_source():
    """源码里不应该再出现写死的 Windows 字体名"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "stoken_login_gui.py"), encoding="utf-8").read()
    assert "Microsoft YaHei UI" in src, "字体表里应该保留 Windows 项"
    # 只允许出现在 _FONT_FAMILY 里一次
    assert src.count("Microsoft YaHei UI") == 1
    assert '("Consolas", 10)' not in src


# --------------------------------------------------------------------------- #
# 二维码尺寸：曾经因为固定 320x320 的框把二维码裁掉、扫不出来
# --------------------------------------------------------------------------- #
def _png_size(path):
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


@pytest.mark.parametrize("target", [200, 300, 400])
def test_save_png_fits_target(tmp_path, target):
    import qrcode.image.pure  # noqa: F401  确认依赖在

    path = str(tmp_path / f"qr{target}.png")
    assert core.save_png("https://user.mihoyo.com/login-platform/mobile.html?"
                         "expire=1791652498&tk=4c5ce2ee-d815-4c1e-9a1e-0123456789ab&token_types=1#/login/qr",
                         path, target_px=target)
    width, height = _png_size(path)
    assert width == height
    assert width <= target, f"二维码 {width}px 超过了目标 {target}px（会被裁）"
    assert width > target * 0.5, "太小了不好扫"


def test_save_png_without_target_keeps_default(tmp_path):
    import qrcode.image.pure  # noqa: F401

    path = str(tmp_path / "qr.png")
    assert core.save_png("https://example.com/x", path)
    width, _ = _png_size(path)
    assert width > 100


def test_qr_frame_never_smaller_than_image(root, tmp_path):
    """回归：容器必须 >= 图片尺寸，否则二维码会被截断（之前的 bug）"""
    import qrcode.image.pure  # noqa: F401

    url = ("https://user.mihoyo.com/login-platform/mobile.html?expire=1&tk="
           "4c5ce2ee-d815-4c1e-9a1e-0123456789ab&token_types=1#/login/qr")
    app = gui.App(root, interval=0, png=str(tmp_path / "qr.png"))
    app.qr_target = 200
    app._show_qr(url, "TICKET")
    assert app.qr_photo is not None, "二维码图片没加载出来"
    assert int(app.qr_frame.cget("width")) >= app.qr_photo.width(), "容器比二维码窄 → 会被裁掉"
    assert int(app.qr_frame.cget("height")) >= app.qr_photo.height(), "容器比二维码矮 → 会被截断"


def test_frame_shrinks_when_qr_no_longer_needed(root, monkeypatch):
    """成功后二维码要收起来给结果腾地方"""
    monkeypatch.setattr(core, "qr_login_flow", lambda **kwargs: RESULT)
    app = gui.App(root, interval=0)
    app._fit_frame(400, 400)
    app._on_done(RESULT)
    assert int(app.qr_frame.cget("height")) < 400
    assert "登录成功" in app.qr_label.cget("text")
