<p align="center">
  <img src="radiostudio/assets/logo-about-window.png" alt="RadioStudio Python" width="420">
</p>

<h1 align="center">RadioStudio Python Sirius</h1>

<p align="center">
  A multilingual radio automation studio for playlists, AutoDJ, live audio, and broadcasting.
</p>

<p align="center"><a href="LICENSE">License</a> · <a href="https://github.com/RaphaaCC/RadioStudio-Python/issues">Report an issue</a></p>

---

RadioStudio Python Sirius is a standalone radio automation application. It brings local playback, music scheduling, studio inputs, and streaming into one workstation.

The same Python source runs on Windows and on Ubuntu/Debian **desktop sessions** (X11 or Wayland). Linux requires a graphical environment and a working desktop audio service (PulseAudio or PipeWire); headless/server operation is outside the supported scope. Windows executable packages remain Windows-specific.

## Features

| Area | Features |
| --- | --- |
| **Playlist and playback** | Add audio files and folders, arrange the queue, save M3U playlists, and control local playback. |
| **Mixing** | Configure fades and track overlaps, monitor the on-air signal, and view track artwork. |
| **AutoDJ** | Index music libraries, schedule blocks and filters, avoid repetition, and review playback history. |
| **Studio inputs** | Use a microphone, up to five auxiliary inputs, a 25-button Cart wall, and scheduled tracks. |
| **Broadcasting** | Stream through the built-in server, Icecast, or SHOUTcast; monitor outputs and listeners when available. |
| **Station** | Configure station metadata, on-air titles, and Discord Rich Presence. |

## Languages

The interface is available in:

- Brazilian Portuguese
- United States English
- Mexican Spanish
- Korean
- Japanese
- Traditional Chinese
- Simplified Chinese

The application can follow the operating system language. The installer lets users choose the initial application language; the portable edition asks on first launch. The preference can also be changed later in **Settings**. Changing the language prompts for confirmation and restarts the application to apply the new language.

## Get started on Windows

1. Install Python 3.10 or newer for Windows and make sure the `py` command is available.
2. Download or clone this repository.
3. Run `Iniciar RadioStudio Python.bat`.

On first launch, the script creates the `.venv` environment, installs dependencies, and starts the application.

### Run from source

In PowerShell, from the project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m radiostudio
```

Run the automated tests with:

```powershell
$env:APPDATA = Join-Path $env:TEMP ("RadioStudio-tests-" + [guid]::NewGuid().ToString("N"))
$env:RADIOSTUDIO_DATA_DIR = $env:APPDATA
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Get started on Ubuntu or Debian desktop

Use Ubuntu 22.04/24.04 or Debian 12/13 with Python 3.10 or newer. From a terminal in your graphical desktop session, install the system dependencies:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip \
  libegl1 libgl1 libopengl0 libdbus-1-3 libfontconfig1 libglib2.0-bin libgssapi-krb5-2 \
  libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
  libxcb-xkb1 libx11-xcb1 libpulse0 libpipewire-0.3-common gnome-keyring
```

Keep the desktop's PulseAudio or PipeWire service running. A Secret Service provider such as GNOME Keyring must be running and unlocked on the user's session D-Bus to save broadcasting and REST API passwords. KDE users can use a compatible Secret Service provider. The application continues to open when the credential store is unavailable, but authenticated services require access to the stored credentials. Passwords are never saved to the settings file as a fallback. Run as your desktop user, without `sudo`.

Clone or download this repository, then run:

```bash
bash "Iniciar RadioStudio Python.sh"
```

The launcher creates `.venv`, installs missing Python dependencies, and starts the desktop application. Alternatively, run from source:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m radiostudio
```

For automated checks, use an isolated profile:

```bash
test_profile=$(mktemp -d)
APPDATA="$test_profile" RADIOSTUDIO_DATA_DIR="$test_profile" \
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/desktop_smoke.py
```

The smoke check opens and closes the main window and Settings using its own temporary profile. CI runs the suite on Windows, Ubuntu, and Debian, and opens Linux windows under Xvfb. These virtual displays are test infrastructure; normal use requires a graphical desktop. Physical audio devices and external services still need validation on the target workstation.

## Broadcasting

- **Built-in server:** audio is available at `/live.mp3`; `/status.json` reports stream status.
- **Icecast:** configure a profile with the server, port, mount, and source user.
- **SHOUTcast:** use source mode v1, which is compatible with DNAS v2 servers.

Broadcast audio is encoded as stereo MP3 at 44.1 kHz. New profiles default to 192 kbps. Passwords are stored through `keyring`, using **Windows Credential Manager** on Windows or the desktop's **Secret Service** on Linux, separately from the settings file. FFmpeg is supplied by `imageio-ffmpeg` for the host platform.

## User data

- **Windows:** `%APPDATA%\RadioStudioPython`.
- **Linux:** `$XDG_DATA_HOME/RadioStudioPython`, normally `~/.local/share/RadioStudioPython`. An existing `~/RadioStudioPython` profile from earlier source versions is retained when no XDG profile or explicit XDG location is present.
- **Portable edition:** `RadioStudioPythonData` beside the executable. If that location is not writable, data is stored in the system user profile above.
- **Explicit profile:** `RADIOSTUDIO_DATA_DIR` overrides these locations on both platforms, including portable mode. It is useful for tests and separate station profiles.

The application profile stores settings, music-library indexes, and history.

Linux filenames are case-sensitive. When moving a library, playlist, or backup between computers, select the corresponding local music folders again; Windows drive letters and Linux mount points are different. Backups do not copy music files or credentials.

## Known limitations

- SHOUTcast native v2 source protocol is not implemented; use the supported source mode v1.
- Playback of each audio format depends on the Qt multimedia backend, host codecs, and bundled FFmpeg.
- Elevated and realtime CPU priorities can be refused by the operating system. Linux uses nice levels for regular priorities and round-robin scheduling for realtime; changing these may require additional user permissions. Do not run the whole desktop application as root to change priority.
- Tray support depends on the desktop environment. If a system tray is unavailable, closing the window exits the application; hiding to the tray is disabled.
- Connections to external servers depend on the destination service and its configuration.

## Contributing

Open an [issue](https://github.com/RaphaaCC/RadioStudio-Python/issues) to report a problem or discuss an idea. Pull requests should explain the change and how it was validated.

Raemi Labs and Raphael Cherfem Carlini maintain the project and control the roadmap and what is accepted into the official repository. A suggestion or pull request does not guarantee acceptance.

## License and credits

The source code is available under the [PolyForm Noncommercial License 1.0.0](LICENSE). Copyright belongs to **Raemi Labs and Raphael Cherfem Carlini**. The license allows noncommercial use, including personal use, research, study, private entertainment, and use by nonprofit organizations, provided its terms and copyright notice are preserved.

The license does not grant commercial use or distribution rights. To request a separate commercial license, contact [raphael@raemi.xyz](mailto:raphael@raemi.xyz). Forks and derivative works must also comply with the PolyForm license; maintainers retain control of the official repository.

## Support

For support, open a [GitHub issue](https://github.com/RaphaaCC/RadioStudio-Python/issues) or email [raphael@raemi.xyz](mailto:raphael@raemi.xyz).
