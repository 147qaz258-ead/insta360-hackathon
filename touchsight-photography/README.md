# TouchSight · X5 视觉摄影智能体

面向视障用户的 AI 摄影 Agent：X5 一次快门捕获完整 360° 场景，Agent 理解用户意图与整个场景，主动取景、评估、比较，输出一张最好的二维照片。

## 链路

```
X5 --USB文件传输--> DCIM/Camera01 --监听--> input/
       --> PanoramaViewGenerator (360°拆成带 yaw/pitch/fov 元数据的二维视图)
       --> VLM 理解整个场景 --> Agent Loop (render_view 主动取景)
       --> Critic 五层评审 --> 候选比较 --> save_image
       --> runs/run_xxx/ (全过程存档 + trace.json)
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入真实 API key

# 1. 生成合成测试全景图并跑通投影验证
python tests/test_projection.py

# 2. Mock 模式验证 Agent 全链路（无需 API）
python tests/test_agent_loop.py

# 3. 真实跑一张已拼接的全景图（替换为自己的照片路径）
python main.py run input/your_panorama.jpg --intent "帮我们三个人拍一张合照，后面的塔也留下"

# 4. X5 联机：先拍照，再切换 USB 文件传输模式接电脑，监听 DCIM 自动处理
python main.py watch --dcim "F:\DCIM\Camera01" --intent "..."
# 或不接相机，明确监听本地 input/ 目录：
python main.py watch --dcim input --intent "..."

# 5. 路演 Dashboard（评委可视化，核心演示入口）
python -m touchsight.web.server   # http://127.0.0.1:8050
# - 实时展示 Agent 决策全过程（观察→思考→取景→评审→选片）
# - 360° 球面上标注每次取景框；候选对比；最终成片+选定理由
# - 上传 INSP 原片自动拼接；上传完成后自动裁剪构图，未填意图时使用默认摄影意图
# - 支持直接上传 JPG/JPEG/PNG 全景照片，无需连接相机
# - 「演示回放模式」仅验证工具链，不代表真实模型评审；实际作品请保持关闭
# - 「监听模式」：多目录监听、首次登记旧文件、8 秒聚合新照片后自动构图
```

INSP 拼接依赖官方 Insta360 MediaSDK：需自行安装，并将 `touchsight/capture/acquisition.py` 中的 `MEDIASDK_TEST` 改为本机可执行文件路径；SDK 二进制不包含在本仓库。使用已导出的 JPG/PNG 全景图无需 SDK。监听目录可在网页中修改，多个目录用分号分隔。

密钥放在本地 `.env`，不得提交；原片、运行成片与 `trace.json` 存在本机 `input/`、`runs/`，默认不上传 GitHub。单张严重运动模糊不能保证恢复，当前支持批次清晰度筛选、保守地平线校正和画质增强。

## 目录

```
touchsight/
├── capture/acquisition.py   # P0: DCIM 监听（X5 文件传输模式兜底）
├── panorama/views.py        # P1: 等距柱状→透视视图渲染器 + overview 生成
├── vlm/providers.py         # P2: VLM 抽象（OpenAI 兼容 / Mock）
├── agent/tools.py           # P3: 6 个工具（get_panorama/get_overview_views/render_view/inspect_images/save_image/ask_user）
├── agent/loop.py            # P4-P6: OBSERVE→THINK→ACT→SEE→EVALUATE 循环
└── storage/runs.py          # 运行存档 + trace.json（路演证据链）
skills/touchsight_photographer/
├── SKILL.md                 # 摄影师身份与工作方式
└── photography_rubric.md    # 五层摄影评审标准（意图/视觉组织/边缘背景/光色/技术）
```

## 验收口径（P0 采集）

X5 按一次快门 → 电脑端连续稳定 10 次得到 360° 图像文件并自动进入 input 目录。
SDK 联调失败时降级为 USB 文件传输模式 + DCIM 监听（已实现）。
