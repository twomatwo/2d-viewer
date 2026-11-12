# 2D Viewer

面向二维材料研究的显微相机软件，支持 FLIR 相机采集、图像处理、标注和测量。

源码包统一命名为 `twodviewer`，位于仓库根目录。程序显示名称为 **2D Viewer**。当前源码与 Windows 安装版均为 **0.14.1**。安装包和安装说明见 [GitHub Releases](https://github.com/twomatwo/2d-viewer/releases/tag/v0.14.1)。

## 快速开始

- 本机源码版：双击根目录 `Start 2D Viewer.cmd`。
- 新电脑：按 [源码使用说明](docs/usage.md) 安装依赖，然后运行启动脚本。
- 离线使用：在主程序中打开图像，或在已配置环境中运行 `python -m twodviewer --no-connect`。
- 实验室安装：下载 [最新 Windows 安装包](https://github.com/twomatwo/2d-viewer/releases/download/v0.14.1/2D-Viewer-Setup-0.14.1-x64.exe)，无需安装 Python；构建方式见 [打包说明](packaging/README.md)。

## 仓库结构

```text
2d-viewer/
├── twodviewer/           # 唯一的 Python 程序源码
├── packaging/           # Windows 安装包构建及打包许可证
├── docs/                # 使用说明、相机依赖说明和 SDK 许可证
├── requirements.txt
├── setup.ps1
├── run.ps1
├── Start 2D Viewer.cmd
└── README.md
```

依赖和官方 PySpin wheel 的准备方式见 [相机依赖说明](docs/camera-dependencies.md)。第三方 SDK、Python 环境、设置、照片、恢复记录和安装包不上传到源码仓库。

新克隆的源码版默认将设置、照片、日志及恢复记录写入 `data/`。已有本机 `LabView-App` 数据目录继续识别，现有配置无需迁移；该旧目录已整体忽略，不再作为源码目录。

新配置名为 `settings/twodviewer.json`；已有 `settings/labview.json` 继续原位读取和更新。安装版继续沿用 `%LOCALAPPDATA%\LabView` 数据目录。为兼容旧设置、标定库和拍摄记录，旧序列化标识保留；安装器 AppId 不变，更新及卸载不删除用户数据。

开发与维护约束见 [开发说明](docs/development.md)。当前文档只保留有效操作，旧版本变更记录可查看 Git 历史。
