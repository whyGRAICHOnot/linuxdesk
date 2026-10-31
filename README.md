# LinuxDesk

Use an Android phone or tablet as an extra touch screen for a **Kubuntu / KDE Plasma (Wayland)** computer, a bit like Spacedesk does on Windows.

It creates a real extra monitor in KDE, streams it to your Android device's browser over Wi-Fi (or USB), and turns your finger touches into touch input on that monitor. There is nothing to install on the Android device: it only needs Chrome.

> **Status:** a hobby project, tested on one laptop (Kubuntu, Plasma 6.6.6, Wayland). It may need tweaking on other setups. See *Known issues* below.

## Requirements

- Kubuntu, or another Ubuntu/Debian-based system with **KDE Plasma running on Wayland** (not X11)
- An Android device with Chrome, on the **same Wi-Fi** as the computer (or connected by USB, see below)

## Install

### Quick install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/whyGRAICHOnot/linuxdesk/main/linuxdesk_wayland.sh -o linuxdesk_wayland.sh
less linuxdesk_wayland.sh      # optional: read it first (press q to quit)
bash linuxdesk_wayland.sh
```

The installer downloads `linuxdesk_wayland.py` by itself, so this is all you need. Reading the script before running it is a good habit for anything that uses `sudo`.

### Manual install (no internet link needed)

You only need two files, saved in the **same folder**:

- `linuxdesk_wayland.sh`
- `linuxdesk_wayland.py`

Then open a terminal in that folder and run:

```bash
bash linuxdesk_wayland.sh
```

The installer asks for your password once. It installs the needed packages, allows touch input, and creates a `linuxdesk` command. **Log out and back in once** afterwards, because the touch permission only activates on a new login.

## Use

```bash
linuxdesk
```

1. The first time, a KDE dialog asks which screen to share. Choose **Virtual-android**. KDE remembers this afterwards.
2. The terminal prints an address like `http://192.168.1.50:8765/?pin=abcd2345`. Open it in Chrome on your Android device.
3. Tap the **fullscreen** button in the corner of the page.
4. A new screen called "android" appears in KDE. Arrange it in *System Settings → Display & Monitor*, then drag windows onto it.
5. Press **Ctrl+C** in the terminal to stop. The extra monitor disappears.

The PIN is created once and saved in `~/.config/linuxdesk/pin`, so the address stays the same between runs. You can bookmark it or add it to the Android home screen.

### Options

| Option | What it does |
|---|---|
| `--size 1600x900` | Resolution of the extra monitor (default `1920x1080`) |
| `--fps 60` | Maximum frames per second (default `30`) |
| `--quality 50` | JPEG quality 1–100 (default `65`). Lower uses less bandwidth |
| `--stream-width 1280` | Shrink the stream to save bandwidth on slow Wi-Fi |
| `--cursor hidden\|embedded\|metadata` | How the mouse pointer is handled (default `hidden`) |
| `--no-touch` | Turn off touch input |
| `--stats` | Print capture fps every 2 seconds (for debugging) |
| `--pin MYPIN` | Choose your own PIN |
| `--port 9000` | Use a different web port (default `8765`) |

### USB instead of Wi-Fi

With USB debugging enabled on the Android device and `adb` installed on the computer:

```bash
adb reverse tcp:8765 tcp:8765
```

Then open the `http://localhost:8765/?pin=...` address shown in the terminal on the Android device.

## Controls

The Android screen works as a real touch screen: tap to click, press and hold for right click, drag to move or scroll, and use two fingers for gestures that your apps support. There is **no keyboard input** yet.

## Known issues

- **The picture freezes while the mouse pointer is on the extra screen.** KDE stops sending new frames for that screen while the pointer is there. Touch works fine. Keep the mouse on your main screen, and use touch on the Android device. Moving the mouse back fixes it.
- Wayland only. The old X11 version is not included here.
- Quality depends on Wi-Fi. If it stutters, try `--stream-width 1280` or `--quality 50`.
- The stream is not encrypted and the only protection is the PIN. Use it on a home network you trust, not on public Wi-Fi.

## Troubleshooting

| Problem | Try this |
|---|---|
| `/dev/uinput is not accessible` | Log out and back in after the install. If it still happens, run `bash linuxdesk_wayland.sh` again. |
| "Wrong or missing PIN" | Use the exact address printed by the program that is running right now. |
| Page won't open on the phone | Check both devices are on the same Wi-Fi. If you use `ufw`: `sudo ufw allow 8765/tcp` |
| Black or blank picture | Run `linuxdesk --stats` and send the terminal output to whoever maintains this. |
| Taps land in the wrong place | In *System Settings → Input Devices → Touchscreen*, set the output of "LinuxDesk Touch" to the Virtual-android screen. |
| `linuxdesk: command not found` | Log out and back in, or run `~/.local/bin/linuxdesk`. |
| Screen stuck or pointer trapped after a crash | Run `pkill -f linuxdesk; pkill krfb-virtualmonitor` |

## Update

Replace `linuxdesk_wayland.py` with the new version and run `bash linuxdesk_wayland.sh` again.

## Uninstall

```bash
rm ~/.local/bin/linuxdesk
rm -r ~/.config/linuxdesk
sudo rm /etc/udev/rules.d/70-uinput.rules /etc/modules-load.d/uinput.conf
```

The packages installed by `linuxdesk_wayland.sh` can stay, or be removed with `sudo apt remove` if you don't use them for anything else.

## How it works

`krfb-virtualmonitor` makes the extra monitor inside KDE. The KDE screen-sharing portal gives the program a PipeWire stream of it, GStreamer turns that into JPEG frames, and a small web server sends them to the browser. Touches from the browser go to a virtual multitouch screen created with `/dev/uinput`, which KDE maps onto the extra monitor.
