# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo
from src.version import APP_VERSION, WINDOWS_VERSION
from scripts.prepare_opensource import public_files

if not Path("assets/flet-windows.zip").is_file():
    raise SystemExit("Place the official Flet 0.86.5 Windows archive at assets/flet-windows.zip before building")

source_root = Path(SPECPATH)
datas = [(str(path), str(path.relative_to(source_root).parent)) for path in public_files(source_root)
         if path.relative_to(source_root).parts[0] in {'workflows', 'examples', 'plugins', 'skills', 'assets'}
         or path.name in {'LICENSE', 'NOTICE'}]
datas += [('assets/flet-windows.zip', 'flet_desktop/app')]
binaries = []
hiddenimports = ['pypdf']

tmp_ret = collect_all('pypdf', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('flet', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('flet_desktop', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('numpy', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('scipy', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sklearn', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pandas', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('matplotlib', filter_submodules=lambda name: not {'tests', 'test', 'benchmarks'}.intersection(name.split('.')), exclude_datas=['**/tests/**', '**/test/**', '**/benchmarks/**'])
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src\\ui\\desktop_launcher.py'],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide6', 'PySide2', 'tkinter', 'playwright', 'panel', 'botocore', 'boto3', 'cv2', 'llvmlite', 'numba', 'notebook', 'bokeh', 'jupyterlab', 'pyarrow', 'selenium', 'IPython', 'jupyter_client', 'jupyter_core', 'spyder', 'holoviews', 'datashader', 'torch', 'tensorflow', 'pytest', 'sphinx', 'lightgbm', 'xgboost', 'dask', 'distributed', 'tables', 'h5py', 'sqlalchemy', 'statsmodels', 'sympy', 'skimage'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Gezhi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\gezhi.ico'],
    version=VSVersionInfo(
        ffi=FixedFileInfo(filevers=WINDOWS_VERSION, prodvers=WINDOWS_VERSION,
                          mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('080404b0', [
            StringStruct('CompanyName', '格致团队'), StringStruct('FileDescription', 'SciZhi Agent'),
            StringStruct('FileVersion', APP_VERSION), StringStruct('InternalName', 'Gezhi'),
            StringStruct('OriginalFilename', 'Gezhi.exe'), StringStruct('ProductName', '格致 SciZhi'),
            StringStruct('ProductVersion', APP_VERSION)])]),
            VarFileInfo([VarStruct('Translation', [2052, 1200])])]),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Gezhi',
)
