# 官方相机依赖

此目录保留依赖许可证；官方二进制安装文件由开发者自行准备，不提交到 GitHub。

1. 从 [Teledyne FLIR 官方 Spinnaker SDK 页面](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/) 获取并安装 Spinnaker SDK 4.4.0.246 x64 和 USB3 驱动，先确认 SpinView 能连接相机。
2. 获取同版本的官方 Python 3.12 x64 wheel：`spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl`。将其放到本目录，再运行上一级的 `setup.ps1`；也可以通过脚本的 `-PySpinWheel` 参数指定文件位置。
3. 不要用 PyPI 上同名的 PySpin 包代替官方绑定。SDK、wheel、Python 版本和架构必须匹配。

只有构建离线安装包时，才需要额外提供完整的 `SpinnakerSDK_FULL_4.4.0.246_x64.exe`，详见项目根目录的 `packaging/README.md`。
