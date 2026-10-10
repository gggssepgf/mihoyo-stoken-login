# mihoyo-stoken-login

米游社**官方扫码登录**，拿到「App 端可用」的 `stoken` / `mid` / `ltoken` / `cookie_token`，
可直接写入 [MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools) 的 `config/config.yaml`。

## 为什么需要它

米游社 App 端接口只接受**官方登录产生的 App 端会话**。用第三方工具或扫码转换得来的 stoken 时，
现象非常迷惑人 —— 网页端一切正常，只有 App 端签到固定失败：

| 接口 | 第三方转换的 stoken | 本工具拿到的 stoken |
| --- | --- | --- |
| `GET /account/auth/api/getCookieAccountInfoBySToken` | ✅ `retcode 0` | ✅ `retcode 0` |
| `GET /account/auth/api/getLTokenBySToken` | ✅ `retcode 0` | ✅ `retcode 0` |
| `GET /apihub/wapi/getUserMissionsState` | ✅ `retcode 0` | ✅ `retcode 0` |
| 四个游戏每日签到 | ✅ 全部成功 | ✅ 全部成功 |
| **`POST /apihub/app/api/signIn`** | ❌ **`-100 登录失效，请重新登录`** | ✅ **`retcode 0`（+30 米游币）** |
| **`GET /user/api/getUserFullInfo`** | ❌ **`-100`** | ✅ **`retcode 0`** |

已逐项排除：风控、验证码（解出 challenge 后带 `x-rpc-challenge` 重签仍是 `-100`）、
DS 代次（只有 `salt_x6` + DS v2 能过签名校验，其余全是 `-10001`）、`x-rpc-client_type`、
cookie 组合、`mid`、`device_id`。结论只剩一条：**会话类型不对**。

## 下载 / 安装

**Windows**：到 [Releases](../../releases) 下载 `mihoyo-stoken-login-gui.exe`，双击运行即可
（PyInstaller 单文件打包，不需要装 Python）。

**Linux / macOS**：直接跑源码，tkinter 是 Python 标准库：

```bash
git clone https://github.com/gggssepgf/mihoyo-stoken-login.git
cd mihoyo-stoken-login
python3 -m pip install -r requirements.txt
python3 stoken_login_gui.py            # 图形界面
python3 stoken_login.py --help         # 命令行版
```

Debian/Ubuntu 如果提示没有 tkinter：`sudo apt install python3-tk`；
macOS 用 python.org 或 Homebrew 的 Python 都自带 tkinter。

想装进环境里当命令用也可以：

```bash
python3 -m pip install .               # 之后可用 mihoyo-stoken-login / mihoyo-stoken-login-gui
```

## 图形界面版（推荐日常使用）

Windows 双击 Release 里的 exe，Linux/macOS 跑源码：

```bash
python3 stoken_login_gui.py           # 或安装后用 mihoyo-stoken-login-gui
```

窗口里会直接把二维码画出来 → 用【米游社 App】扫码并在手机上点确认，之后：

- **stoken 自动复制到剪贴板**，顶部状态栏变成绿色 `✅ 登录成功，stoken 已自动复制到剪贴板`，
  并响一声提示音（Windows 用 `winsound`，其它平台用系统 bell）
- 下方文本框列出 `stuid` / `mid` / `stoken` / `ltoken` / `cookie_token` / `cookie`，
  以及一段可以直接粘贴进 `config.yaml` 的 `account:` YAML 片段，可自行选中复制
- 另有「复制 stoken」「复制全部」「复制链接」「重新申请二维码」按钮，以及「窗口置顶」开关
  （扫码时不想被别的窗口盖住就开着）

顺手写进配置：

```bash
python stoken_login_gui.py --config /path/to/MihoyoBBSTools/config/config.yaml --write
```

`--png qr.png` 可把二维码另存成图片（不指定则存到系统临时目录）。GUI 只依赖标准库
`tkinter`，无需额外安装。

## 用法

```bash
# 扫码 -> 打印结果
mihoyo-stoken-login

# 扫码 -> 备份并写入 MihoyoBBSTools 配置
mihoyo-stoken-login --config /path/to/MihoyoBBSTools/config/config.yaml

# 拿到后立刻调 App 端 signIn 验证会话是否真的可用（推荐）
mihoyo-stoken-login --config config.yaml --verify

# 其它
mihoyo-stoken-login --png qr.png      # 把二维码存成图片
mihoyo-stoken-login --json            # JSON 输出，便于脚本处理
mihoyo-stoken-login --only-ticket     # 只申请二维码
mihoyo-stoken-login --timeout 300     # 轮询等待秒数
```

