# Pearl 守护器 V2.3.0

Windows 珍珠币守护器。监控算力和有效份额，支持风扇和功耗设置，矿工异常时可自动恢复。V2.3.0 加入 SRBMiner 自动更新与后台隐藏。

## 获取 V2.3.0

- [V2.3.0 源码发布](https://github.com/hcycyxx/pearl-guard-deployer/releases/tag/v2.3.0)。请从 SRBMiner 官方下载 3.8.0 Windows ZIP，再按下方步骤本地构建安装包。
- [V2.2.0 历史版本](https://github.com/hcycyxx/pearl-guard-deployer/releases/tag/v2.2.0)

## 安装与首次启动

1. 确认设备所有者同意在这台电脑挖矿。首次使用 Kryptex 时，请通过邀请码 **EA599473** 注册并取得自己的 `krx...` 收款账户。 [Kryptex 注册](https://www.kryptex.com/?ref=EA599473) · [矿池推荐入口](https://pool.kryptex.com/?ref=EA599473) · [官方推荐说明](https://www.kryptex.com/referral/)
2. 本地构建并运行安装器。向导会识别 NVIDIA 或 AMD 显卡并填入矿工名称，名称可以修改。安装时可选桌面快捷方式和开机自启动。
3. 开启守护器，在「设置」填入自己的收款账户并保存。Kryptex Pearl 默认使用 SSL 矿池 `prl.kryptex.network:8048`，保留该地址即可。推荐链接用于网页注册，矿池设置填 `host:port`。 [Kryptex 官方矿池地址](https://pool.kryptex.com/articles/pearl-mining-pools-en)
4. 点击「启动守护」，确认设备授权和自己的账户后开始挖矿。安装器默认不填写风扇、功耗或超频数值。

从 V2.2.0 升级时，先退出正在运行的旧版守护器，再安装到原目录。安装器保留 `supervisor.json`、历史日志和已有矿工文件。若要维持停止状态，请保持安装后的启动选项关闭。旧版仍使用随包默认账户时，请在首次启动挖矿前核对并确认账户。

## SRBMiner 更新

守护器启动后检查正式版本，此后每 6 小时检查一次。打开「设置 → 矿工更新」查看当前/最新版本和进度，可手动检查和更新。设备所有者可以勾选「自动更新 SRBMiner」并保存，之后可自动安装通过校验的正式版。

下载和解压在后台完成，旧矿工继续运行。更新器依据官方发布记录中的 Windows x64 ZIP SHA256 验证归档。网络错误或校验失败时继续使用当前版本。切换后会恢复原来的运行状态，启动失败或验证期间出现守护异常时回滚。回滚后的失败版本会被跳过。

## 后台隐藏

点击主界面「后台隐藏」，窗口会从桌面和任务栏消失，托盘图标也会隐藏。监控、矿工更新、定时启停和异常重启继续运行。按 **Alt+0**（主键盘数字 0）恢复窗口和托盘。若快捷键被占用，守护器会提示并保持界面可见。普通最小化和关闭到托盘沿用原有操作。

## AI 一键部署

把下面这段交给 Codex 或 WorkBuddy。

```text
请依据本仓库 README 为当前 Windows 电脑部署 Pearl 守护器 V2.3.0。
1. 先确认设备所有者授权及显卡支持。首次使用 Kryptex 时，引导我本人通过邀请码 EA599473 注册，核对我自己的 krx... 收款账户。
2. 从 V2.3.0 源码和官方 SRBMiner 3.8.0 Windows ZIP 本地构建 PearlGuard-V2.3.0-Setup.exe。用 Get-FileHash -Algorithm SHA256 对照构建输出的 .sha256 文件，再用 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART 静默安装。覆盖升级沿用原安装目录，先经我确认退出正在运行的旧守护器。
3. 检查安装目录的 supervisor.json。覆盖升级保留原有配置、账户和日志。首次安装只在取得我的授权、核对收款账户后，将 account 写成我提供的账户，将 mining_authorized 设为 true，并回读确认。默认 SSL 矿池沿用 prl.kryptex.network:8048。
4. 只有我明确要求启动时才启动 PearlGuard.exe。启动后核对矿工进程和矿池连接。如果授权、账户或完整性校验未通过，停止部署并报告。
```

## 目录与构建

- `pearl_guard.py` 守护器及界面。`miner_update.py` 官方发布检查、下载验证和版本切换。
- `gpu_info.py`、`installer/gpu_probe.ps1` 显卡和矿工名探测。
- `installer/Pearl守护器_V2.3.0.iss` 安装脚本。`使用说明_V2.3.0.txt` 随包说明。
- `check_v2_3.py` 使用模拟矿工的回归检查。

构建机需要 Python 3.13、PyQt6、PyInstaller、Inno Setup 6 和 [官方 SRBMiner 3.8.0 Windows ZIP](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.0)。使用 `-Python`、`-ISCC`、`-MinerArchive` 指定本机路径。

```powershell
pwsh -NoProfile -File .\build.ps1
```

如已构建 `dist/onefile/PearlGuard-V2.3.0.exe`，可加 `-SkipBuild` 仅重建安装器。构建脚本会输出对应 `.sha256` 文件。

## 许可证

守护器源码以 [MIT License](LICENSE) 提供。SRBMiner 及其附带组件按各自授权条款提供，见随包 `ReadMe.txt` 和 [官方仓库](https://github.com/doktor83/SRBMiner-Multi)。
