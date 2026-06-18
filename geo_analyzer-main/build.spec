# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


ROOT = Path.cwd()


def existing_datas() -> list[tuple[str, str]]:
    datas: list[tuple[str, str]] = []

    config_dir = ROOT / "config"
    if config_dir.exists():
        datas.append((str(config_dir), "config"))

    for package in [
        "pyproj",
        "rasterio",
        "geopandas",
        "contextily",
        "xyzservices",
        "certifi",
    ]:
        try:
            datas.extend(collect_data_files(package))
        except Exception:
            pass

    return datas


def existing_binaries() -> list[tuple[str, str]]:
    binaries: list[tuple[str, str]] = []

    for package in [
        "rasterio",
        "pyproj",
        "shapely",
        "pyogrio",
    ]:
        try:
            binaries.extend(collect_dynamic_libs(package))
        except Exception:
            pass

    return binaries


hiddenimports = []

for package in [
    "geo_analyzer",
    "rasterio",
    "pyproj",
    "shapely",
    "pyogrio",
    "geopandas",
    "contextily",
    "xyzservices",
]:
    try:
        hiddenimports.extend(collect_submodules(package))
    except Exception:
        pass

hiddenimports.extend(
    [
        "geo_analyzer.core._embedded_secret",
        "rasterio.sample",
        "rasterio.vrt",
        "rasterio.enums",
        "rasterio.env",
        "rasterio.errors",
        "rasterio.features",
        "rasterio.transform",
        "rasterio.warp",
        "rasterio.windows",
        "pyarrow",
        "pyarrow.parquet",
        "pandas",
        "geopandas",
        "shapely",
        "matplotlib",
        "openpyxl",
        "yaml",
        "dotenv",
        "contextily",
        "certifi",
    ]
)

block_cipher = None

a = Analysis(
    ["gui.py"],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=existing_binaries(),
    datas=existing_datas(),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "notebook",
        "jupyter",
        "IPython",
        "pytest",
        "tkinter.test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeoAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GeoAnalyzer",
)