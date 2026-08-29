# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_data_files


webview_data, webview_binaries, webview_hidden = collect_all("webview")
playwright_data, playwright_binaries, playwright_hidden = collect_all("playwright")
scrapling_data, scrapling_binaries, scrapling_hidden = collect_all("scrapling")
cryptography_data, cryptography_binaries, cryptography_hidden = collect_all("cryptography")

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["src"],
    binaries=[
        *webview_binaries,
        *playwright_binaries,
        *scrapling_binaries,
        *cryptography_binaries,
    ],
    datas=[
        *collect_data_files("kad_collector"),
        *webview_data,
        *playwright_data,
        *scrapling_data,
        *cryptography_data,
    ],
    hiddenimports=[
        *webview_hidden,
        *playwright_hidden,
        *scrapling_hidden,
        *cryptography_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KAD-Collector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
