# 相机适配层与 FLIR 兼容说明

源码 0.15.0 引入通用相机适配层。现有 GitHub 安装包为 0.14.1，不包含本次采集后端改造；新后端可从源码运行。

## 当前支持范围

- 生产后端为 `spinnaker`，使用官方 Spinnaker／PySpin 4.4 系列。当前本机环境为 4.4.0.246。
- 扫描所有 SDK 可见设备；不按型号或 USB3/GigE 接口筛选。只发现一台时自动连接，多台时必须在列表选择或指定序列号，序列号不存在则报错，不回退到其他设备。
- 当前 Blackfly S BFS-U3-51S5C-C 已完成实机回归。其他型号与 GigE 连接尚未实测，需要实际设备验证，不能把统一 SDK 接口等同于所有型号已通过验证。
- 不接入模拟相机，也不在连接失败时回退；无设备使用主程序打开图像离线分析。

## 分层边界

| 位置 | 职责 |
| --- | --- |
| `twodviewer/acquisition.py` | Qt QThread、原有命令接口和信号、异常通知。 |
| `twodviewer/discovery.py` | 异步发现，通过同一后端入口扫描设备。 |
| `twodviewer/camera_backends/base.py` | 不依赖 Qt 的 CameraAdapter Protocol 与 CameraCallbacks。 |
| `twodviewer/camera_backends/__init__.py` | 显式后端创建与发现入口；未知 ID 报错。 |
| `twodviewer/camera_backends/spinnaker.py` | SDK 生命周期、设备选择、能力快照、参数命令、方案／回读／恢复、采集和遥测。 |
| `twodviewer/camera_features.py` | GenICam 标量与选择器采样、依赖顺序写入及恢复。 |
| `twodviewer/models.py` | 原有 Frame／LatestBuffer，供处理、标注和保存复用。 |

SDK 对象只在其工作线程中使用；初始化失败也释放已获取资源。帧必须在 image.Release 前复制，并设为只读；传出完整配置快照、代次、时间戳及可用逐帧 chunk 数据。界面和图像处理不直接接触 SDK 对象。

新增后端时实现 CameraAdapter 契约并在创建／发现入口注册；沿用 configure_many、configure_profile、configure_context、request_snapshot、request_stop 和回调事件。未来其他厂商后端仍需独立实现和驱动安装，本次没有新增其他厂商支持或 GUI 后端选项。

## 像素格式与能力协商

保留相机当前已支持的格式，不为通用化强制切换现有 BFS 的 BayerRG8。当前可直接采集未打包 BayerRG/GR/GB/BG 的 8／16 位、Mono8／Mono16、RGB8／BGR8（包括 SDK 的 Packed 名称别名）。

当前格式不兼容时，在设备实际枚举的格式中优先选择同一 Bayer／Mono 族的未打包格式，例如 Mono12p 优先 Mono16、BayerBG12Packed 优先 BayerBG16；否则选择可用的 RGB／BGR／Bayer／Mono 格式。没有兼容格式则明确报错，不生成伪造图像。不在主机端自行猜测打包像素布局。

参数快照保留 numeric／enums／integers／booleans 字段，并提供可写状态。缺少或只读参数在界面禁用；黑白设备不假定存在白平衡、固定 ROI 不显示可用裁切操作。无独立帧率 Enable 节点但有可写帧率时可直接调整。遥测不为不存在的自动模式补造 Off 参数，避免污染保存方案。

保留原始方案 schema、相机型号／序列号、标定匹配和旧设置读取方式，没有迁移真实配置。跨相机方案仍沿用原确认和回读流程。应用自动模式后会重试依赖该模式才可写的参数（例如当前 BFS 的自动算法阻尼、测光参数），再恢复选择器；最终回读不一致必须报告并尝试恢复原方案。

## 验证

无需相机的回归测试：

```powershell
.\.venv-camera\Scripts\python.exe -m unittest discover -s tests -v
```

19 项契约／SDK 边界／Qt 界面测试覆盖：单台非 BFS、多台显式选择、序列号错误不回退、未打包格式选择、黑白及只读能力、缓冲区所有权、失败资源释放、工作线程接口、自动模式依赖重试和缺失参数的 UI／方案行为。测试使用 SDK 边界替身，不是应用运行时可选的模拟设备。

当前 BFS 实机：改造前后 2448×2048、BayerRG8、uint8 只读帧、枚举能力及完整配置保持一致，读取 2,152 条功能记录。19 项采集／控制回归覆盖曝光、增益、白平衡、持续自动／一次自动、640×480 ROI、BayerRG16／uint16、解码、异常命令、完整快照、物镜上下文和原方案恢复；10 项真实 GUI 检查覆盖扫描、连接、实际型号标题、控件、方案选择／回读、临时标定测量、绘制及断开。实机检查最终恢复原方案并回读确认；使用临时 GUI 配置，没有写入真实用户设置或保存实验照片。
