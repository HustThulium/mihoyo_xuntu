# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from runpy import run_path
import sysconfig

from PyInstaller.utils.win32 import versioninfo

project_root = Path(SPECPATH).parent
if sysconfig.get_platform() != 'win-amd64':
    raise SystemExit('RouteOverlay requires an x64 Windows build interpreter.')

version = run_path(str(project_root / 'map_overlay' / '__init__.py'))['__version__']
windows_version = (*map(int, version.split('.')), 0)
version_resource = versioninfo.VSVersionInfo(
    ffi=versioninfo.FixedFileInfo(filevers=windows_version, prodvers=windows_version,
                                 OS=0x40004, fileType=1),
    kids=[
        versioninfo.StringFileInfo([versioninfo.StringTable('080404b0', [
            versioninfo.StringStruct('FileDescription', '循图 · 原神路线叠加工具'),
            versioninfo.StringStruct('ProductName', '循图'),
            versioninfo.StringStruct('FileVersion', version),
            versioninfo.StringStruct('ProductVersion', version),
            versioninfo.StringStruct('OriginalFilename', 'RouteOverlay.exe'),
        ])]),
        versioninfo.VarFileInfo([versioninfo.VarStruct('Translation', [0x0804, 1200])]),
    ],
)

a = Analysis(
    [str(project_root / 'run.py')],
    pathex=[str(project_root)],
    binaries=[], datas=[(str(project_root / 'map_overlay' / 'icons'), 'map_overlay/icons')],
    hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork', 'PySide6.QtOpenGLWidgets'],
    noarchive=False, optimize=0,
)

def keep_binary(destination):
    name = Path(destination).name.lower()
    # Windows supplies ICU and the Universal CRT. A different ICU on the build
    # machine can shadow the OS DLL and break QtCore.
    if name.startswith(('icuuc', 'icudt', 'icuin', 'api-ms-win-', 'ext-ms-win-')):
        return False
    # Capture is via Windows and MSS, and the Qt Widgets UI uses raster painting.
    return name not in {'ucrtbase.dll', 'opengl32sw.dll'} and not name.startswith('opencv_videoio_ffmpeg')


def keep_data(destination):
    parts = Path(destination.lower()).parts
    return parts[:2] != ('pyside6', 'translations') or parts[-1] == 'qtbase_zh_cn.qm'


a.binaries = [entry for entry in a.binaries if keep_binary(entry[0])]
a.datas = [entry for entry in a.datas if keep_data(entry[0])]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RouteOverlay',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          version=version_resource,
          icon=str(project_root / 'map_overlay' / 'icons' / 'xuntu.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RouteOverlay')
