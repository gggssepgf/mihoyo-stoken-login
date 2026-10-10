# -*- coding: utf-8 -*-
"""stoken_login.py 的离线测试（不联网）"""
import hashlib
import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stoken_login as S  # noqa: E402


# --------------------------------------------------------------------------- #
# DS 签名
# --------------------------------------------------------------------------- #
def test_ds2_matches_documented_formula():
    sig = S.ds2("{}", "", timestamp=1700000000, nonce=123456)
    t, r, digest = sig.split(",")
    assert (t, r) == ("1700000000", "123456")
    expected = hashlib.md5(
        f"salt={S.PASS_SALT}&t=1700000000&r=123456&b={{}}&q=".encode()).hexdigest()
    assert digest == expected
    assert len(digest) == 32


def test_ds2_is_v2_shape_with_body_and_query():
    sig = S.ds2('{"a":1}', "x=1", timestamp=1, nonce=100001)
    expected = hashlib.md5(
        f"salt={S.PASS_SALT}&t=1&r=100001&b={{\"a\":1}}&q=x=1".encode()).hexdigest()
    assert sig == f"1,100001,{expected}"


def test_ds2_nonce_is_numeric_in_range():
    for _ in range(20):
        _, nonce, _ = S.ds2("{}").split(",")
        assert nonce.isdigit() and 100000 <= int(nonce) <= 200000


# --------------------------------------------------------------------------- #
# 请求头信封（缺一项就会 -3001）
# --------------------------------------------------------------------------- #
REQUIRED_HEADERS = [
    "x-rpc-device_id", "x-rpc-app_id", "x-rpc-device_name", "x-rpc-device_fp",
    "x-rpc-device_model", "x-rpc-app_version", "x-rpc-game_biz", "x-rpc-sys_version",
    "x-rpc-aigis", "Content-Type", "x-rpc-client_type", "DS", "x-rpc-sdk_version",
    "User-Agent", "x-rpc-channel",
]


def test_pass_headers_contain_required_envelope(tmp_path):
    headers = S.pass_headers("{}", dev_id="ABC123")
    for key in REQUIRED_HEADERS:
        assert key in headers, f"缺少必需请求头 {key}"
    assert headers["x-rpc-app_id"] == S.APP_ID
    assert headers["x-rpc-client_type"] == "2"
    assert headers["x-rpc-device_id"] == "ABC123"
    assert headers["Content-Type"].startswith("application/json")
    assert headers["DS"].count(",") == 2
    assert "Cookie" not in headers


def test_pass_headers_add_cookie_when_given():
    headers = S.pass_headers("{}", cookie="stuid=1;stoken=x;mid=y", dev_id="A")
    assert headers["Cookie"] == "stuid=1;stoken=x;mid=y"


# --------------------------------------------------------------------------- #
# cookie 拼装 / 响应解析
# --------------------------------------------------------------------------- #
def test_build_cookie_full():
    cookie = S.build_cookie("6061062", "LTOKEN", "COOKIETOKEN")
    assert cookie == "ltuid=6061062; ltoken=LTOKEN; cookie_token=COOKIETOKEN; account_id=6061062;"


def test_build_cookie_without_tokens():
    assert S.build_cookie("6061062") == "ltuid=6061062; account_id=6061062;"


def test_build_stoken_cookie_exact_format():
    """cookies 的格式必须和用户要求的一致：无空格、结尾带分号"""
    cookie = S.build_stoken_cookie("6061062", "V2STOKEN", "LTOKEN", "0px3aha5ri_mhy")
    assert cookie == "stuid=6061062;stoken=V2STOKEN;ltoken=LTOKEN;mid=0px3aha5ri_mhy;"
    assert " " not in cookie
    assert cookie.endswith(";")


def test_build_stoken_cookie_without_ltoken():
    assert S.build_stoken_cookie("1", "S", "", "M") == "stuid=1;stoken=S;mid=M;"


def test_qr_login_flow_result_has_cookies(monkeypatch):
    monkeypatch.setattr(S, "create_qr",
                        lambda timeout=20.0: {"retcode": 0, "data": {"url": "u?ticket=T", "ticket": "T"}})
    monkeypatch.setattr(S, "query_qr",
                        lambda ticket, timeout=20.0: {"retcode": 0, "data": {
                            "status": "Confirmed", "user_info": {"aid": 6061062, "mid": "m_mhy"},
                            "tokens": [{"name": "stoken_v2", "token": "ST"}]}})
    monkeypatch.setattr(S, "ltoken_by_stoken", lambda *a, **k: "LT")
    monkeypatch.setattr(S, "cookie_token_by_stoken", lambda *a, **k: "CT")
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    result = S.qr_login_flow(interval=0)
    assert result["cookies"] == "stuid=6061062;stoken=ST;ltoken=LT;mid=m_mhy;"


