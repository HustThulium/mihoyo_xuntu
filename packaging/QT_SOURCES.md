# RouteOverlay Qt sources

The source companion contains these unmodified Qt Project archives for the Qt libraries in the RouteOverlay Windows x64 distribution. Each archive contains the upstream build files and license notices.

The included `qt-sources.json` lists the exact Qt version, upstream download URLs and SHA-256 checksums.

Build the Qt modules for Windows x64 with MSVC, then build the PySide6 Essentials and Shiboken6 bindings with that Qt installation if you are replacing the bindings. The upstream instructions cover [building Qt on Windows](https://doc.qt.io/qt-6/windows-building.html) and [building Qt for Python](https://doc.qt.io/qtforpython-6/building_from_source/index.html). See `THIRD_PARTY_NOTICES.md` in the binary distribution for how to run RouteOverlay with replacement libraries.
