# RDK X5 + HDMI｜V4 现场操作

## 拓扑

```text
PC 产品页 / qwen3.8-max 智能体
              ↓ 同一 TactileFrameV2 + runtime events
PC 80×48 数字孪生            RDK /display.html
```

RDK 页面只显示最终银色触觉表面，不调用模型、不运行检测、不生成第二份矩阵。PC 和 RDK 必须显示相同 `frame_id` 与 `checksum`。

## 当前 Windows 运行

在项目目录运行：

```powershell
python app/main.py --host 0.0.0.0 --port 8765
```

- PC 控制页：`http://127.0.0.1:8765/`
- RDK 展示页：PC 页面开发者面板给出的 `http://PC_IP:8765/display.html`

端口保持 `8765`。

## RDK 打开展示页

浏览器直接打开（PC 防火墙允许 `8765/TCP` 时）：

```text
http://PC_IP:8765/display.html
```

或通过 SSH 在 HDMI 桌面启动：

```bash
DISPLAY=:0 firefox --kiosk http://PC_IP:8765/display.html
```

RDK 页面启动后先读取 `/api/runtime/frame`，随后订阅 `/api/runtime/events`。断线会自动重连并恢复当前权威帧。

## 已部署的 RDK X5 现场链路

当前板端地址为 `172.20.11.156`，系统为 Ubuntu 22.04.5/aarch64，HDMI 为 800×480，浏览器为 Firefox 141。板端登录后会通过 XFCE autostart 打开：

```text
http://127.0.0.1:18765/display.html
```

`18765` 是 PC 发起的 SSH reverse tunnel，映射到 PC 的 `127.0.0.1:8765`。这样即使当前 Wi‑Fi 阻止板端访问 PC 的入站端口，RDK 仍然读取 PC 的同一权威帧。PC 端 `scripts/run_v4_stack.ps1` 会自动启动服务和断线重连通道；密钥位于用户 SSH 配置目录，不写入项目。

板端部署文件：

- `/home/sunrise/tactile-v4/rdk_launch_display.sh`
- `/home/sunrise/.config/autostart/tactile-v4-display.desktop`

板端只显示 `/display.html`，不运行模型、检测、编译器或 synthetic frame。

## 验证

1. PC 上传图片并生成；
2. 页面显示模型候选和真实状态；
3. 旧帧先落下，新帧连续升起；
4. PC/RDK 显示相同 frame id、checksum、80×48 UInt8 表面；
5. PC 点击凸点可以命中模型输出的区域并语音说明；
6. “全部落下”和“重新升起”在两个页面同步。

## 当前运行信息

- RDK：`172.20.11.156`，用户 `sunrise`；
- PC：当前 WLAN 地址由服务启动日志打印，默认端口 `8765`；
- 当前验收帧：`Frame #3`，checksum `sha256:2c29e427ac07cc6607619e5818405f5eefce09965ad6776990c76edd15f83225`；
- Windows 登录自启入口：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\tactile-v4-autostart.cmd`。
