# Third-Party Notices

The Windows executable is built with the following third-party components.
Their licenses are retained in `third_party_licenses/`; they are separate from
any license the repository owner later chooses for this project's own source.

| Component | Version | Notice |
| --- | --- | --- |
| CPython | 3.13.2 | `third_party_licenses/python.txt` |
| psutil | 7.2.2 | `third_party_licenses/psutil.txt` |
| websockets | 16.1 | `third_party_licenses/websockets.txt` |
| Pillow | 12.2.0 | `third_party_licenses/pillow.txt` |
| PyInstaller bootloader | 6.22.3 | `third_party_licenses/pyinstaller.txt` |
| Tcl/Tk | Bundled with the tested Python runtime | `third_party_licenses/tk.txt` |

PyInstaller's notice includes its bootloader exception. No modifications to
the bootloader are made by this project. The build excludes NumPy, which is
not used by the application.

If rebuilding with another runtime or dependency version, preserve the notices
for that distribution too. This list is not a license for the project's own
source and does not grant rights to OpenDesign or Google trademarks.
