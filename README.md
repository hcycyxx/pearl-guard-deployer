# Pearl 守护器 V2.2.0

Windows 一键部署版。安装包内含 Pearl 守护器、SRBMiner 3.8.0、显卡型号探测、默认配置和快捷方式，目标电脑无需安装 Python。

## 下载

- [PearlGuard-V2.2.0-Setup.exe](dist/PearlGuard-V2.2.0-Setup.exe)
- SHA256：`68F3DAF42855E01D7DB0FF6EE7C3DAB32CBD685F8E30D9520FEFB4B4B067AF37`

## 功能

- NVIDIA / AMD 显卡型号自动探测，矿工名自动填为 `RTX3060`、`RX6700XT` 这类短名称。
- 保留矿池 `prl.kryptex.network:8048` 和账户 `krxX72P2ZE`。
- 风扇、功耗、核心偏移、显存偏移默认全部不设置。
- 默认安装到当前用户目录，不需要管理员权限。
- 首次安装创建桌面和开始菜单快捷方式，可选开机启动。
- 覆盖升级保留 `supervisor.json`，历史日志不随包分发。
- 主界面按屏幕可用区域缩放，小屏幕可纵向滚动；设置页始终支持滚动。
- 相对矿工路径 `miner\SRBMiner-MULTI.exe` 随安装目录定位，不依赖 `C:\Mining`。

## 安装

1. 双击 `PearlGuard-V2.2.0-Setup.exe`。
2. 安装向导会显示本机识别到的显卡和自动生成的矿工名，可修改矿工名。
3. 选择安装目录，完成安装。
4. 首次打开会按配置自动挖矿。默认不修改显卡参数。

安装包未使用商业代码签名证书，Windows 可能显示未知发布者提示。

## 目录

```text
pearl_guard.py                 守护器源码
gpu_info.py                    显卡和电脑型号探测
installer/gpu_probe.ps1        安装器使用的显卡探测
installer/config.template.json 首次安装配置模板
installer/*.iss                Inno Setup 安装脚本
assets/pearl.ico               程序与安装器图标
build.ps1                      可重复构建脚本
check_v2_2.py                  离线检查（不启动矿工）
dist/PearlGuard-V2.2.0-Setup.exe
```

## 构建

构建机需要 Python 3.13、PyQt6、PyInstaller 和 Inno Setup 6。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

只重新打安装器：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -SkipBuild
```

## 已验证

- 守护器 Python 源码编译通过。
- NVIDIA `RTX 3060 Laptop GPU` 自动识别为矿工名 `RTX3060`；AMD 型号转换示例通过。
- 静默安装退出码 0，生成相对矿工路径、桌面快捷方式、开始菜单快捷方式和配置。
- 静默覆盖安装保留自定义矿工名与风扇值。
- 静默卸载退出码 0，保留用户配置供重装使用。
- 新 EXE 通过现有单实例唤醒通道启动并退出码 0，没有新增挖矿进程。
- 720p 级界面构造与滚动预览通过。

## 尚未验证

- 另一台真实 NVIDIA / AMD 电脑的完整安装和实际算力。
- 不同显卡驱动的风扇、功耗和超频支持。
- 真实用户操作下的 Alt+0、托盘和安装向导页面。

SRBMiner 使用和设备条件请以其随包 `ReadMe.txt` 和 [官方参数](https://github.com/doktor83/SRBMiner-Multi/blob/3.8.0/Parameters)为准。
