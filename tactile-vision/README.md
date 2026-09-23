# Tactile Vision V4

> 当前唯一有效计划：[通用多模态智能体触觉闭环 V4](./docs/通用多模态智能体触觉闭环_开发母计划_V4.md)

本项目把任意图片交给 `qwen3.8-max` 触觉智能体，由模型直接决定完整 `80×48` 高度矩阵和触摸区域矩阵。网页是最终触觉硬件的数字孪生；本地代码只校验、无损解码、渲染和传输，不通过传统视觉算法重新决定点位。

## 启动

Windows 双击 `start_multimodal.bat`，或在项目目录运行：

```powershell
python app/main.py --host 0.0.0.0 --port 8765
```

- PC 产品页：`http://127.0.0.1:8765/`
- RDK 展示页：`http://PC_IP:8765/display.html`

模型和 TTS 配置从未提交的 `config.env` 读取。不要把密钥放入浏览器代码或文档。

## 当前产品主链

```text
图片 → 模型 manifest → 模型分块提交全部 3840 点
→ 机械校验 → 模型自检实际预览 → 发布权威帧
→ PC/RDK 同帧升降 → 触摸语音与连续问答
```

模型提交可使用逐行无损 RLE；发布时始终展开为完整 48 行 × 80 列 UInt8 高度和区域矩阵。最多三个完整候选，错误会精确返回给模型，失败不覆盖最近合法帧。

最后一次合法帧和对应原图保存在 `output/runtime/current_frame.json`。服务或 RDK 重连后仍读取同一个 frame id/checksum；该运行文件包含用户当前原图，不应提交或对外公开。

当前本地业务空间使用既有 OpenAI-compatible 多模态地址调用同一个 `qwen3.8-max`，避开原生 OSS 上传链路波动。模型、密钥与服务地址没有改变。

## 测试

```powershell
python -m pytest -q
python app/main.py --check
node --check renderer/web/app.js
node --check renderer/web/display.js
```

当前默认端口保持 `8765`。`GET /api/live-frame` 仅用于旧客户端读取快照，不再允许外部覆盖模型权威帧。`GET /api/runtime/image` 返回当前权威帧对应原图，供 PC 产品页重载恢复并列展示。
