"""Validate the two release archives and run the extracted EXE on a private desktop."""
from __future__ import annotations

from ctypes import wintypes as wt
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_MANIFEST = json.loads((ROOT / 'packaging/samples.json').read_text(encoding='utf-8'))
sys.path.insert(0, str(ROOT))
from map_overlay.demo import make_demo


def check_qt_sources(path: Path) -> None:
    manifest = (ROOT / 'packaging/qt-sources.json').read_bytes()
    sources = {
        PurePosixPath(urlsplit(item['url']).path).name: item['sha256']
        for item in json.loads(manifest)['archives']
    }
    with ZipFile(path) as archive:
        if (sorted(archive.namelist()) != sorted([*sources, 'QT_SOURCES.md', 'qt-sources.json'])
                or archive.read('qt-sources.json') != manifest
                or archive.read('QT_SOURCES.md') != (ROOT / 'packaging/QT_SOURCES.md').read_bytes()):
            raise RuntimeError('Qt source ZIP does not match the source manifest.')
        for name, expected_hash in sources.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
                    raise RuntimeError(f'Invalid Qt source in ZIP: {name}')


def extract_runtime(path: Path, destination: Path) -> Path:
    expected_hash = path.with_suffix('.zip.sha256').read_text(encoding='ascii').split()[0]
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
            raise RuntimeError('Runtime ZIP checksum mismatch.')

    sample_directory = SAMPLE_MANIFEST['directory']
    sample_prefix = f'RouteOverlay/{sample_directory}/'
    sample_sources = {f'{sample_prefix}{name}': ROOT / sample_directory / name
                      for name in SAMPLE_MANIFEST['files']}
    allowed = {'RouteOverlay.exe', '_internal', 'README.md', 'RELEASE_NOTES.md',
               'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_LICENSES', sample_directory}
    required = {'RouteOverlay.exe', '_internal/cv2/__init__.py', '_internal/cv2/cv2.pyd',
                '_internal/PySide6/translations/qtbase_zh_CN.qm',
                'THIRD_PARTY_LICENSES/GPL-3.0-only.txt', 'THIRD_PARTY_LICENSES/LGPL-3.0-only.txt',
                'THIRD_PARTY_LICENSES/Python-LICENSE.txt'}
    with ZipFile(path) as archive:
        names = archive.namelist()
        parts = [name.split('/') for name in names]
        if (len(names) != len(set(names)) or any(len(p) < 2 or p[0] != 'RouteOverlay'
                or '\\' in name or ':' in name or set(p) & {'', '.', '..'}
                for name, p in zip(names, parts)) or {p[1] for p in parts} != allowed):
            raise RuntimeError('Unexpected runtime ZIP layout.')
        if not {f'RouteOverlay/{name}' for name in required} <= set(names):
            raise RuntimeError('Runtime files or licenses missing from the ZIP.')
        if any(name.lower().endswith(('/settings.json', '.log', '/opengl32sw.dll'))
               or (name.lower().endswith('.routepack') and name not in sample_sources)
               or 'opencv_videoio_ffmpeg' in name.lower() for name in names):
            raise RuntimeError('Unexpected data or optional library in the runtime ZIP.')
        if {name for name in names if name.startswith(sample_prefix)} != set(sample_sources):
            raise RuntimeError('The bundled samples do not match the sample manifest.')
        if archive.read('RouteOverlay/README.md') != (ROOT / 'packaging/README.md').read_bytes():
            raise RuntimeError('The runtime ZIP does not contain the release README.')
        for name, source in sample_sources.items():
            if archive.read(name) != source.read_bytes():
                raise RuntimeError(f'Incorrect sample in runtime ZIP: {name}')
        archive.extractall(destination)  # Also checks each entry's CRC.
    return destination / 'RouteOverlay/RouteOverlay.exe'


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR), ('lpTitle', wt.LPWSTR),
        ('dwX', wt.DWORD), ('dwY', wt.DWORD), ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD),
        ('dwXCountChars', wt.DWORD), ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD),
        ('dwFlags', wt.DWORD), ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD),
        ('lpReserved2', ctypes.c_void_p), ('hStdInput', wt.HANDLE),
        ('hStdOutput', wt.HANDLE), ('hStdError', wt.HANDLE),
    ]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('process', wt.HANDLE), ('thread', wt.HANDLE), ('pid', wt.DWORD), ('tid', wt.DWORD)]


user = ctypes.WinDLL('user32', use_last_error=True)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
WindowCallback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
user.CreateDesktopW.restype = wt.HANDLE
user.CloseDesktop.argtypes = [wt.HANDLE]
user.EnumDesktopWindows.argtypes = [wt.HANDLE, WindowCallback, wt.LPARAM]
user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL,
                                  wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                  ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel.CloseHandle.argtypes = [wt.HANDLE]
kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD,
                                      ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]


