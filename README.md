# mihoyo-stoken-login

米游社官方扫码登录，获取 **App 端可用**的 `stoken` / `mid` / `ltoken` / `cookie_token`，
可直接写入 [MihoyoBBSTools](https://github.com/Womsxd/MihoyoBBSTools) 的 `config/config.yaml`。

## 为什么需要它

米游社 App 端接口（`/apihub/app/api/signIn` 等）只接受**官方登录产生的 App 端会话**。
用第三方工具或扫码转换得来的 stoken，网页端接口和游戏签到都正常，但米游社签到会固定返回
`-100 登录失效，请重新登录`。本工具直接走官方 passport 扫码登录，拿到的就是 App 端可用的会话。

## 下载 / 安装

**Windows**：到 [Releases](../../releases) 下载 `mihoyo-stoken-login-gui.exe`，双击运行即可
（单文件，不需要装 Python）。

**Linux / macOS**：直接跑源码，tkinter 是 Python 标准库。

```bash
git clone https://github.com/gggssepgf/mihoyo-stoken-login.git
cd mihoyo-stoken-login
python3 -m pip install -r requirements.txt
python3 stoken_login_gui.py            # 图形界面
python3 stoken_login.py --help         # 命令行版
```

Debian/Ubuntu 如果提示没有 tkinter：`sudo apt install python3-tk`。

## 图形界面版

打开窗口后会直接显示二维码，用【米游社 App】扫码并在手机上确认，然后：

- **cookies 自动复制到剪贴板**，状态栏提示并响一声
- 文本框列出 `stuid` / `mid` / `stoken` / `ltoken` / `cookie_token` / `cookies`，
  以及可直接粘进 `config.yaml` 的 `account:` 段；内容较长时可以横向拖动查看
- 按钮：复制 cookies / 复制全部 / 复制链接 / 重新申请二维码；「窗口置顶」默认开启

顺带写进 MihoyoBBSTools 配置：

```bash
python3 stoken_login_gui.py --config /path/to/config.yaml --write
```

## 命令行版

```bash
python3 stoken_login.py                                  # 扫码 -> 打印结果
python3 stoken_login.py --config config.yaml             # 扫码 -> 备份并写入配置
python3 stoken_login.py --config config.yaml --verify    # 写入后立刻验证 App 端是否接受该会话
python3 stoken_login.py --json                           # JSON 输出
python3 stoken_login.py --only-ticket                    # 只申请二维码
python3 stoken_login.py --png qr.png                     # 二维码另存为图片
```

## 许可证

MIT
