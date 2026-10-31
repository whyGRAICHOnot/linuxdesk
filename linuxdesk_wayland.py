#!/usr/bin/env python3
"""
LinuxDesk (Wayland, v3) - use an Android device as an extra touch monitor on KDE Plasma.

  krfb-virtualmonitor : creates the virtual monitor inside KWin (used only for that)
  ScreenCast portal   : gives us a PipeWire stream of that monitor (pick it once in
                        the KDE dialog; the choice is remembered afterwards)
  gst-launch + jpegenc: turns the stream into JPEG frames -> MJPEG over HTTP
  uinput touchscreen  : phone touches -> virtual touchscreen mapped to the monitor

Install:
  sudo apt install krfb python3-aiohttp python3-evdev python3-gi \
       gstreamer1.0-tools gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good
Run:
  python3 linuxdesk_wayland.py            (--help for options)
"""
import argparse, asyncio, json, os, random, shutil, socket, string, subprocess, sys, threading, time

from aiohttp import web, WSMsgType

M = 32767  # touch coordinate range

APT_LINE = ("sudo apt install krfb python3-aiohttp python3-evdev python3-gi gstreamer1.0-tools "
            "gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good")

UDEV_HELP = """
/dev/uinput is not accessible. One-time fix (then log out and in, or reboot):

  echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/70-uinput.rules
  echo uinput | sudo tee /etc/modules-load.d/uinput.conf
  sudo modprobe uinput
  sudo udevadm control --reload-rules && sudo udevadm trigger
"""

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<title>LinuxDesk</title>
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;touch-action:none;user-select:none;-webkit-user-select:none}
#v{position:fixed;inset:0;width:100%;height:100%;object-fit:contain}
#fs{position:fixed;top:8px;right:8px;width:44px;height:44px;border:0;border-radius:22px;z-index:5;
background:rgba(255,255,255,.18);color:#fff;font-size:22px;transition:opacity .6s}
#st{position:fixed;left:0;right:0;top:45%;text-align:center;color:#888;font:16px sans-serif;z-index:4}
</style></head><body>
<img id="v" draggable="false"><div id="st">Waiting for video&hellip;</div><button id="fs">&#x26F6;</button>
<script>
const pin = new URLSearchParams(location.search).get('pin') || '';
const img = document.getElementById('v'), st = document.getElementById('st'), fs = document.getElementById('fs');
function loadStream() { img.src = '/stream?pin=' + encodeURIComponent(pin) + '&t=' + Date.now(); }
img.onerror = () => setTimeout(loadStream, 2000);
loadStream();
setInterval(() => { st.style.display = img.naturalWidth ? 'none' : 'block'; }, 500);

const proto = location.protocol === 'https:' ? 'wss' : 'ws';
let tws;
function connectTouch() {
  tws = new WebSocket(proto + '://' + location.host + '/touch?pin=' + encodeURIComponent(pin));
  tws.onclose = () => setTimeout(connectTouch, 2000);
}
connectTouch();

function norm(t) {  // map a touch to 0..1 inside the (letterboxed) video
  const r = img.getBoundingClientRect(), iw = img.naturalWidth, ih = img.naturalHeight;
  if (!iw) return null;
  const s = Math.min(r.width / iw, r.height / ih), w = iw * s, h = ih * s;
  const ox = r.left + (r.width - w) / 2, oy = r.top + (r.height - h) / 2;
  return { x: Math.min(1, Math.max(0, (t.clientX - ox) / w)), y: Math.min(1, Math.max(0, (t.clientY - oy) / h)) };
}
function sendTouches(e, phase) {
  if (e.target === fs) return;
  e.preventDefault();
  if (!tws || tws.readyState !== 1) return;
  for (const t of e.changedTouches) {
    const p = norm(t); if (!p) continue;
    tws.send(JSON.stringify({ i: t.identifier, p: phase, x: p.x, y: p.y }));
  }
}
for (const [n, p] of [['touchstart','s'],['touchmove','m'],['touchend','e'],['touchcancel','e']])
  addEventListener(n, e => sendTouches(e, p), { passive: false, capture: true });