@pytest.mark.parametrize("info_key", ["aid", "uid", "account_id"])
def test_extract_account_uid_keys(info_key):
    payload = {
        "user_info": {info_key: 6061062, "mid": "0px3aha5ri_mhy"},
        "tokens": [{"name": "cookie_token", "token": "CT"},
                   {"name": "stoken_v2", "token": "STOKEN"}],
    }
    assert S.extract_account(payload) == ("6061062", "0px3aha5ri_mhy", "STOKEN")


def test_extract_account_prefers_stoken_over_first():
    payload = {
        "user_info": {"aid": 1, "mid": "m"},
        "tokens": [{"name": "other", "token": "OTHER"}, {"name": "stoken", "token": "S1"}],
    }
    assert S.extract_account(payload)[2] == "S1"


def test_extract_account_falls_back_to_first_token():
    payload = {"user_info": {"aid": 1, "mid": "m"}, "tokens": [{"name": "weird", "token": "T"}]}
    assert S.extract_account(payload)[2] == "T"


def test_extract_account_empty_payload():
    assert S.extract_account({}) == ("", "", "")


# --------------------------------------------------------------------------- #
# 设备 id
# --------------------------------------------------------------------------- #
def test_device_id_is_stable_and_persisted(tmp_path):
    path = str(tmp_path / "device_id")
    first = S.device_id(path)
    second = S.device_id(path)
    assert first == second
    assert len(first) == 32 and first.isupper()
    assert open(path, encoding="utf-8").read() == first


def test_device_id_regenerates_when_empty(tmp_path):
    path = tmp_path / "device_id"
    path.write_text("", encoding="utf-8")
    value = S.device_id(str(path))
    assert len(value) == 32


# --------------------------------------------------------------------------- #
# 写 config.yaml
# --------------------------------------------------------------------------- #
CONFIG_TEMPLATE = {
    "enable": True,
    "version": 15,
    "push": "",
    "account": {"cookie": "OLD", "stuid": "", "stoken": "StokenError", "mid": ""},
    "device": {"name": "Xiaomi MI 6", "model": "Mi 6", "id": "", "fp": ""},
    "mihoyobbs": {"enable": True, "checkin": True, "checkin_list": [5, 2]},
}


def test_write_config_updates_account_and_backs_up(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump(CONFIG_TEMPLATE, Dumper=yaml.Dumper, sort_keys=False),
                   encoding="utf-8")
    backup = S.write_config(str(cfg), "6061062", "NEWSTOKEN", "mid_mhy", "LT", "CT")
    assert os.path.exists(backup)
    assert "OLD" in open(backup, encoding="utf-8").read()

    data = yaml.safe_load(open(cfg, encoding="utf-8"))
    assert data["account"]["stuid"] == "6061062"
    assert data["account"]["stoken"] == "NEWSTOKEN"
    assert data["account"]["mid"] == "mid_mhy"
    assert data["account"]["cookie"] == "ltuid=6061062; ltoken=LT; cookie_token=CT; account_id=6061062;"
    # 其它配置不动
    assert data["mihoyobbs"]["checkin_list"] == [5, 2]
    assert data["version"] == 15


