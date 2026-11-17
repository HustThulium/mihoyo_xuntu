# Third-party components

This prototype uses dynamically bundled, unmodified third-party libraries:

| Component | Upstream | License family |
| --- | --- | --- |
| Python | https://www.python.org/ | PSF and bundled notices |
| NumPy | https://numpy.org/ | BSD-3-Clause and bundled notices |
| OpenCV / opencv-python | https://github.com/opencv/opencv-python | Apache-2.0 / packaging and bundled notices |
| PySide6 Essentials / Shiboken6 / Qt | https://www.qt.io/qt-for-python | LGPL-3.0 / GPL / commercial, depending on component |
| MSS | https://github.com/BoboTiG/python-mss | MIT |
| PyInstaller | https://pyinstaller.org/ | GPL with bootloader exception |

The build uses the PySide6 Essentials widgets/core/gui modules. Qt libraries remain separate DLLs in the folder distribution, not statically linked.
The build script copies installed distribution license files into `THIRD_PARTY_LICENSES`; bundled packages also retain their own notices where provided.
Refer to those exact license texts for terms and notices. This table is descriptive, not a substitute for them.

BetterGI is a GPL-3.0 public research reference. No BetterGI source, binaries, map assets or automation modules are distributed in this prototype.
