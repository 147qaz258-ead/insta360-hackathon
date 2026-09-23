# Insta360 Link 2 Web Controller

本项目在 Windows 本机运行：

- 浏览器通过 `getUserMedia` 获取 Link 2 的 UVC 视频画面。
- 本地 C++ 服务通过 Insta360 `UVCCamera.dll` 控制云台、AI 跟踪和自动构图。
- 服务只监听 `127.0.0.1:8765`，不会暴露到局域网。

## 构建

使用安装了“使用 C++ 的桌面开发”组件的 Visual Studio 2022：

```powershell
cmake -S . -B build -A x64
cmake --build build --config Release
```

仓库已包含黑客松 SDK 包中的 Windows x64 头文件、导入库和运行时 DLL。

## 运行

```powershell
.\build\Release\link2_web_controller.exe
```

随后访问：

```text
http://127.0.0.1:8765
```

第一次打开画面时，浏览器会询问摄像头权限。允许后页面会优先选择名称包含 `Insta360 Link 2` 的设备。

## 摄像头一直朝下

1. 确认 USB 设备已经重新连接，并关闭可能占用摄像头的会议或相机软件。
2. 打开本项目页面，确认右上状态显示设备在线。
3. 点击“修复镜头一直朝下”。服务会关闭跟踪、退出隐私模式、调用 `SwitchNormalMode()`，并循环等待设备重新枚举完成。
4. 如果仍朝下，先关闭“AI 跟踪”和“自动构图”，再点击“回正”。

云台方向按钮使用持续移动协议，因此按钮松开、窗口失焦或页面关闭时都会发送停止命令。
