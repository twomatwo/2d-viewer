# 2D Viewer Windows 安装包构建

本目录供开发电脑构建发布包。实验室用户只需要 `release` 中的安装包和《实验室安装与更新说明.html》，不需要 Python、源代码、虚拟环境或本目录的构建工具。

目标平台为 Windows 11 x64。程序用 PyInstaller 的目录模式打包，由 Inno Setup 制成单个安装 EXE；Python、PySide6、OpenCV、NumPy、tifffile 和官方 PySpin 随程序分发。官方 Spinnaker 安装包作为离线依赖随安装器提供。

## 构建

在项目根目录打开 PowerShell，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -SpinnakerInstaller '.\.build-tools\Spinnaker\SpinnakerSDK_FULL_4.4.0.246_x64.exe'
```

`-SpinnakerInstaller` 指向可信的官方 **Spinnaker SDK 4.4.0.246 x64 完整离线安装文件**（约 256 MiB），不能用系统 Package Cache 中只有约 1.5 MiB 的同名引导器。版本与当前正式程序使用的 PySpin 4.4.0.246 匹配，构建时检查文件版本和官方数字签名。

本机完整离线包已缓存于 `.build-tools\Spinnaker`，从旧版安装包恢复并通过 FLIR 数字签名及版本检查。换开发电脑时可提供该官方文件的其他路径。

当前开发电脑已经安装 Python 3.12、程序依赖、PyInstaller 6.22.3 和 Inno Setup 6.7.3。`build.ps1` 默认使用 `.venv-camera\Scripts\python.exe` 和 `.build-tools\InnoSetup\ISCC.exe`；也可用 `-Python` 和 `-ISCC` 指定其他路径（该 Python 环境须已安装程序依赖及 PyInstaller）。脚本不自动下载或安装构建工具。在其他开发电脑准备 PyInstaller 可运行 `python -m pip install pyinstaller==6.22.3`，Inno Setup 从 https://jrsoftware.org/isdl.php 安装。首次准备构建工具可能需要联网；成品在实验室电脑上可离线安装。

默认构建完成会删除 `.package-build` 临时目录；只有调查打包问题时使用 `-KeepBuild` 保留。实验室更新程序只需运行新版安装器，不需要在实验室执行本构建脚本。

应用版本取自 `twodviewer/__init__.py`。发布新版前先更新该版本，并保持安装器的 **AppId 固定**，让新版识别并覆盖现有安装。

最终交付目录为项目根目录下的 `release`。当前交付文件为：

- `2D-Viewer-Setup-0.14.1-x64.exe`
- `实验室安装与更新说明.html`
- `SHA256SUMS.txt`（安装包校验值，可选保留）

安装包和 HTML 说明一起交付即可。构建工作目录、Python 虚拟环境和旧版构建输出不属于交付内容。新版本的文件名随应用版本变化。

## 安装和升级约定

| 内容 | 安装版路径 |
| --- | --- |
| 程序文件 | `%LOCALAPPDATA%\Programs\2D Viewer` |
| 设置和标定 | `%LOCALAPPDATA%\LabView\settings\twodviewer.json`（已有 labview.json 继续使用） |
| 默认照片与录像 | `%LOCALAPPDATA%\LabView\captures` |
| 日志 | `%LOCALAPPDATA%\LabView\logs` |

0.10.2 将原 LabView 更名为 2D Viewer。新装使用上表的 `Programs\2D Viewer`，覆盖旧版沿用原安装位置（通常是 `Programs\LabView`）。安装器 AppId 不变，更新安装项和快捷方式名称，移除本程序的旧 EXE；清理旧快捷方式前核对目标，避免影响其他同名软件。`%LOCALAPPDATA%\LabView`、`labview.json`、文件格式标识保留，Python 包现为 `twodviewer`，以继续读取旧设置、标定、相机方案和照片。项目目录名称不影响程序显示名称。

安装按当前 Windows 用户进行。相机驱动和系统运行库可能需要管理员权限。安装器检查匹配的 Spinnaker 运行库和 USB3 驱动，已安装时跳过；缺少时运行随包提供的官方安装程序。

后续更新由同一 Windows 用户关闭 2D Viewer 后直接运行新版安装器完成，不要求先卸载。升级和卸载均保留用户数据；自选拍摄目录也不能被移动或清除。打包时不要把开发机的 `settings`、用户照片、日志、旧环境或测试产物混进程序资源。

官方相机驱动按系统范围安装，而 2D Viewer 数据按 Windows 用户隔离。不要将相机驱动卸载绑定到 2D Viewer 卸载流程。

## 发布检查

发布前检查以下实际行为，临时验证文件用完后清理：

1. 打包程序能在独立位置启动，不依赖开发机源码路径或原虚拟环境；无相机时仍能打开已有图像。
2. 真实相机连接、显示、参数读写及 PNG／TIFF 保存正常。
3. 安装、再次安装和覆盖更新后，设置与标定仍在；用户数据只写入上述数据目录或用户选择的保存目录。
4. 已安装匹配驱动时不重复安装；缺少驱动时能进入官方安装流程。
5. 《实验室安装与更新说明.html》与实际构建文件名、安装流程及数据路径一致。

当前开发机的通过记录不能替代实验室电脑首次安装时的连接检查。

## 源码版迁移

新源码版使用 `data/settings/twodviewer.json`；旧源码版使用 `LabView-App/settings/labview.json`。安装版首次启动并关闭后，可在备份现有数据的前提下将旧设置复制到 `%LOCALAPPDATA%\LabView\settings\twodviewer.json`（已有 labview.json 继续使用）。检查其中保存的拍摄目录，避免保留另一台电脑不存在的盘符。照片由用户自行复制或继续使用原目录。

标定与相机、采样和实际光路有关；数据迁移不代表新配置已完成标定。实验室操作步骤见同目录 HTML 文档。


## GitHub 发布

0.14.1 安装包、HTML 安装说明和 SHA256SUMS.txt 发布于 [GitHub Releases](https://github.com/twomatwo/2d-viewer/releases/tag/v0.14.1)，二进制不提交到 main 源码目录。

Qt 6.12 在本项目目标 Windows 11 上使用系统 ICU。构建规格会排除宿主 PATH 中的同名 icuuc.dll 及 icudt 数据 DLL，避免误收集 Poppler 依赖导致 QtCore 加载失败。发布前须验证实际成品 EXE 离线启动，并使用打包归档与原生库验证主要 GUI／导出路径，不能仅测试源码。
