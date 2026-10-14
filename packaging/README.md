# Windows installer

The installed app is a self-contained folder: Python, FFmpeg and the UI are bundled. Users do
not need Python, Node or FFmpeg, and nothing is downloaded at runtime (fully offline).

## Build (on the Windows development PC)

You need these once:

- the project `.venv`, created as in the main README;
- Node.js, for `npm`;
- **Inno Setup 6**, only for the installer `.exe`: `winget install JRSoftware.InnoSetup`.

Then, from the project folder:

```
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

The script runs these steps in order:

1. UI: typecheck, tests and build.
2. Python tests.
3. The offline policy check.
4. PyInstaller.
5. A **self-check of the built app**: UI files, fonts, FFmpeg with x264/AAC/libass, data
   folder, libraries.
6. Inno Setup.

It stops at the first failure.

It produces:

| Output | What it is |
|---|---|
| `build\dist\OfflineVideoEditor\` | Portable app folder; run `OfflineVideoEditor.exe` |
| `build\installer\OfflineVideoEditor-Setup-<version>.exe` | The installer to share |

Useful switches:

- `-NoInstaller`: build only the app folder (Inno Setup not needed).
- `-SkipTests`: skip pytest when re-packaging a commit that is already tested.

## What the installer does

- Installs per user, without admin rights, to `%LOCALAPPDATA%\Programs\OfflineVideoEditor`.
- Adds a Start-menu shortcut and an optional desktop shortcut.
- Projects, caches and logs are kept in `%LOCALAPPDATA%\OfflineVideoEditor`. They survive
  upgrades and uninstall.
- Starting the app opens the editor in the default browser, at `http://127.0.0.1:<random
  port>`. Starting it again while it runs just reopens the browser tab.
- Logs: `%LOCALAPPDATA%\OfflineVideoEditor\logs\editor.log`.

The installer is not code-signed, so Windows SmartScreen may show "Windows protected your
PC". Choose **More info → Run anyway**. Signing needs a paid certificate and can be added
later.

## Troubleshooting on a user's PC

Run the self-check from a command prompt:

```
"%LOCALAPPDATA%\Programs\OfflineVideoEditor\OfflineVideoEditor.exe" --check
```

The report is saved to `%LOCALAPPDATA%\OfflineVideoEditor\logs\self_check.json`.
