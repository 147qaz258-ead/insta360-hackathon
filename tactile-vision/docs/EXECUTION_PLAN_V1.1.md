# 视障触觉图像系统｜开发执行计划 V1.1（已废止）

> **废止通知（2026-09-23）：** Sobel/边缘优先路线不再是产品主链。当前唯一有效计划为 [通用多模态智能体触觉闭环_开发母计划_V4.md](./通用多模态智能体触觉闭环_开发母计划_V4.md)。本文件仅保留历史记录。

状态：执行中  
基线日期：2026-09-22  
当前里程碑：M1「真实图片 → 双设备 TactileFrame → 虚拟凸点预览」

## 1. 本轮目标

建立一条可重复运行、可测试、可部署到 RDK X5 的软件闭环：

```text
Image
  → Baseline Structural Extraction
  → Device-independent Structural Map
  → DeviceProfile
  → Aspect-preserving Rasterizer
  → TactileFrame V1
  → RDK / ESP32 Renderer
```

本轮不制作最终机械凸点屏，不引入语义删减，不让 Agent 决定保留哪些对象。

## 2. 已锁定的工程决策

1. 触觉画布默认横向，长边为 X 轴。
2. 显示器像素分辨率、触觉逻辑分辨率、未来物理针阵分辨率彼此独立。
3. 原图完整进入系统；使用 `fit + padding`，禁止拉伸和默认裁剪。
4. 先产生设备无关的结构图，再针对每个 DeviceProfile 独立栅格化；禁止先固定生成 80×48 再把它缩成其他设备尺寸。
5. V1 只支持二值高度：`0 = 落下`、`1 = 升起`。
6. ESP32 不运行 AI，只接收、校验并渲染 TactileFrame。
7. Agent 是后置优化器；关闭 Agent 时完整链路必须正常工作。

## 3. 已确认的真实硬件基线

### RDK X5

- 型号：D-Robotics RDK X5 V1.0
- 系统：Ubuntu 22.04.5 LTS / aarch64
- 内核：Linux 6.1.83
- 内存：3.0 GiB，无 Swap
- 系统盘：14 GiB，当前约剩余 2.2 GiB
- 当前 HDMI：800×480，比例 5:3
- Python：3.10.12
- 已有：OpenCV、NumPy、`hobot_dnn`、`hobot_vio`
- Display API：`from hobot_vio import libsrcampy`
- BPU Runtime：已通过官方 YOLO11-Seg 样例实测

因此首个 RDK profile 固定为屏幕 800×480、目标点阵 80×48。系统仍保留动态 profile 能力，换屏后只修改配置。

### ESP32

当前计划文件暂按 V1.0 中的 4.3 英寸 800×480 配置。接入实物后必须读取实际像素尺寸和横竖方向，再更新 profile；算法层不得依赖该临时值。

## 4. 里程碑和阶段 Gate

### M0：契约与自适应点阵（已基本完成）

交付物：

- `DeviceProfile`
- 自动点阵求解器
- `TactileFrame V1`
- 不裁剪、不拉伸的 Rasterizer
- Synthetic structural maps

Gate：

- 800×480 / 3840 点预算稳定解析为 80×48。
- 4:3、5:3、16:9 输入均保持比例和边界位置。
- 同一结构图可针对两个 profile 独立生成帧。

### M1：真实图片 Baseline（当前执行）

交付物：

- 图片读取与完整缩放
- 灰度、平滑、Sobel/Canny 基线
- 形态学闭运算和小碎片清理
- 真实图片 CLI
- 原图、结构图、TactileFrame 调试输出

Gate：

- 任意 JPEG/PNG 可以生成合法 TactileFrame。
- 原图边界和长宽比例不丢失。
- 同一输入能生成 RDK 和 ESP32 两个 profile 的帧。
- 自动测试全部通过。

### M2：双终端传输和展示

交付物：

