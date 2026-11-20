# Third-party components

RouteOverlay uses Qt, PySide6 Essentials and Shiboken6 under the GNU Lesser General Public License, version 3. The libraries are distributed unmodified and dynamically loaded. The full LGPLv3 and GPLv3 texts are included in `THIRD_PARTY_LICENSES`.

| Component | Upstream | License family |
| --- | --- | --- |
| Python | https://www.python.org/ | PSF and bundled notices |
| OpenSSL | https://www.openssl.org/ | Apache-2.0 |
| libffi | https://sourceware.org/libffi/ | MIT |
| NumPy | https://numpy.org/ | BSD-3-Clause and bundled notices |
| OpenCV / opencv-python-headless | https://github.com/opencv/opencv-python | Apache-2.0 / packaging and bundled notices |
| PySide6 Essentials / Shiboken6 / Qt | https://www.qt.io/qt-for-python | LGPL-3.0 |
| MSS | https://github.com/BoboTiG/python-mss | MIT |
| PyInstaller | https://pyinstaller.org/ | GPL with bootloader exception |
| Microsoft Visual C++ runtime | https://learn.microsoft.com/cpp/windows/determining-which-dlls-to-redistribute | Microsoft Distributable Code, see the notice in `THIRD_PARTY_LICENSES/Python-LICENSE.txt` |

## Qt source and library replacement

The companion `RouteOverlay-Qt-<version>-source.zip` provides the corresponding Qt Base, SVG, Image Formats, Translations, PySide6 and Shiboken6 source. It includes the version, upstream archive names, checksums and build references. Download it alongside the binary ZIP from the same release page.

To use an interface-compatible Windows x64 build of the libraries, close RouteOverlay and make a copy of its extracted application folder. In that copy, replace the matching files under `_internal/PySide6` and `_internal/shiboken6`, keeping the DLL, Python extension and plugin names and locations. Launch `RouteOverlay.exe` from the copy. The LGPL permits modifying these libraries and reverse engineering for debugging such modifications.

BetterGI is a GPL-3.0 public research reference. Its source, binaries, map assets and automation modules are outside this distribution.
