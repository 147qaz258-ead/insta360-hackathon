# Development Status · V4

权威计划：`docs/通用多模态智能体触觉闭环_开发母计划_V4.md`

## 已实现

- [x] 固定 `80×48 / 3840 pin / UInt8 0..255` 硬件契约；
- [x] 模型权威 `TactileFrameV2`、区域矩阵、SHA-256 checksum；
- [x] 原始二维数组与逐行无损 RLE 的纯机械验证/展开；
- [x] 模型 manifest + 模型逐行点位提交，不经过本地语义栅格化；
- [x] 最多三个完整候选、协议反馈、实际预览和模型自检；
- [x] 异步 run API、run SSE、统一 runtime snapshot/SSE；
- [x] PC 单页产品体验与 RDK 只读同帧展示；
- [x] 已接通实体 RDK X5（172.20.11.156）：HDMI 800×480 Firefox kiosk、XFCE 登录自启、PC→RDK SSH reverse tunnel、同帧 checksum 校验；
- [x] RDK 默认正投影平视；`height>0` 的升起点统一白色，`height=0` 的落下点统一黑色；0–255 实际高度继续由挤出、边缘和中性阴影表达；
- [x] `80×48` 点阵按 `800×480` 屏幕精确铺满，每个 pin 固定对应 `10×10` 像素单元，无边距、无裁切、无比例拉伸；
- [x] PC 产品页直接展示模型 `height_rows/region_rows` 的高度矩阵与区域边界，不生成第二份硬件帧；
- [x] 连续高度升降、落下、重放；
- [x] 模型区域矩阵触摸命中、连续问答、Qwen TTS 尝试和浏览器降级；
- [x] UInt8 CRC32 二进制帧协议；
- [x] 自动化协议、智能体循环、预览、旧功能回归测试；
- [x] 最后合法权威帧与原图落盘，服务重启后按 checksum 原样恢复；
- [x] 原生 OSS/网关异常时可切换同一 `qwen3.8-max` 的 OpenAI-compatible 多模态通道；
- [x] 追问 API 暂时不可用时，明确降级到模型预生成区域短句，不在本地编造新语义。

## 真实 API 联调状态（2026-09-23 复验）

- 真实闭环已跑通：客厅样例图 → `qwen3.8-max` → 候选 1 协议错误精确反馈 → 模型自行修复 → 候选 2 通过校验并自检接受 → 发布 `frame_id/checksum`，全程 3840 高度均来自模型提交的同一候选（trace 留档于 `output/real_loop_*.json`）；
- 分块行协议 `{row,height_rle,region_rle}` 真实可用；模型误输出会被验证器精确拒绝且不会发布；
- 首次复验候选 1 曾出现 230 个 `unowned_raised_pin`；已在分块级机械校验中加入"升起点必须有已定义区域 id / 不得引用未定义区域"反馈，同类错误降为 0；
- 分块重试由 2 次提高到 3 次（仍属同一次运行，不占三个候选名额）；
- 针对工作区网关的 TLS 抖动：调用级瞬时网络错误重试（3 次退避）、OSS 上传策略过期自动刷新重试、上传超时收紧到 90 秒；
- 浏览器产品页端到端实测通过：IDLE→ANALYZING→协议反馈→候选推进→SELF_REVIEW→READY、区域清单、语音概览、点击凸点命中 `region_rows` 并返回区域语音；
- 最新真实运行 `018f269ee682419f8b7761baf3b64dc3` 的候选 1 机械校验通过，模型查看实际预览后自主 `accept`；
- 最新中文产品帧已发布为 Frame #3：`3840` 点、`3064` 个升起点、`9` 种实际高度、`7` 个中文语义区域，模型查看真实预览后自主 `accept`；checksum 为 `sha256:2c29e427ac07cc6607619e5818405f5eefce09965ad6776990c76edd15f83225`；
- 已验证落下 `RETRACTING→IDLE`、重放 `RISING→READY` 全过程 frame id/checksum 不变；
- 已验证服务重启后 Frame #2、3840 点、checksum 与 2,232,406 字节原图完整恢复；
- 区域触摸已命中模型 `region_rows`；TTS 当前未返回音频，浏览器语音降级正常；复杂追问遇到网关超时时会明确使用模型预生成区域说明降级；
- 页面真实验收通过：原图重载恢复、落下/升起、凸点坐标命中、两轮连续中文追问、新运行会话清空、PC/RDK 自动切换到同一 Frame #3；
- 自动化测试：`60 passed`；
- 待完成：通用回归集（人物/动物/街景/图表/地图/商品）；产品页 2D 高度/区域矩阵已加入主界面，仍需用更多真实图片做可辨性回归。

## 不再属于产品主链

- DeviceCompiler 从 bbox/轮廓生成最终 pin；
- 固定 GroundedScene、YOLO、SAM、Edge、Depth；
- 浏览器 Sobel 或 synthetic frame 冒充模型输出；
- 二值或 0/1/2/3 固定高度。

## 后续硬件联调

实体 RDK 已完成首次部署；后续 PC 与 RDK 继续使用同一 frame id/checksum。若现场网络允许 PC 入站 `8765/TCP`，可将 RDK URL 直接切换为 `http://PC_IP:8765/display.html`，无需改变页面或帧协议。
