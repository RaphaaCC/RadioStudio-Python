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

1. Install Python 3 for Windows and make sure the `py` command is available.
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Broadcasting

- **Built-in server:** audio is available at `/live.mp3`; `/status.json` reports stream status.
- **Icecast:** configure a profile with the server, port, mount, and source user.
- **SHOUTcast:** use source mode v1, which is compatible with DNAS v2 servers.

Broadcast audio is encoded as stereo MP3 at 44.1 kHz. New profiles default to 192 kbps. Passwords are stored in **Windows Credential Manager**, separately from the settings file.

## User data

- **Setup installation:** `%APPDATA%\RadioStudioPython`.
- **Portable edition:** `RadioStudioPythonData` beside the executable. If that location is not writable, data is stored in `%APPDATA%\RadioStudioPython`.

The application profile stores settings, music-library indexes, and history.

## Known limitations

- SHOUTcast native v2 source protocol is not implemented; use the supported source mode v1.
- Playback of each audio format depends on the codecs available in Windows and the bundled FFmpeg.
- Connections to external servers depend on the destination service and its configuration.

## Contributing

Open an [issue](https://github.com/RaphaaCC/RadioStudio-Python/issues) to report a problem or discuss an idea. Pull requests should explain the change and how it was validated.

Raemi Labs and Raphael Cherfem Carlini maintain the project and control the roadmap and what is accepted into the official repository. A suggestion or pull request does not guarantee acceptance.

## License and credits

The source code is available under the [PolyForm Noncommercial License 1.0.0](LICENSE). Copyright belongs to **Raemi Labs and Raphael Cherfem Carlini**. The license allows noncommercial use, including personal use, research, study, private entertainment, and use by nonprofit organizations, provided its terms and copyright notice are preserved.

The license does not grant commercial use or distribution rights. To request a separate commercial license, contact [raphael@raemi.xyz](mailto:raphael@raemi.xyz). Forks and derivative works must also comply with the PolyForm license; maintainers retain control of the official repository.

## Support

For support, open a [GitHub issue](https://github.com/RaphaaCC/RadioStudio-Python/issues) or email [raphael@raemi.xyz](mailto:raphael@raemi.xyz).