跑在 MihoyoBBSTools 的 Docker 容器里：

```bash
docker exec -w /var/app mihoyobbstools-mihoyo-bbs-1 python3 /var/app/stoken_login.py \
    --config /var/app/config/config.yaml --verify
```

终端里会打印二维码链接，用**米游社 App**扫描并在手机上确认即可；`--png` 可以顺手存一张图片来扫。

## 它调了哪些接口

全部来自 [UIGF-org/mihoyo-api-sdk](https://github.com/UIGF-org/mihoyo-api-sdk) 的
`src/passport.tsp`：

```
POST https://passport-api.mihoyo.com/account/ma-cn-passport/app/createQRLogin        body {}
POST https://passport-api.mihoyo.com/account/ma-cn-passport/app/queryQRLoginStatus   body {"ticket": ...}
GET  https://api-takumi.mihoyo.com/auth/api/getCookieAccountInfoBySToken             ?stoken=&uid=&mid=&game_biz=hk4e_cn
GET  https://passport-api.mihoyo.com/account/auth/api/getLTokenBySToken              Cookie: stuid=;stoken=;mid=
POST https://bbs-api.miyoushe.com/apihub/app/api/signIn                              （--verify 时用来验证）
```

流程：`createQRLogin` → `queryQRLoginStatus`（`Created` → `Scanned` → `Confirmed`）
→ 从 `data.tokens[]` 取 `stoken`、`user_info.aid` / `user_info.mid` → 换 `ltoken`、`cookie_token`。

### ⚠️ SDK 只写了接口，没写"信封"

SDK 明确定义了路径与 body 字段，但**故意不提供 salt**（`runtime/ds.ts`：
*"this package never contains vendor salts or discovers them at runtime"*），
生成的客户端 README 示例是空占位（`<!-- write code here -->`），
passport 登录接口连请求头都没声明。实际能被服务器接受的请求还需要一层信封，
**缺任何一项都会得到 `-3001 参数不合法`**：

- passport 专用 salt：`JwYDpKvLj6MrMqqYU6jTKF17KNO2PXoS`（用于 DS v2 签名）
- 请求头：`x-rpc-app_id`、`x-rpc-client_type`、`x-rpc-app_version`、`x-rpc-device_id`、
  `x-rpc-device_fp`、`x-rpc-device_name`、`x-rpc-device_model`、`x-rpc-game_biz`、
  `x-rpc-sys_version`、`x-rpc-aigis`、`x-rpc-sdk_version`、`x-rpc-channel`
- `createQRLogin` 的 body 必须是**空对象** `{}`

这些参数在本仓库里都有实现，`tests/` 里也有对应的断言，方便日后接口微调时定位。

### 关于设备 id

随机设备 id 会让 passport 回 `-3503 请求失败，当前设备或网络环境存在风险`，
所以设备 id 会在首次运行时生成并保存到 `~/.mihoyo-stoken-login/device_id` 复用
（可用环境变量 `MIHOYO_STOKEN_LOGIN_HOME` 改目录）。

### 关于短信登录

`POST /account/ma-cn-verifier/verifier/createLoginCaptcha`（短信登录第一步）按 SDK 的
`LoginCaptchaRequest { area_code, mobile }` 严格构造仍然返回 `-3001`，
带不带 `action_type` 都一样，且没有可参考的可用实现，因此本工具只提供扫码登录。
扫码和短信拿到的是同一类官方 App 会话。

## 开发

```bash
pip install -r requirements.txt pytest
pytest -q
```

## 发布

推 tag 或手动触发 `Release` workflow，会自动：

1. 在 Linux 上跑单元测试（用 `xvfb` 让 GUI 用例也真跑起来）
2. 在 Windows 上用 PyInstaller 打一个单文件 GUI exe，并对产物做启动冒烟测试
3. 创建 GitHub Release，附上该 exe、README 与 LICENSE（Linux/macOS 用户直接用源码运行）

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## 许可证

MIT
