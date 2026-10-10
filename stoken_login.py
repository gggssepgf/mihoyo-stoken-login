#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mihoyo-stoken-login —— 米游社官方扫码登录，获取「App 端可用」的 stoken

为什么需要它
------------
米游社 App 端接口（/apihub/app/api/signIn、/apihub/sapi/*、/user/api/*）只接受
**官方登录产生的 App 端会话**。用第三方工具/扫码转换得来的 stoken 时表现是：

    /account/auth/api/getCookieAccountInfoBySToken   -> retcode 0   （网页端通过）
    /account/auth/api/getLTokenBySToken              -> retcode 0   （网页端通过）
    /apihub/wapi/getUserMissionsState                -> retcode 0   （网页端通过）
    /apihub/app/api/signIn                           -> retcode -100 （App 端登录失效）
    四个游戏的每日签到                                  -> 全部成功

本工具走官方 passport 的扫码登录，拿到官方会话的 stoken / mid / ltoken / cookie_token，
可以直接写进 MihoyoBBSTools 的 config/config.yaml。

用法
----
    stoken_login.py                          # 扫码 -> 打印结果
    stoken_login.py --config config.yaml     # 扫码 -> 备份并写入 MihoyoBBSTools 配置
    stoken_login.py --json                   # 以 JSON 输出（便于脚本处理）
    stoken_login.py --png qr.png             # 把二维码存成 PNG
    stoken_login.py --only-ticket            # 只申请二维码，不轮询
    stoken_login.py --timeout 300            # 轮询等待秒数（默认 300）
    stoken_login.py --verify                 # 拿到后立刻验证 signIn 是否被 App 端接受
    stoken_login.py --version

实现说明
--------
UIGF-org/mihoyo-api-sdk 只定义接口路径与 body 字段，且明确声明不提供 vendor salt
（runtime/ds.ts: "this package never contains vendor salts"），生成的客户端示例是空占位，
passport 登录接口在 SDK 里连请求头都没声明。真正能被服务器接受的请求还需要一层信封，
缺任何一项都会得到 -3001 参数不合法：

  * passport 专用 salt：JwYDpKvLj6MrMqqYU6jTKF17KNO2PXoS（用于 DS v2 签名）
  * 请求头 x-rpc-app_id / x-rpc-client_type / x-rpc-app_version / x-rpc-device_id /
    x-rpc-device_fp / x-rpc-device_name / x-rpc-device_model / x-rpc-game_biz /
    x-rpc-sys_version / x-rpc-aigis / x-rpc-sdk_version / x-rpc-channel
  * createQRLogin 的 body 必须是空对象 {}

许可证：MIT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import string
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

__version__ = "1.0.0"

PASS_API = "https://passport-api.mihoyo.com"
WEB_API = "https://api-takumi.mihoyo.com"
PASS_SALT = "JwYDpKvLj6MrMqqYU6jTKF17KNO2PXoS"
APP_ID = "bll8iq97cem8"
APP_VERSION = "2.70.1"
USER_AGENT = "okhttp/4.8.0"

CREATE_QR = "/account/ma-cn-passport/app/createQRLogin"
QUERY_QR = "/account/ma-cn-passport/app/queryQRLoginStatus"
COOKIE_BY_STOKEN = "/auth/api/getCookieAccountInfoBySToken"
LTOKEN_BY_STOKEN = "/account/auth/api/getLTokenBySToken"
SIGN_IN = "https://bbs-api.miyoushe.com/apihub/app/api/signIn"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def ds2(body: str = "", query: str = "", salt: str = PASS_SALT,
        timestamp: Optional[int] = None, nonce: Optional[int] = None) -> str:
    """passport 的 DS v2 签名：md5(f"salt={salt}&t={t}&r={r}&b={body}&q={query}")

    timestamp / nonce 可显式传入，便于测试时得到确定结果。
    """
    t = str(int(time.time()) if timestamp is None else timestamp)
    r = str(random.randint(100001, 200000) if nonce is None else nonce)
    return f"{t},{r},{md5(f'salt={salt}&t={t}&r={r}&b={body}&q={query}')}"


def rand_text(n: int) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def device_dir() -> str:
    """设备 id 的存放目录（跨平台）"""
    base = os.environ.get("MIHOYO_STOKEN_LOGIN_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".mihoyo-stoken-login")
    return base


def device_id(path: Optional[str] = None) -> str:
    """稳定的设备 id：首次生成后落盘复用。

    每次随机设备 id 很容易让 passport 返回 -3503「当前设备或网络环境存在风险」。
    """
    if path is None:
        path = os.path.join(device_dir(), "device_id")
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = rand_text(32).upper()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)
    except OSError:
        pass
    return value


def build_cookie(uid: str, ltoken: str = "", cookie_token: str = "") -> str:
    """拼 MihoyoBBSTools 的 account.cookie（ltuid/ltoken/cookie_token/account_id）"""
    parts = [f"ltuid={uid}"]
    if ltoken:
        parts.append(f"ltoken={ltoken}")
    if cookie_token:
        parts.append(f"cookie_token={cookie_token}")
    parts.append(f"account_id={uid}")
    return "; ".join(parts) + ";"


def build_stoken_cookie(uid: str, stoken: str, ltoken: str = "", mid: str = "") -> str:
    """拼 stoken 形式的 cookie（stuid/stoken/ltoken/mid，无空格、结尾带分号）

    形如：
        stuid=6061062;stoken=v2_xxx;ltoken=xxx;mid=0px3aha5ri_mhy;
    """
    parts = [f"stuid={uid}", f"stoken={stoken}"]
    if ltoken:
        parts.append(f"ltoken={ltoken}")
    if mid:
        parts.append(f"mid={mid}")
    return ";".join(parts) + ";"


def extract_account(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    """从 queryQRLoginStatus 的 data 里取 (uid, mid, stoken)"""
    info = payload.get("user_info") or {}
    uid = str(info.get("aid") or info.get("uid") or info.get("account_id") or "")
    mid = info.get("mid") or ""
    token = ""
    tokens: List[Dict[str, Any]] = payload.get("tokens") or []
    for item in tokens:
        if item.get("name") in ("stoken", "stoken_v2"):
            token = item.get("token", "")
            break
    if not token and tokens:
        token = tokens[0].get("token", "")
    return uid, mid, token


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def pass_headers(body: str, cookie: str = "", dev_id: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "x-rpc-device_id": dev_id or device_id(),
        "x-rpc-app_id": APP_ID,
        "x-rpc-device_name": "Xiaomi MI 6",
        "x-rpc-device_fp": "38d7ee0e96649",
        "x-rpc-device_model": "Mi 6",
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-game_biz": "bbs_cn",
        "x-rpc-sys_version": "11",
        "x-rpc-aigis": "",
        "Content-Type": "application/json;",
        "x-rpc-client_type": "2",
        "DS": ds2(body),
        "x-rpc-sdk_version": "1.3.1.2",
        "User-Agent": USER_AGENT,
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip, deflate, br",
        "x-rpc-channel": "appstore",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _httpx():
    try:
        import httpx  # noqa: F401
    except ImportError:  # pragma: no cover
        sys.exit("缺少依赖 httpx，请先执行：pip install httpx")
    import httpx
    return httpx


def passport_post(path: str, body: Dict[str, Any], cookie: str = "",
                  timeout: float = 20.0) -> Dict[str, Any]:
    httpx = _httpx()
    raw = json.dumps(body, separators=(",", ":"))
    resp = httpx.post(PASS_API + path, content=raw,
                      headers=pass_headers(raw, cookie), timeout=timeout)
    try:
        return resp.json()
    except Exception:
        return {"retcode": -999, "message": f"HTTP {resp.status_code} {resp.text[:200]}"}


def create_qr(timeout: float = 20.0) -> Dict[str, Any]:
    """申请扫码登录二维码。body 必须是空对象 {}"""
    return passport_post(CREATE_QR, {}, timeout=timeout)


def query_qr(ticket: str, timeout: float = 20.0) -> Dict[str, Any]:
    return passport_post(QUERY_QR, {"ticket": ticket}, timeout=timeout)


def cookie_token_by_stoken(stoken: str, uid: str, mid: str, timeout: float = 20.0) -> str:
    httpx = _httpx()
    try:
        resp = httpx.get(WEB_API + COOKIE_BY_STOKEN,
                         params={"game_biz": "hk4e_cn", "stoken": stoken, "uid": uid, "mid": mid},
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
        return (resp.json().get("data") or {}).get("cookie_token", "")
    except Exception:
        return ""


def ltoken_by_stoken(stoken: str, uid: str, mid: str, timeout: float = 20.0) -> str:
    httpx = _httpx()
    try:
        resp = httpx.get(PASS_API + LTOKEN_BY_STOKEN,
                         headers={"Cookie": f"stuid={uid};stoken={stoken};mid={mid}",
                                  "User-Agent": USER_AGENT}, timeout=timeout)
        return (resp.json().get("data") or {}).get("ltoken", "")
    except Exception:
        return ""


def verify_signin(stoken: str, uid: str, mid: str, gids: int = 5,
                  timeout: float = 20.0) -> Dict[str, Any]:
    """用新 stoken 直接打 App 端签到接口，确认会话被接受。

    retcode 0    -> 签到成功
    retcode 1008 -> 今天已经签过（同样说明会话有效）
    retcode -100 -> 会话不被 App 端接受（stoken 有问题）
    """
    httpx = _httpx()
    cookie = f"stuid={uid};stoken={stoken};mid={mid}"
    body = json.dumps({"gids": gids})
    headers = {
        "DS": ds2(body, salt="t0qEgfub6cvueAPgR5m9aQWWVciEer7v"),
        "cookie": cookie,
        "x-rpc-client_type": "2",
        "x-rpc-app_version": "2.109.0",
        "x-rpc-sys_version": "12",
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_id": device_id(),
        "x-rpc-device_name": "Xiaomi MI 6",
        "x-rpc-device_model": "Mi 6",
        "x-rpc-h265_supported": "1",
        "Referer": "https://app.mihoyo.com",
        "x-rpc-verify_key": APP_ID,
        "x-rpc-csm_source": "discussion",
        "Content-Type": "application/json; charset=UTF-8",
        "Host": "bbs-api.miyoushe.com",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "User-Agent": "okhttp/4.9.3",
    }
    try:
        resp = httpx.post(SIGN_IN, content=body, headers=headers, timeout=timeout)
        return resp.json()
    except Exception as exc:  # pragma: no cover
        return {"retcode": -999, "message": repr(exc)}


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def save_png(url: str, path: str, target_px: int = 0) -> bool:
    """把二维码存成 PNG。

    target_px > 0 时按二维码模块数反推 box_size，保证生成图**不超过** target_px 像素
    （Tk 不会缩放图片，框比图小就会把二维码裁掉，扫不出来）。
    """
    try:
        import qrcode
        from qrcode.image.pure import PyPNGImage

        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        if target_px and target_px > 0:
            side_modules = qr.modules_count + 2 * qr.border
            qr.box_size = max(2, int(target_px) // side_modules)
        img = qr.make_image(image_factory=PyPNGImage)
        img.save(path)
        return True
    except Exception as exc:
        print(f"[warn] 生成 PNG 失败（需要 pip install qrcode pypng）：{exc}", file=sys.stderr)
        return False


def write_config(path: str, uid: str, stoken: str, mid: str,
                 ltoken: str = "", cookie_token: str = "") -> str:
    """把结果写进 MihoyoBBSTools 的 config.yaml，返回备份文件路径"""
    try:
        import yaml
    except ImportError:  # pragma: no cover
        sys.exit("缺少依赖 PyYAML，请先执行：pip install PyYAML")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "account" not in data:
        sys.exit(f"{path} 看起来不是 MihoyoBBSTools 的 config.yaml")
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    with open(path, "r", encoding="utf-8") as src, open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    data["account"]["cookie"] = build_cookie(uid, ltoken, cookie_token)
    data["account"]["stuid"] = uid
    data["account"]["stoken"] = stoken
    data["account"]["mid"] = mid
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=yaml.Dumper, sort_keys=False)
    return backup


def _out(text: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936
    except Exception:
        pass
    print(text)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def poll_qr(ticket: str, timeout: int = 300, interval: int = 3,
            on_status: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """轮询扫码状态，直到 Confirmed（或超时抛 TimeoutError）"""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    deadline = time.time() + timeout
    seen: Any = ""
    while time.time() < deadline:
        time.sleep(interval)
        resp = query_qr(ticket)
        if resp.get("retcode") != 0:
            msg = resp.get("message", "")
            if msg != seen:
                status(f"轮询：{resp.get('retcode')} {msg}")
                seen = msg
            continue
        data = resp.get("data") or {}
        state = data.get("status", "")
        if state and state != seen:
            status(f"状态：{state}")
            seen = state
        if state == "Confirmed" or data.get("tokens"):
            return data
    raise TimeoutError("没有等到扫码确认，二维码已失效，请重新运行")


def qr_login_flow(timeout: int = 300, interval: int = 3,
                  on_qr: Optional[Callable[[str, str], None]] = None,
                  on_status: Optional[Callable[[str], None]] = None,
                  on_token: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """完整扫码登录流程，通过回调汇报进度（不打印任何东西），供 CLI 与 GUI 共用。

    on_qr(url, ticket)   申请到二维码时立刻回调（GUI 用来画二维码）
    on_status(text)      状态变化时回调
    on_token(result)     拿到 ltoken / cookie_token 后回调
    """
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    status("正在申请二维码…")
    res = create_qr()
    if res.get("retcode") != 0:
        raise RuntimeError(f"申请二维码失败：{res}")
    data = res["data"]
    url = data.get("url", "")
    ticket = data.get("ticket") or url.split("ticket=")[-1]
    if on_qr:
        on_qr(url, ticket)

    status("等待扫码…")
    payload = poll_qr(ticket, timeout=timeout, interval=interval, on_status=on_status)

    uid, mid, stoken = extract_account(payload)
    if not (uid and mid and stoken):
        raise RuntimeError(f"返回数据不完整：{json.dumps(payload, ensure_ascii=False)[:400]}")
    status("正在换取 ltoken / cookie_token…")
    ltoken = ltoken_by_stoken(stoken, uid, mid)
    cookie_token = cookie_token_by_stoken(stoken, uid, mid)
    result: Dict[str, Any] = {"uid": uid, "mid": mid, "stoken": stoken,
                              "ltoken": ltoken, "cookie_token": cookie_token,
                              "cookies": build_stoken_cookie(uid, stoken, ltoken, mid),
                              "cookie": build_cookie(uid, ltoken, cookie_token)}
    if on_token:
        on_token(result)
    return result


def login(timeout: int = 300, interval: int = 3, png: str = "",
          only_ticket: bool = False, want_json: bool = False) -> Dict[str, Any]:
    """CLI 用的封装：把流程进度打印到标准输出"""
    if only_ticket:
        res = create_qr()
        if res.get("retcode") != 0:
            raise RuntimeError(f"申请二维码失败：{res}")
        data = res["data"]
        url = data.get("url", "")
        ticket = data.get("ticket") or url.split("ticket=")[-1]
        if not want_json:
            _print_qr(url, ticket)
            if png and save_png(url, png):
                _out(f"二维码已保存：{png}")
        return {"url": url, "ticket": ticket}

    def on_qr(url: str, ticket: str) -> None:
        if not want_json:
            _print_qr(url, ticket)
            if png and save_png(url, png):
                _out(f"二维码已保存：{png}")

    def on_status(msg: str) -> None:
        if not want_json:
            _out("  " + msg)

    return qr_login_flow(timeout=timeout, interval=interval,
                         on_qr=on_qr, on_status=on_status)


def _print_qr(url: str, ticket: str) -> None:
    _out("=" * 70)
    _out("请用【米游社 App】扫描下面的二维码并在手机上确认登录：")
    _out("  " + url)
    _out("ticket: " + ticket)
    _out("=" * 70)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stoken_login",
        description="米游社官方扫码登录，获取 App 端可用的 stoken",
    )
    parser.add_argument("--config", default="", metavar="PATH",
                        help="MihoyoBBSTools 的 config.yaml 路径；给了就备份并写入")
    parser.add_argument("--png", default="", metavar="PATH", help="把二维码保存成 PNG")
    parser.add_argument("--json", action="store_true", dest="want_json",
                        help="以 JSON 输出结果")
    parser.add_argument("--only-ticket", action="store_true",
                        help="只申请二维码，不轮询、不写配置")
    parser.add_argument("--verify", action="store_true",
                        help="拿到 stoken 后立刻调 App 端 signIn 验证会话是否可用")
    parser.add_argument("--timeout", type=int, default=300, help="轮询等待秒数（默认 300）")
    parser.add_argument("--interval", type=int, default=3, help="轮询间隔秒数（默认 3）")
    parser.add_argument("--version", action="version", version=f"mihoyo-stoken-login {__version__}")
    args = parser.parse_args(argv)

    try:
        result = login(timeout=args.timeout, interval=args.interval, png=args.png,
                       only_ticket=args.only_ticket, want_json=args.want_json)
    except (RuntimeError, TimeoutError) as exc:
        if args.want_json:
            _out(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            _out(f"[错误] {exc}")
        return 1

    if args.only_ticket:
        if args.want_json:
            _out(json.dumps(result, ensure_ascii=False))
        return 0

    verify = None
    if args.verify:
        verify = verify_signin(result["stoken"], result["uid"], result["mid"])
        result["verify"] = verify

    written = ""
    if args.config:
        written = write_config(args.config, result["uid"], result["stoken"], result["mid"],
                               result.get("ltoken", ""), result.get("cookie_token", ""))
        result["config_backup"] = written

    if args.want_json:
        _out(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0

    _out("")
    _out("[完成] 官方 App 会话已获取：")
    _out(f"  stuid        = {result['uid']}")
    _out(f"  mid          = {result['mid']}")
    _out(f"  stoken       = {result['stoken'][:32]}…（已截断）")
    _out(f"  ltoken       = {result.get('ltoken', '')[:16] + '…' if result.get('ltoken') else '(未取到)'}")
    _out(f"  cookie_token = {result.get('cookie_token', '')[:16] + '…' if result.get('cookie_token') else '(未取到)'}")
    _out(f"  cookie       = {result['cookie'][:80]}…")
    if args.config:
        _out(f"[写入] {args.config}（原文件备份：{written}）")
    if verify is not None:
        code = verify.get("retcode")
        if code in (0, 1008):
            _out(f"[验证] App 端 signIn 通过：retcode={code} {verify.get('message', '')}"
                 f"{'（今天已签过，同样说明会话可用）' if code == 1008 else ''}")
        else:
            _out(f"[验证] App 端 signIn 失败：retcode={code} {verify.get('message', '')}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