- 带版本、尺寸、payload 长度和 CRC32 的二进制帧协议
- RDK HDMI Renderer
- ESP32 Serial/Wi-Fi transport adapter
- 单材质 3D 虚拟凸点 Renderer

Gate：

- 帧序列化后可无损还原。
- 损坏 payload 必须被 CRC 拒绝。
- RDK 800×480 显示完整点阵，无拉伸和裁切。
- ESP32 与 RDK 的对象相对位置一致。

### M3：多源结构与质量指标

交付物：

- LSD 线段
- Deep Edge backend 接口及 DexiNed 实现
- Segmentation boundary backend 接口及 RDK YOLO11-Seg 实现
- Structure Fusion
- coverage、continuity、fragmentation、density、aspect error

Gate：

- 每个 backend 可独立启停。
- segmentation 使用全部 mask 边界，不按语义删除对象。
- 20～30 张固定数据集可以批量回归并输出指标。

### M4：Compiler Agent

前置条件：M3 指标稳定后才开始。

交付物：最多 2 次重编译的参数优化闭环。Agent 只能输出 CompilerConfig，不得输出对象删除指令。

## 5. 当前任务板

| ID | 优先级 | 任务 | 依赖 | 完成条件 |
|---|---|---|---|---|
| T01 | P0 | 复核并固化 DeviceProfile | 无 | RDK 实机 profile 与 800×480 一致 |
| T02 | P0 | 改造 coverage-aware Rasterizer | T01 | 缩小时 1 像素结构线不因采样点错位而消失 |
| T03 | P0 | TactileFrame binary protocol | T01 | round-trip 与 CRC 测试通过 |
| T04 | P0 | 真实图片 Baseline | T02 | JPEG/PNG → structural map |
| T05 | P0 | CLI 双 profile 输出 | T03、T04 | 一次输入生成两个 frame |
| T06 | P0 | RDK 板端部署 | T05 | 板端 CLI 编译成功 |
| T07 | P0 | RDK HDMI 输出 | T06 | 800×480 实屏显示成功 |
| T08 | P1 | ESP32 transport | T03 | 可接收并校验一帧 |
| T09 | P1 | Metrics | T04 | 五项指标形成 JSON |
| T10 | P1 | Segmentation boundary | T06 | 官方 YOLO11 masks 转边界 |
| T11 | P1 | Fusion | T09、T10 | 权重配置可复现 |
| T12 | P2 | Agent Critic | T11 | 最多两次，无语义删除 |

## 6. 每次提交的 Definition of Done

- 不破坏完整性、比例保持和二值高度三条硬约束。
- 新逻辑有自动测试。
- PC 与 RDK 的核心数据契约一致。
- 不依赖公网才能运行核心闭环。
- 输出错误必须可诊断，不静默吞掉损坏帧或缺失模型。
- 调试产物写入 `output/`，不得污染源码目录。

## 7. 当前风险与处理

| 风险 | 处理 |
|---|---|
| RDK 系统盘只剩约 2.2 GiB | 不复制大型模型；复用 `/app` 官方模型；输出定期清理 |
| PC 默认 Python 环境损坏 | 核心模块保持纯 Python；使用可用运行时执行测试 |
| Three.js 当前存在 CDN fallback | RDK 离线部署前固定 vendor 文件 |
| 最近邻缩放会漏掉细线 | M1 改成覆盖率感知的区域采样 |
| ESP32 实际屏幕参数未确认 | profile 配置化；接入实机后只改配置并跑同一组测试 |

## 8. 演示完成标准

输入一张包含人物、桌子、物品、植物和背景结构的图片，系统同时给出：

1. 原图；
2. Raw Structural Map；
3. Simplified Structural Map；
4. RDK TactileFrame；
5. ESP32 TactileFrame；
6. 单材质虚拟凸点画面。

两个设备的点阵数可以不同，但内容比例、左右上下关系和整体结构必须一致。