def test_write_config_rejects_non_mihoyobbstools_yaml(tmp_path):
    cfg = tmp_path / "other.yaml"
    cfg.write_text("foo: bar\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        S.write_config(str(cfg), "1", "S", "M")


# --------------------------------------------------------------------------- #
# 端到端（全部打桩，不联网）
# --------------------------------------------------------------------------- #
FAKE_PAYLOAD = {
    "status": "Confirmed",
    "user_info": {"aid": 6061062, "mid": "0px3aha5ri_mhy"},
    "tokens": [{"name": "stoken_v2", "token": "V2_NEW_STOKEN"}],
}


def _stub(monkeypatch, status_sequence=("Confirmed",)):
    state = {"i": 0}

    def fake_create_qr(timeout=20.0):
        return {"retcode": 0, "data": {"url": "https://user.mihoyo.com/qr?ticket=TICKET1",
                                       "ticket": "TICKET1"}}

    def fake_query_qr(ticket, timeout=20.0):
        idx = min(state["i"], len(status_sequence) - 1)
        state["i"] += 1
        status = status_sequence[idx]
        if status == "Confirmed":
            return {"retcode": 0, "data": FAKE_PAYLOAD}
        return {"retcode": 0, "data": {"status": status}}

    monkeypatch.setattr(S, "create_qr", fake_create_qr)
    monkeypatch.setattr(S, "query_qr", fake_query_qr)
    monkeypatch.setattr(S, "ltoken_by_stoken", lambda *a, **k: "LT_NEW")
    monkeypatch.setattr(S, "cookie_token_by_stoken", lambda *a, **k: "CT_NEW")


def test_only_ticket(monkeypatch, capsys):
    _stub(monkeypatch)
    assert S.main(["--only-ticket"]) == 0
    out = capsys.readouterr().out
    assert "user.mihoyo.com/qr" in out and "TICKET1" in out


def test_only_ticket_json(monkeypatch, capsys):
    _stub(monkeypatch)
    assert S.main(["--only-ticket", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ticket"] == "TICKET1"


def test_full_flow_writes_config(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, status_sequence=("Created", "Scanned", "Confirmed"))
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump(CONFIG_TEMPLATE, Dumper=yaml.Dumper, sort_keys=False),
                   encoding="utf-8")

    assert S.main(["--config", str(cfg), "--interval", "0"]) == 0
    data = yaml.safe_load(open(cfg, encoding="utf-8"))
    assert data["account"]["stoken"] == "V2_NEW_STOKEN"
    assert data["account"]["mid"] == "0px3aha5ri_mhy"
    assert "Created" in capsys.readouterr().out


def test_full_flow_verify_ok(monkeypatch, tmp_path):
    _stub(monkeypatch)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    monkeypatch.setattr(S, "verify_signin",
                        lambda *a, **k: {"retcode": 1008, "message": "打卡失败或重复打卡"})
    assert S.main(["--verify"]) == 0


def test_full_flow_verify_rejects_bad_session(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    monkeypatch.setattr(S, "verify_signin",
                        lambda *a, **k: {"retcode": -100, "message": "登录失效，请重新登录"})
    assert S.main(["--verify"]) == 2


def test_full_flow_json(monkeypatch, capsys):
    _stub(monkeypatch)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    assert S.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["stoken"] == "V2_NEW_STOKEN"
    assert payload["ltoken"] == "LT_NEW"
    assert payload["cookie_token"] == "CT_NEW"


def test_qr_request_failure(monkeypatch, capsys):
    monkeypatch.setattr(S, "create_qr",
                        lambda timeout=20.0: {"retcode": -3001, "message": "参数不合法"})
    assert S.main([]) == 1
    assert "参数不合法" in capsys.readouterr().out


def test_scan_timeout(monkeypatch):
    """扫码一直不确认时应该超时退出（时钟必须推进，否则会死循环——这里就是回归点）"""
    _stub(monkeypatch, status_sequence=("Created",))
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    clock = {"t": 10_000.0}

    def fake_time():  # 小步推进：轮询循环会真的跑几轮再超时
        clock["t"] += 0.2
        return clock["t"]

    monkeypatch.setattr(S.time, "time", fake_time)
    assert S.main(["--timeout", "1"]) == 1


def test_incomplete_payload(monkeypatch):
    monkeypatch.setattr(S, "create_qr",
                        lambda timeout=20.0: {"retcode": 0, "data": {"url": "u", "ticket": "t"}})
    monkeypatch.setattr(S, "query_qr",
                        lambda ticket, timeout=20.0: {"retcode": 0, "data": {"status": "Confirmed",
                                                                            "user_info": {"aid": 1},
                                                                            "tokens": []}})
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    assert S.main([]) == 1


# --------------------------------------------------------------------------- #
# 仓库自检：workflow 必须是合法 YAML 且具备发布所需步骤
# --------------------------------------------------------------------------- #
def test_release_workflow_shape():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".github", "workflows", "release.yml")
    assert os.path.exists(path), "缺少 .github/workflows/release.yml"
    with open(path, encoding="utf-8") as f:
        wf = yaml.safe_load(f)
    text = json.dumps(wf, ensure_ascii=False)
    assert "contents" in text and "write" in text, "workflow 需要 contents: write 权限"
    assert "tags" in text, "workflow 需要在 tag 上触发"
    assert "pyinstaller" in text.lower(), "workflow 需要用 PyInstaller 编译单文件"
    assert "build" in text, "workflow 需要构建 wheel/sdist"
    assert "action-gh-release" in text, "workflow 需要发布 Release"
