# 视障触觉图像系统｜开发与验证母计划 V1.0（已废止）

> **废止通知（2026-09-22）：** 当前唯一有效计划为 [`tactile-vision/docs/通用多模态智能体触觉闭环_开发母计划_V4.md`](./tactile-vision/docs/通用多模态智能体触觉闭环_开发母计划_V4.md)。本文件仅保留历史记录，不得继续作为实现依据。

> 目标：从现在开始进入正式开发。第一原则不是先做漂亮 UI，也不是先把所有模型接上，而是先把 **Image → Tactile Compiler → TactileFrame → 虚拟凸点阵列 → RDK HDMI** 整条链路打通，再逐层替换和增强算法。

---

## 0. 当前开发基线

当前本地工作区：`E:\C_Projects\影石黑客松`

当前目录已有：
- Insta360 CameraSDK / MediaSDK
- Link SDK
- `link2-web-controller`
- `视障多感官影像产品说明文档_V0.2_纯Markdown.md`

当前还没有正式建立 `tactile-vision/` 工程。

注意：旧产品说明 V0.2 中“只识别 3～5 个主要对象”“4×4 / 6×6 / 8×8”属于旧原型路线，和现在“图像完整性优先 / 自适应点阵 / 不按语义删对象”的开发方向冲突。后续开发以本计划和最新 Tactile Compiler V1 路线为准。

---

# 1. 第一目标：先打通完整链路

第一条必须跑通的链路：

```text
Input Image
    ↓
Image Normalizer
    ↓
Baseline Structural Extractor
    ↓
Structure Cleanup
    ↓
Adaptive Rasterizer
    ↓
TactileFrame
    ↓
Virtual Pin Renderer
    ↓
Browser / RDK HDMI
```

这一条链路必须在没有 DexiNed、SAM、VLM、Agent 的情况下也能工作。

第一阶段验收不是“算法最好”，而是：

> 给任意一张普通图片，系统一定能输出一张保持原始空间关系和宽高比的虚拟触觉点阵图。

---

# 2. 工程目录

建立独立工程：

```text
tactile-vision/
│
├── app/
│   ├── main.py
│   ├── config.py
│   └── server.py
│
├── compiler/
│   ├── compiler.py
│   ├── fusion.py
│   ├── metrics.py
│   └── profiles.py
│
├── vision/
│   ├── base.py
│   ├── baseline.py
│   ├── edges/
│   ├── segmentation/
│   └── geometry/
│
├── tactile/
│   ├── frame.py
│   ├── rasterizer.py
│   ├── simplify.py
│   └── protocol.py
│
├── renderer/
│   ├── web/
│   │   ├── index.html
│   │   ├── app.js
│   │   ├── pin_renderer.js
│   │   └── ui.css
│   └── debug_renderer.py
│
├── devices/
│   ├── profiles/
│   │   ├── rdk_hdmi.json
│   │   └── esp32_43.json
│   ├── rdk/
│   └── esp32/
│
├── agent/
│   ├── critic.py
│   └── loop.py
│
├── samples/
├── output/
├── tests/
└── README.md
```

CameraSDK / Link SDK 不直接塞进核心编译器。后面只作为 Input Adapter 接入。

---

# 3. 数据协议先锁死

## 3.1 DeviceProfile

示例：

```json
{
  "name": "rdk_hdmi",
  "screen_px_width": 800,
  "screen_px_height": 480,
  "target_pin_count": 3840,
  "aspect_mode": "preserve",
  "height_levels": 2
}
```

ESP32 第一版：

```json
{
  "name": "esp32_43",
  "screen_px_width": 800,
  "screen_px_height": 480,
  "target_pin_count": 3840,
  "aspect_mode": "preserve",
  "height_levels": 2
}
```

不固定写死 64×64。

## 3.2 TactileFrame

```json
{
  "version": 1,
  "cols": 80,
  "rows": 48,
  "levels": 2,
  "aspect_ratio": 1.6667,
  "pins": []
}
```

V1 只允许：

```text
0 = 未凸起
1 = 凸起
```

---

# 4. 虚拟凸点页面：正式视觉定义

## 4.1 我们要模拟的不是“黑白点阵图”

我们要模拟一块真实存在的可刷新触觉表面。

视觉方向：

- 一整块浅灰 / 银灰底板；
- 全部触点是统一银色金属；
- 每个触点大小相同；
- 每个触点间距相同；
- 每个触点材质相同；
- 不用颜色表示信息；
- 唯一核心变量是 **高度**；
- 0 状态接近底板；
- 1 状态从底板凸起；
- 斜视角能清楚看到高度；
- 顶视角能清楚看到图形整体结构。

