# 循图 · 原神路线叠加工具

循图把手绘路线和从攻略图提取的路线叠加到原神小地图上。用自己的游戏截图建立区域，可保存为 `.routepack`，下次打开继续使用。程序通过系统截图在本机定位，不读写游戏内存、不注入游戏进程。

适用于 **Windows 10 2004 及以上或 Windows 11（x64）**。游戏需使用窗口化或无边框窗口，小地图固定朝北。长时间跑图与失效恢复仍待完整实测。

## 快速开始

正式发布后，可在 [GitHub Releases](https://github.com/HustThulium/mihoyo_xuntu/releases) 下载便携 ZIP。完整解压到可写目录，双击 `RouteOverlay.exe` 即可运行，无需安装 Python。

1. 选择游戏窗口，关闭游戏大地图，在「小地图位置设置…」中框选小地图。
2. 打开随包的样例 `.routepack`，或从游戏大地图「新建区域」，手绘路线或导入攻略图。
3. 关闭游戏大地图，点击「开始叠加」，回到游戏的对应区域。按 **Ctrl+Alt+F9** 可开始或停止叠加，**Ctrl+Alt+F11** 可切换截图模式。

随包样例的使用步骤见 [发布包说明](packaging/README.md)。

## 从源码运行与构建

使用 Python 3.12 x64。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run.py
```

如果沿用安装过 `opencv-python` 的旧虚拟环境，请先卸载它，再按锁定文件安装依赖。两种 OpenCV wheel 共用 `cv2` 模块。

- 运行测试 `\.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
- 在 Windows 桌面会话运行 `.\release.cmd`，生成并校验运行 ZIP、`.sha256` 校验文件和 Qt 源码 ZIP，产物位于 `dist/release/`。

依赖与许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。
