# 官方相机依赖

1. 从 [Teledyne FLIR 官方 Spinnaker SDK 页面](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/) 获取并安装 Spinnaker SDK 4.4.0.246 x64 和 USB3 驱动，先确认 SpinView 能连接相机。
2. 获取匹配 Python 3.12 x64 的官方 wheel：`spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl`。放到仓库根目录的 `dependencies/` 后运行根目录 `setup.ps1`，或用 `-PySpinWheel` 指定其他位置。`dependencies/` 已由 Git 忽略。本机旧 `LabView-App/install/` wheel 继续作为默认路径的兼容回退。
3. 不要用 PyPI 上同名 PySpin 包代替官方绑定。SDK、wheel、Python 版本和架构必须匹配。明确指定的 wheel 路径无效时会报错，不替换为其他文件。

SDK 许可证保存在 [licenses/Spinnaker](licenses/Spinnaker/)，官方二进制文件不上传 GitHub。只有构建离线安装包时，才需要额外提供完整 SDK 安装 EXE，详见 [打包说明](../packaging/README.md)。

源码 0.15.0 使用通用 Spinnaker 后端，不按 BFS 型号筛选。其他 SDK 可见 FLIR 设备的具体能力与像素格式仍需实机验证；详见 [相机适配说明](camera-backends.md)。当前安装包仍为 0.14.1。
