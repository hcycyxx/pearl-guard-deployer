# Pearl 守护器 V2.2.0

珍珠币挖矿一键部署。实现监测算力，控制风扇，限制功率。自动检测是否提交成功。挖矿意外终止会随时拉起，保持挖矿在线。自动更新最新的挖矿程序。
Windows 一键部署版。安装包内含 Pearl 守护器、SRBMiner 3.8.0、显卡型号探测、默认配置和快捷方式，目标电脑无需安装 Python。

## 首次挖矿：邀请码与自己的账户

首次使用 Kryptex，请先通过邀请码 **EA599473** 注册：

- 注册：https://www.kryptex.com/?ref=EA599473
- 矿池：https://pool.kryptex.com/?ref=EA599473

> **重要：安装包默认仍使用作者账户 `krxX72P2ZE`。** 其他使用者安装后请立即点击“停止守护”，打开“设置”，把“收款账户”改成自己在 Kryptex 的 `krx...` 账户，然后重新启动矿工。不要把自己的挖矿收入长期留在默认账户。

## AI 一键部署

将下面这段直接交给 Codex 或 WorkBuddy：

```text
请读取此 GitHub 仓库的 README 和 Release，为当前 Windows 电脑部署 Pearl 守护器 V2.2.0。先确认有受支持的 NVIDIA/AMD 显卡并取得电脑所有者同意；首次使用 Kryptex 时，按邀请码 EA599473 引导我本人注册并取得自己的 krx... 收款账户。安装时使用静默模式避免程序首次启动，然后把安装目录 supervisor.json 中的 account 改成我的账户并回读确认；只有账户核对一致后才启动 Pearl 守护器，最后检查 SRBMiner-MULTI.exe 和矿池连接。任何一步失败就停止并报告，不要使用默认的作者账户继续挖矿。
```

## 下载

- [PearlGuard-V2.2.0-Setup.exe](dist/PearlGuard-V2.2.0-Setup.exe)
- SHA256：`DB9D23553F082478B0B8DCFDBE87F92B913C4FCCF509A739AC8C77ABD0C2DC4E`

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

1. 首次使用 Kryptex 时，通过邀请码 `EA599473` 注册并取得自己的 `krx...` 收款账户。
2. 双击 `PearlGuard-V2.2.0-Setup.exe`。
3. 安装向导会显示本机识别到的显卡和自动生成的矿工名，可修改矿工名。
4. 选择安装目录，完成安装。
5. 如果安装器自动打开了程序，请**先点击“停止守护”**，进入“设置”，把“收款账户”换成自己的 `krx...` 账户并保存，再启动矿工。
6. 默认不修改显卡风扇、功耗、核心偏移和显存偏移。

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
- 安装器在未识别 AMD/NVIDIA 显卡时显示醒目警告，不静默使用空名称。

## 尚未验证

- 另一台真实 NVIDIA / AMD 电脑的完整安装和实际算力。
- 不同显卡驱动的风扇、功耗和超频支持。
- 真实用户操作下的 Alt+0、托盘和安装向导页面。

SRBMiner 使用和设备条件请以其随包 `ReadMe.txt` 和 [官方参数](https://github.com/doktor83/SRBMiner-Multi/blob/3.8.0/Parameters)为准。

## 许可证

本项目采用 [MIT License](LICENSE)。任何人都可以免费使用、复制、修改、合并、发布、分发、再许可和商业使用；分发时请保留版权声明和许可证文本。