addEventListener('contextmenu', e => e.preventDefault());

fs.onclick = async () => {
  try { await document.documentElement.requestFullscreen(); if (navigator.wakeLock) await navigator.wakeLock.request('screen'); } catch (_) {}
  fs.style.opacity = 0;
};
setTimeout(() => fs.style.opacity = .35, 100);
</script></body></html>"""


# ---------------------------------------------------------------- touch input
class TouchPad:
    """Virtual multitouch screen via uinput.

    Deliberately a *pure* multitouch device (no ABS_X/ABS_Y single-touch axes):
    a device with plain X/Y axes plus a button can be mistaken for a gamepad by
    games / Steam Input.
    """

    def __init__(self):
        from evdev import UInput, AbsInfo, ecodes as e
        self.e = e
        cap = {
            e.EV_KEY: [e.BTN_TOUCH],
            e.EV_ABS: [
                (e.ABS_MT_SLOT, AbsInfo(0, 0, 9, 0, 0, 0)),
                (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
                (e.ABS_MT_POSITION_X, AbsInfo(0, 0, M, 0, 0, 0)),
                (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, M, 0, 0, 0)),
            ],
        }
        self.ui = UInput(cap, name="LinuxDesk Touch", input_props=[e.INPUT_PROP_DIRECT])
        self.slots, self.next_id = {}, 1

    def event(self, cid, phase, x, y):
        e, w = self.e, self.ui.write
        X, Y = int(x * M), int(y * M)
        if phase == "s":
            free = [s for s in range(10) if s not in self.slots.values()]
            if not free or cid in self.slots:
                return
            slot = free[0]
            self.slots[cid] = slot
            w(e.EV_ABS, e.ABS_MT_SLOT, slot)
            w(e.EV_ABS, e.ABS_MT_TRACKING_ID, self.next_id)
            self.next_id = self.next_id % 60000 + 1
            w(e.EV_ABS, e.ABS_MT_POSITION_X, X)
            w(e.EV_ABS, e.ABS_MT_POSITION_Y, Y)
            if len(self.slots) == 1:
                w(e.EV_KEY, e.BTN_TOUCH, 1)
        elif phase == "m" and cid in self.slots:
            w(e.EV_ABS, e.ABS_MT_SLOT, self.slots[cid])
            w(e.EV_ABS, e.ABS_MT_POSITION_X, X)
            w(e.EV_ABS, e.ABS_MT_POSITION_Y, Y)
        elif phase == "e" and cid in self.slots:
            w(e.EV_ABS, e.ABS_MT_SLOT, self.slots.pop(cid))
            w(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
            if not self.slots:
                w(e.EV_KEY, e.BTN_TOUCH, 0)
        self.ui.syn()

    def release_all(self):
        for cid in list(self.slots):
            self.event(cid, "e", 0, 0)


def find_devnode(name="LinuxDesk Touch", tries=40):
    """Find /dev/input/eventN via /proc (readable by everyone, unlike the event nodes)."""
    import re
    for _ in range(tries):
        try:
            text = open("/proc/bus/input/devices").read()
        except OSError:
            text = ""
        for block in text.split("\n\n"):
            if f'Name="{name}"' in block:
                m = re.search(r"\bevent(\d+)\b", block)
                if m:
                    return f"/dev/input/event{m.group(1)}"
        time.sleep(0.25)
    return None


def map_to_output(output):
    """Tell KWin to map our touchscreen onto the virtual monitor."""
    devpath = find_devnode()
    if not devpath:
        print("[linuxdesk] WARNING: touch device not found; map it by hand in "
              "System Settings > Input Devices > Touchscreen.")
        return
    node, err = os.path.basename(devpath), ""
    for _ in range(30):
        r = subprocess.run(["busctl", "--user", "set-property", "org.kde.KWin",
                            f"/org/kde/KWin/InputDevice/{node}", "org.kde.KWin.InputDevice",
                            "outputName", "s", output], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[linuxdesk] touch input mapped to {output}")
            return
        err = r.stderr.strip()
        time.sleep(0.5)
    print(f"[linuxdesk] WARNING: could not map touch to {output} automatically ({err}).\n"
          "  Do it by hand: System Settings > Input Devices > Touchscreen > 'LinuxDesk Touch' > Output.")


# ---------------------------------------------------------------- screen capture
class Portal:
    """Minimal xdg-desktop-portal ScreenCast client (returns a PipeWire fd + node)."""
    DEST, PATH = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    SC = "org.freedesktop.portal.ScreenCast"

    def __init__(self):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.n = 0
        threading.Thread(target=GLib.MainLoop().run, daemon=True).start()

    def _request(self, method, build, timeout=300):
        Gio = self.Gio
        self.n += 1
        token = f"linuxdesk{os.getpid()}x{self.n}"
        rpath = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        ev, box = threading.Event(), {}

        def on_resp(*args):
            box["v"] = args[5].unpack()
            ev.set()

        sub = self.bus.signal_subscribe(self.DEST, "org.freedesktop.portal.Request", "Response",
                                        rpath, None, Gio.DBusSignalFlags.NONE, on_resp)
        try:
            self.bus.call_sync(self.DEST, self.PATH, self.SC, method, build(token), None,
                               Gio.DBusCallFlags.NONE, -1, None)
            if not ev.wait(timeout):
                raise RuntimeError("timed out waiting for the screen-share dialog")
        finally:
            self.bus.signal_unsubscribe(sub)
        code, results = box["v"]
        if code != 0:
            raise RuntimeError("screen sharing was cancelled")
        return results

    def start(self, token_file, cursor_mode=1):
        V = self.GLib.Variant
        saved = None
        try:
            saved = open(token_file).read().strip() or None
        except OSError:
            pass

        res = self._request("CreateSession", lambda t: V("(a{sv})", ({
            "handle_token": V("s", t), "session_handle_token": V("s", "s" + t)},)))
        session = res["session_handle"]

        def select(t):
            o = {"handle_token": V("s", t), "types": V("u", 1), "multiple": V("b", False),
                 "cursor_mode": V("u", cursor_mode), "persist_mode": V("u", 2)}
            if saved:
                o["restore_token"] = V("s", saved)
            return V("(oa{sv})", (session, o))

        self._request("SelectSources", select)
        res = self._request("Start", lambda t: V("(osa{sv})", (session, "", {"handle_token": V("s", t)})))
        node = res["streams"][0][0]
        if res.get("restore_token"):
            os.makedirs(os.path.dirname(token_file), exist_ok=True)
            with open(token_file, "w") as f:
                f.write(res["restore_token"])

        reply, fds = self.bus.call_with_unix_fd_list_sync(
            self.DEST, self.PATH, self.SC, "OpenPipeWireRemote",
            V("(oa{sv})", (session, {})), None, self.Gio.DBusCallFlags.NONE, -1, None, None)
        fd = fds.get(reply.get_child_value(0).get_handle())
        return fd, node


class Hub:
    """Runs GStreamer and fans the latest JPEG frame out to every viewer."""

    def __init__(self, fd, node, args):
        self.fd, self.node, self.args = fd, node, args
        self.frame, self.seq = b"", 0
        self.viewers = 0
        self.cond = asyncio.Condition()
        self.proc = None

    async def run(self):
        a = self.args
        w, h = map(int, a.size.lower().split("x"))
        scale = []
        if a.stream_width and a.stream_width != w:
            sh_ = round(a.stream_width * h / w / 2) * 2
            scale = ["!", "videoscale", "!", f"video/x-raw,width={a.stream_width},height={sh_}"]
        cmd = (["gst-launch-1.0", "-q", "pipewiresrc", f"fd={self.fd}", f"path={self.node}",
                "do-timestamp=true", "always-copy=true", "keepalive-time=200",
                "!", "queue", "leaky=downstream", "max-size-buffers=2",
                "!", "videorate", "drop-only=true", f"max-rate={a.fps}",
                "!", "videoconvert"] + scale +
               ["!", "jpegenc", f"quality={a.quality}", "!", "fdsink", "fd=1"])
        env = dict(os.environ)
        env.setdefault("GST_DEBUG", "2")  # warnings + errors go to the terminal (stderr)
        print("[linuxdesk] starting capture:", " ".join(cmd))
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, pass_fds=(self.fd,), env=env)
        buf = bytearray()
        while True:
            chunk = await self.proc.stdout.read(262144)
            if not chunk:
                print("[linuxdesk] GStreamer stopped (see messages above)")
                os.kill(os.getpid(), 2)
                return
            buf += chunk
            while (i := buf.find(b"\xff\xd9")) >= 0:  # JPEG end-of-image marker
                frame = bytes(buf[: i + 2])
                del buf[: i + 2]
                async with self.cond:
                    self.frame, self.seq = frame, self.seq + 1
                    self.cond.notify_all()

    def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()


# ---------------------------------------------------------------- web server
def make_app(args, pad, vm, hub):
    app = web.Application()

    def ok(req):
        return req.query.get("pin", "") == args.pin

    async def index(req):
        if not ok(req):
            return web.Response(status=403, text="Wrong or missing PIN. Use the URL printed in the terminal.")
        return web.Response(text=PAGE, content_type="text/html")

    async def stream(req):
        if not ok(req):
            return web.Response(status=403)
        resp = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame", "Cache-Control": "no-cache"})
        await resp.prepare(req)
        last = -1
        hub.viewers += 1
        try:
            while True:
                async with hub.cond:
                    await hub.cond.wait_for(lambda: hub.seq > 0 and hub.seq != last)
                    frame, last = hub.frame, hub.seq
                await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            hub.viewers -= 1
        return resp

    async def touch(req):
        if not ok(req):
            return web.Response(status=403)
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        async for m in ws:
            if m.type == WSMsgType.TEXT and pad:
                try:
                    d = json.loads(m.data)
                    pad.event(d["i"], d["p"], d["x"], d["y"])
                except Exception as ex:
                    print("[linuxdesk] touch error:", ex)
        if pad:
            pad.release_all()
        return ws

    async def on_start(app):
        app["cap"] = asyncio.create_task(hub.run())

        async def watch():
            while vm.poll() is None:
                await asyncio.sleep(1)
            print("[linuxdesk] krfb-virtualmonitor exited")
            os.kill(os.getpid(), 2)
        app["wd"] = asyncio.create_task(watch())

        async def stats():
            last = hub.seq
            while True:
                await asyncio.sleep(2)
                n, last = hub.seq - last, hub.seq
                print(f"[linuxdesk] capture: {n / 2:.0f} fps | viewers: {hub.viewers} | "
                      f"last frame: {len(hub.frame) // 1024} KB")
        if args.stats:
            app["st"] = asyncio.create_task(stats())

    async def cleanup(app):
        hub.stop()
        if vm.poll() is None:
            vm.terminate()
        if pad:
            pad.ui.close()
        print("[linuxdesk] stopped, virtual monitor removed")

    app.add_routes([web.get("/", index), web.get("/stream", stream), web.get("/touch", touch)])
    app.on_startup.append(on_start)
    app.on_cleanup.append(cleanup)
    return app


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def wait_port(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def load_or_create_pin():
    """Keep the same PIN between runs so saved/bookmarked URLs keep working."""
    import secrets
    path = os.path.expanduser("~/.config/linuxdesk/pin")
    try:
        pin = open(path).read().strip()
        if pin:
            return pin
    except OSError:
        pass
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/O/1/l/i look-alikes
    pin = "".join(secrets.choice(alphabet) for _ in range(8))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(pin)
    os.chmod(path, 0o600)
    return pin


def gst_has(element):
    return subprocess.run(["gst-inspect-1.0", element], capture_output=True).returncode == 0


def main():
    p = argparse.ArgumentParser(description="Use an Android device as a second touch monitor on KDE Wayland.")
    p.add_argument("--size", default="1920x1080", help="virtual monitor resolution")
    p.add_argument("--fps", type=int, default=30, help="max frames per second (default 30)")
    p.add_argument("--quality", type=int, default=65, help="JPEG quality 1-100 (default 65)")
    p.add_argument("--stream-width", type=int, default=0,
                   help="downscale the stream to this width to save bandwidth (e.g. 1280)")
    p.add_argument("--name", default="android")
    p.add_argument("--vnc-port", type=int, default=5900, help="internal port used by krfb-virtualmonitor")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cursor", choices=["hidden", "embedded", "metadata"], default="hidden",
                   help="hidden (default): no mouse cursor in the stream. embedded: draw it into the "
                        "video. metadata: cursor sent separately (not drawn); a different code path in KDE")
    p.add_argument("--stats", action="store_true",
                   help="print capture fps / viewer count every 2 seconds (for debugging freezes)")
    p.add_argument("--no-touch", action="store_true",
                   help="disable touch input (use to test whether touch causes game input problems)")
    p.add_argument("--pin", default=None, help="PIN for the web page (default: saved in ~/.config/linuxdesk/pin)")
    args = p.parse_args()
    args.pin = args.pin or load_or_create_pin()

    if not shutil.which("krfb-virtualmonitor") or not shutil.which("gst-launch-1.0"):
        sys.exit("Missing packages. Install with:\n  " + APT_LINE)
    for el in ("pipewiresrc", "jpegenc", "videoconvert", "videorate"):
        if not gst_has(el):
            sys.exit(f"GStreamer element '{el}' is missing. Install with:\n  " + APT_LINE)
    try:
        import evdev  # noqa: F401
        import gi  # noqa: F401
    except ImportError:
        sys.exit("Missing python modules. Install with:\n  " + APT_LINE)

    pad = None
    if not args.no_touch:
        if not os.access("/dev/uinput", os.W_OK):
            sys.exit(UDEV_HELP)
        pad = TouchPad()

    # Internal VNC password: random and never shown, so nobody can use that port.
    vnc_pw = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    vm = subprocess.Popen(["krfb-virtualmonitor", "--resolution", args.size, "--name", args.name,
                           "--password", vnc_pw, "--port", str(args.vnc_port)],
                          stderr=subprocess.DEVNULL)
    if not wait_port(args.vnc_port):
        vm.terminate()
        sys.exit("krfb-virtualmonitor did not start (is another one already running?)")
    time.sleep(1.5)

    print(f"\n[linuxdesk] A KDE dialog will ask which screen to share.\n"
          f"            Choose the screen named 'Virtual-{args.name}' (only needed the first time).\n")
    try:
        portal = Portal()  # must stay referenced: KDE ends the share if this connection goes away
        fd, node = portal.start(os.path.expanduser("~/.config/linuxdesk/restore_token"),
                                {"hidden": 1, "embedded": 2, "metadata": 4}[args.cursor])
    except Exception as ex:
        vm.terminate()
        sys.exit(f"Screen capture setup failed: {ex}")

    if pad:
        threading.Thread(target=map_to_output, args=(f"Virtual-{args.name}",), daemon=True).start()

    hub = Hub(fd, node, args)
    hub.portal = portal
    print(f"""
  Virtual monitor '{args.name}' ({args.size}) is ready.
  Drag it into place in System Settings > Display & Monitor.

  Open on your Android device (same Wi-Fi):

    http://{local_ip()}:{args.port}/?pin={args.pin}

  USB instead:  adb reverse tcp:{args.port} tcp:{args.port}  then open
    http://localhost:{args.port}/?pin={args.pin}

  Ctrl+C to stop.
""")
    web.run_app(make_app(args, pad, vm, hub), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