def check_loaded_libraries(process, package: Path, system_root: Path) -> None:
    modules = (wt.HMODULE * 512)()
    size = wt.DWORD()
    if not psapi.EnumProcessModulesEx(process, modules, ctypes.sizeof(modules), ctypes.byref(size), 3):
        raise ctypes.WinError(ctypes.get_last_error())
    if size.value > ctypes.sizeof(modules):
        raise RuntimeError('Too many modules to verify.')
    paths = {}
    for module in modules[:size.value // ctypes.sizeof(wt.HMODULE)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if not psapi.GetModuleFileNameExW(process, module, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        path = Path(buffer.value)
        paths[path.name.lower()] = path
    python_dll = f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    for name in (python_dll, 'qt6core.dll', 'qt6widgets.dll', 'cv2.pyd', 'qwindows.dll', 'vcruntime140.dll'):
        if name not in paths or not paths[name].is_relative_to(package / '_internal'):
            raise RuntimeError(f'Expected bundled module: {name} ({paths.get(name)})')
    unexpected = [str(path) for path in paths.values()
                  if not path.is_relative_to(system_root) and not path.is_relative_to(package)]
    if unexpected or {'mfplat.dll', 'mf.dll', 'mfreadwrite.dll'}.intersection(paths):
        raise RuntimeError(f'Unexpected runtime dependencies: {unexpected or "Media Foundation"}')


def smoke_test(exe: Path, route: Path | None) -> None:
    sandbox = exe.parent.parent
    system_root = Path(os.environ['SystemRoot'])
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PYTHON', 'QT_', 'CONDA', 'VIRTUAL_ENV'))}
    env.update(PATH=f'{system_root / "System32"};{system_root}',
               APPDATA=str(sandbox / 'AppData/Roaming'), LOCALAPPDATA=str(sandbox / 'AppData/Local'))
    environment = ctypes.create_unicode_buffer('\0'.join(f'{k}={env[k]}' for k in sorted(env)) + '\0')
    args = [str(exe)] + ([str(route)] if route else [])
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))

    desktop_name = f'RouteOverlayRelease_{uuid4().hex}'
    desktop = user.CreateDesktopW(desktop_name, None, None, 0, 0x00C3, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    startup = StartupInfo(cb=ctypes.sizeof(StartupInfo), lpDesktop=desktop_name)
    process = ProcessInfo()
    windows = {}

    @WindowCallback
    def inspect_window(hwnd, _):
        owner = wt.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == process.pid:
            title = ctypes.create_unicode_buffer(256)
            if user.GetWindowTextW(hwnd, title, len(title)):
                windows[hwnd] = title.value
        return True

    try:
        # CREATE_UNICODE_ENVIRONMENT keeps non-ASCII extraction paths and arguments intact.
        if not kernel.CreateProcessW(str(exe), command, None, None, False, 0x400, environment,
                                     str(sandbox), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 20
        first_window = None
        while time.monotonic() < deadline and kernel.WaitForSingleObject(process.process, 150) == 258:
            windows.clear()
            user.EnumDesktopWindows(desktop, inspect_window, 0)
            if any('无法完成操作' in title or '发生错误' in title for title in windows.values()):
                raise RuntimeError(f'Packaged application displayed an error: {list(windows.values())}')
            main = next((hwnd for hwnd, title in windows.items() if title.startswith('循图 · ')), None)
            if main:
                first_window = first_window or time.monotonic()
                if time.monotonic() - first_window >= 3:
                    break
        else:
            raise RuntimeError(f'Packaged main window did not stay open: {list(windows.values())}')

        check_loaded_libraries(process.process, exe.parent, system_root)
        if not user.PostMessageW(main, 0x0010, 0, 0):  # WM_CLOSE
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(process.process, 5000) != 0:
            raise RuntimeError('Packaged application did not close.')
        exit_code = wt.DWORD()
        if not kernel.GetExitCodeProcess(process.process, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value or (exe.parent / 'logs/crash.log').exists():
            raise RuntimeError(f'Packaged application failed on exit (0x{exit_code.value:08X}).')
        print(f'PASS: packaged startup and normal shutdown ({"with" if route else "without"} route).', flush=True)
    finally:
        if process.process:
            if kernel.WaitForSingleObject(process.process, 0) == 258:
                kernel.TerminateProcess(process.process, 1)
                kernel.WaitForSingleObject(process.process, 5000)
            kernel.CloseHandle(process.thread)
            kernel.CloseHandle(process.process)
        user.CloseDesktop(desktop)


def main(runtime: Path, qt_sources: Path) -> None:
    check_qt_sources(qt_sources)
    with tempfile.TemporaryDirectory(prefix='循图 解压测试 ', dir=runtime.parent) as temporary:
        destination = Path(temporary)
        exe = extract_runtime(runtime, destination)
        route = destination / '演示 路线.routepack'
        make_demo().save(route)
        sample_name = next(name for name in SAMPLE_MANIFEST['files'] if name.endswith('.routepack'))
        sample_route = exe.parent / SAMPLE_MANIFEST['directory'] / sample_name
        for argument in (None, route, None, sample_route):
            smoke_test(exe, argument)


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