这就是页面里的“数字孪生触觉板”。

## 4.2 点的几何造型

不要下载几千个独立 3D 模型。

程序生成一个 Pin Primitive，再用 GPU Instancing 重复。

单个 pin：

```text
短圆柱
+
圆润顶帽 / 半球帽
```

近似：

```text
      ___
    /     \
   |       |
   |       |
---|-------|--- base plate
```

推荐：
- 圆柱 8～12 个 radial segments 足够；
- 顶部圆润；
- 单个模型低面数；
- 80×48 = 3840 个实例；
- 使用 Three.js `InstancedMesh`；
- 不为每根针创建独立 Mesh。

## 4.3 材质

统一材质：

```text
Metallic silver
metalness ≈ 0.7～0.9
roughness ≈ 0.25～0.45
```

底板：
- 暖白 / 浅银灰；
- 不使用纯黑大背景；
- 细微粗糙度；
- 让凸点阴影能显出来。

注意：

> 活跃点和未活跃点不能靠颜色区分。

它们必须是同一个材质，只靠 Z/Y 轴高度变化。

## 4.4 灯光

至少：
- 1 个大面积主光；
- 1 个弱环境光；
- Soft Shadow / Contact Shadow。

目的不是“炫 3D”，而是让高度一眼可见。

## 4.5 相机

默认：
- 25°～35° 俯视；
- 轻微透视；
- 整块点阵始终完整显示。

开发模式提供：
- Perspective
- Top View

两个视角。

---

# 5. 页面结构

V1 不做复杂 UI。

## Demo Mode

全屏只显示：

```text
┌──────────────────────────────────────────┐
│                                          │
│          Virtual Tactile Surface         │
│                                          │
│           银色三维凸点阵列                │
│                                          │
└──────────────────────────────────────────┘
```

不出现算法参数面板。

## Debug Mode

```text
┌────────────────────────────────────────────────────┐
│ Input | Device | 80×48 | Iteration | FPS          │
├───────────────────────────────┬────────────────────┤
│                               │ Original           │
│      3D Tactile Surface       │ Raw Structural     │
│                               │ Simplified         │
│                               │ Tactile Binary     │
├───────────────────────────────┴────────────────────┤
│ coverage / continuity / fragments / density       │
└────────────────────────────────────────────────────┘
```

Debug 面板可收起。

页面不是产品 UI，它是开发工具 + Demo Renderer。

---

# 6. 开发阶段

## P0：空算法也要先跑通全链路

输入先不用真实视觉算法。

准备三个 synthetic structural maps：
- 圆；
- 矩形 + 对角线；
- 两个分离对象。

完成：

```text
synthetic map
→ Rasterizer
→ TactileFrame
→ Web Renderer
```

验收：
- 页面出现完整 80×48 pin board；
- 数量正确；
- 点阵比例正确；
- 同一材质；
- 0/1 只有高度差；
- 图形位置正确。

这一步一旦完成，说明“数据链路 + 页面链路”已经通。

---

## P1：普通图片 Baseline

实现：

```text
Image
↓
Aspect-preserving normalize
↓
Bilateral / edge-preserving smoothing
↓
Canny + Scharr
↓
Morphology
↓
Skeleton
↓
Adaptive Rasterizer
↓
TactileFrame
```

验收：
- 输入普通 JPG / PNG；
- 不中心裁剪；
- 不拉伸；
- 保持原始长宽比；
- 输出 Original / Raw / Simplified / Tactile 四张 debug 图；
- 3D pin board 同时刷新。

---

## P2：结构层简化

加入：
- connected component filtering；
- skeletonization；
- Douglas-Peucker；
- line cleanup；
- morphology。

原则：

> 删除碎纹理，不删除语义对象。

验收：
- 人物轮廓不能因为简化整个消失；
- 建筑整体边界保留；
- 树可以减少叶片纹理，但树整体不能被删掉；
- 同一批测试图修改前后可比较。

---

## P3：多源结构提取

按接口增加：

```text
Deep Edge
Segmentation Boundary
Line Segment
Classical Edge
```

优先：
- DexiNed；
- segmentation boundary；
- OpenCV LSD；
- Canny/Scharr。

然后：

```text
Structure Fusion
→ StructuralMap
```

所有模块都必须可以单独开关。

Baseline 仍然保留，不能删。

---

## P4：Metrics

每次 compile 输出：

