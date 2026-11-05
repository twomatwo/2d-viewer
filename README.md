# 2D Viewer

Microscope application for 2D material research.

原名 LabView，自 0.10.2 起更名为 2D Viewer。新版安装包可覆盖更新旧版；设置、标定和相机方案继续使用原数据目录，无需手动迁移。

- **本机源码版**：双击根目录的 `Start 2D Viewer.cmd`。
- **离线查看和处理图片**：在主程序中点击“打开图像…”，无需连接相机。
- **实验室安装与更新**：复制 `release` 内的安装 EXE 和《实验室安装与更新说明.html》，双击安装包。已内置 Python 和程序依赖；相机运行库和驱动缺失时由安装器调用官方离线安装程序。

源码版的数据在 `LabView-App` 内；安装版的数据在 `%LOCALAPPDATA%\LabView`，覆盖更新会保留。以后关闭程序，运行新版安装包即可更新，无需先卸载。

`.venv-camera` 和 `.build-tools` 只供本机开发与打包，不需要复制到实验室。重新构建安装包的方法见 `packaging/README.md`。

从 GitHub 获取源码后，先按 [源码安装说明](LabView-App/README.md) 准备依赖。官方 PySpin wheel 和 Spinnaker SDK 安装文件不随源码仓库分发，准备方法见 [相机依赖说明](LabView-App/install/README.md)。本机设置、照片、日志及构建产物也不上传到 Git。
