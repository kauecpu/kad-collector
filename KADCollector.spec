# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_data_files


webview_data, webview_binaries, webview_hidden = collect_all("webview")
playwright_data, playwright_binaries, playwright_hidden = collect_all("playwright")
# patchright e um fork do playwright usado pelo StealthySession do Scrapling
# (page_transport="scrapling"). Sem coletar os dados dele explicitamente, o
# driver Node.js (driver/package/*) e a pasta .local-browsers nao entram no
# .exe, e o Patchright acaba procurando o Chromium dentro da pasta temporaria
# de extracao do PyInstaller (_MEI...) em vez do cache real do usuario --
# combinado com configure_playwright_browsers_path() (que aponta
# PLAYWRIGHT_BROWSERS_PATH para %LOCALAPPDATA%\ms-playwright), isso garante
# que o Chromium instalado por "python -m patchright install chromium" seja
# encontrado corretamente pelo .exe.
patchright_data, patchright_binaries, patchright_hidden = collect_all("patchright")
scrapling_data, scrapling_binaries, scrapling_hidden = collect_all("scrapling")
# browserforge (spoofing de fingerprint usado pelo StealthySession) e o
# apify_fingerprint_datapoints de onde ele le os datasets de fingerprint
# (ex.: input-network-definition.zip) sao pacotes proprios, separados do
# scrapling -- collect_all("scrapling") nao alcanca os dados deles, entao
# precisam ser coletados explicitamente para nao faltar em tempo de execucao.
browserforge_data, browserforge_binaries, browserforge_hidden = collect_all("browserforge")
fingerprint_data, fingerprint_binaries, fingerprint_hidden = collect_all(
    "apify_fingerprint_datapoints"
)
cryptography_data, cryptography_binaries, cryptography_hidden = collect_all("cryptography")
rapidocr_data, rapidocr_binaries, rapidocr_hidden = collect_all("rapidocr")
onnxruntime_data, onnxruntime_binaries, onnxruntime_hidden = collect_all("onnxruntime")
pdfium_data, pdfium_binaries, pdfium_hidden = collect_all("pypdfium2")

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["src"],
    binaries=[
        *webview_binaries,
        *playwright_binaries,
        *patchright_binaries,
        *scrapling_binaries,
        *browserforge_binaries,
        *fingerprint_binaries,
        *cryptography_binaries,
        *rapidocr_binaries,
        *onnxruntime_binaries,
        *pdfium_binaries,
    ],
    datas=[
        *collect_data_files("kad_collector"),
        *webview_data,
        *playwright_data,
        *patchright_data,
        *scrapling_data,
        *browserforge_data,
        *fingerprint_data,
        *cryptography_data,
        *rapidocr_data,
        *onnxruntime_data,
        *pdfium_data,
    ],
    hiddenimports=[
        *webview_hidden,
        *playwright_hidden,
        *patchright_hidden,
        *scrapling_hidden,
        *browserforge_hidden,
        *fingerprint_hidden,
        *cryptography_hidden,
        *rapidocr_hidden,
        *onnxruntime_hidden,
        *pdfium_hidden,
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