```json
{
  "coverage": 0,
  "continuity": 0,
  "fragment_count": 0,
  "active_pin_ratio": 0,
  "aspect_error": 0
}
```

第一阶段重点不是分数绝对值，而是让每次算法调整可以客观比较。

---

## P5：Agent Critic

Agent 最后接。

输入：
- Original；
- StructuralMap；
- SimplifiedMap；
- Tactile Preview；
- Metrics。

输出只能是：

```text
CompilerConfig
```

Agent 不允许：
- remove person；
- remove plant；
- keep dog；
- 删除背景对象。

最多 2～3 次 recompile。

---

## P6：RDK X5 部署

先部署不依赖 BPU 的完整 CPU 链路。

目标命令：

```bash
python main.py --input samples/photo.jpg --device rdk_hdmi
```

结果：
- RDK 本地编译图片；
- TactileFrame 生成；
- 本地 Renderer 页面刷新；
- HDMI 800×480 全屏展示。

之后再逐个替换：
- segmentation；
- deep edge；

为 RDK/BPU 版本。

禁止一开始把整个工程绑定 BPU。

---

## P7：真实相机输入

等静态图片链路稳定后，再接现有 Insta360 / Link SDK。

接口只做：

```text
Camera Frame
→ InputAdapter
→ ndarray / image
→ Compiler
```

Camera SDK 不能侵入 compiler。

这样以后：
- 文件；
- 摄像头；
- 手机；
- API；

都只是不同 Input Adapter。

---

## P8：ESP32

RDK / PC 输出统一 TactileFrame。

```text
TactileFrame
↓
JSON / Binary
↓
Wi-Fi / Serial
↓
ESP32
↓
4.3" virtual pin renderer
```

ESP32 不跑 AI。

---

# 7. 验证路径

## 7.1 链路验证

每一层必须可以单测：

```text
Input
Normalizer
Extractor
Simplifier
Rasterizer
Frame
Renderer
```

禁止只有 `main.py` 一跑才知道有没有问题。

## 7.2 几何验证

自动测试：
- aspect_error = 0；
- no crop；
- padding 正确；
- 原图左边对象输出仍在左边；
- 原图上方对象输出仍在上方；
- 不发生镜像；
- 不发生 4:3 → 16:9 拉伸。

## 7.3 Renderer 验证

自动检查：
- instance_count == rows × cols；
- 所有 pin material 相同；
- 所有 pin diameter 相同；
- height 只有 0 / 1；
- frame 更新后不重建整个 scene。

## 7.4 算法验证集

固定准备 20～30 张图片：

- 单人物；
- 多人物；
- 动物；
- 单物体；
- 多物体；
- 室内；
- 建筑；
- 道路；
- 风景；
- 复杂背景；
- 近景；
- 远景；
- 遮挡。

所有算法版本都跑同一套。

## 7.5 Golden Output

每个测试图保存：

```text
original.png
raw_structural.png
simplified_structural.png
tactile_frame.json
tactile_preview.png
metrics.json
```

这样可以直接看“这次修改到底改善了哪一步”。

---

# 8. 第一轮开发任务拆分

第一轮只做六件事：

1. 创建 `tactile-vision/` 工程骨架；
2. 实现 DeviceProfile；
3. 实现 TactileFrame；
4. 实现 Adaptive Rasterizer；
5. 实现 Three.js Instanced Pin Renderer；
6. 用 synthetic map 跑通浏览器完整链路。

第一轮完成以后再接真实图片。

不要在第一轮：
- 接 YOLO；
- 接 SAM；
- 接 DexiNed；
- 接 Agent；
- 接相机；
- 接 ESP32。

---

# 9. 第一轮完成标准

启动：

```bash
python app/main.py --demo synthetic
```

然后浏览器看到：

- 一块完整银色触觉板；
- 80×48 左右的自适应点阵；
- 全部点相同颜色、相同材质、相同直径；
- 一部分点真实凸起；
- 能显示圆 / 线 / 矩形；
- 切换 TactileFrame 后点阵即时改变；
- 不靠颜色表达 0/1。

这一刻才算“底座完成”。

---

# 10. 当前最终原则

> 先把整条链路做成，再优化每一层。

> 页面里的点阵不是“黑白图片”，而是一块真实触觉设备的数字孪生。

> 凸点全部使用同一个银色金属材质，信息只通过几何高度表达。

> 视觉算法永远输出结构，不直接控制 UI。

> Renderer 永远只认识 TactileFrame，不认识 YOLO、SAM、DexiNed 或原图。
