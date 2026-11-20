# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH).parent
a = Analysis(
    [str(project_root / 'run.py')],
    pathex=[str(project_root)],
    binaries=[], datas=[(str(project_root / 'map_overlay' / 'icons'), 'map_overlay/icons')],
    hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork', 'PySide6.QtOpenGLWidgets'],
    noarchive=False, optimize=0,
)

# Qt on Windows imports the unversioned Windows ICU API. The build interpreter's
# PATH can contain another icuuc.dll with version-suffixed exports (e.g. ICU 78).
# Bundling that file shadows the OS DLL and breaks importing QtCore. Keep Windows
# ICU as an OS dependency, matching the source application's DLL resolution.
a.binaries = [entry for entry in a.binaries
              if not Path(entry[0]).name.lower().startswith(('icuuc', 'icudt', 'icuin'))]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RouteOverlay',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(project_root / 'map_overlay' / 'icons' / 'xuntu.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RouteOverlay')
