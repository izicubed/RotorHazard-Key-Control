#!/usr/bin/env python3
'''
KEY CONTROL - Pi-side forwarder.

Reads the two-key USB keyboards plugged into this machine with evdev, grabs
them exclusively (so presses never leak into a console), and forwards every
key-down as a Socket.IO event to the RotorHazard server running the
key_control (KEY CONTROL) plugin.

Keyboard order: devices are sorted by their physical USB topology path
('phys'), so keyboard numbering follows the USB ports and survives reboots
and re-enumeration. KB1 = lowest port path. Use --list to see the order and
--learn to discover which keycodes your keyboards emit.

Usage:
    python3 keyboard_forwarder.py --server http://<rh-host>:5000
    python3 keyboard_forwarder.py --list            # show detected keyboards
    python3 keyboard_forwarder.py --learn           # print keycodes as you press
    python3 keyboard_forwarder.py --server ... --add-keys KEY_1,KEY_KP1 --del-keys KEY_2,KEY_KP2

Dependencies (Raspberry Pi OS):
    sudo apt install -y python3-evdev python3-socketio python3-websocket
    (or: pip3 install evdev "python-socketio[client]" --break-system-packages)
'''

import argparse
import json
import logging
import os
import socket as pysocket
import sys
import threading
import time

try:
    import evdev
    from evdev import InputDevice, categorize, ecodes
except ImportError:
    print('python3-evdev is required:  sudo apt install python3-evdev', file=sys.stderr)
    sys.exit(1)

log = logging.getLogger('kb-forwarder')

EV_HELLO = 'button_kb_hello'
EV_HEARTBEAT = 'button_kb_heartbeat'
EV_KEY = 'button_kb_event'

# keycodes accepted out of the box; extend with --add-keys / --del-keys.
DEFAULT_ADD_KEYS = ['KEY_1', 'KEY_KP1', 'KEY_A', 'KEY_ENTER', 'KEY_KPENTER',
                    'KEY_VOLUMEUP', 'KEY_PAGEUP', 'KEY_UP', 'KEY_F1']
DEFAULT_DEL_KEYS = ['KEY_2', 'KEY_KP2', 'KEY_B', 'KEY_BACKSPACE', 'KEY_DELETE',
                    'KEY_VOLUMEDOWN', 'KEY_PAGEDOWN', 'KEY_DOWN', 'KEY_F2']

HEARTBEAT_SEC = 5
RESCAN_SEC = 10          # watch for keyboards (re)appearing


def key_names(code):
    '''evdev KEY code -> list of names (ecodes.KEY[code] may be str or list).'''
    name = ecodes.KEY.get(code)
    if name is None:
        return []
    return name if isinstance(name, list) else [name]


def usb_sort_key(dev):
    return (dev.phys or '', dev.path)


def phys_root(phys):
    '''Group multiple event nodes of one physical USB device.'''
    return (phys or '').split('/')[0]


def find_keyboards(name_filter=None):
    '''Detect candidate button keyboards: EV_KEY devices, deduped per physical
    USB device, sorted by USB port path.'''
    found = {}
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError) as ex:
            log.debug('skip %s: %s', path, ex)
            continue
        caps = dev.capabilities()
        if ecodes.EV_KEY not in caps:
            dev.close()
            continue
        keys = caps[ecodes.EV_KEY]
        # keyboards, not mice: must carry ordinary key codes
        if not any(c < 0x100 for c in keys):
            dev.close()
            continue
        if name_filter:
            if name_filter.lower() not in (dev.name or '').lower():
                dev.close()
                continue
        elif not (dev.phys or '').startswith('usb'):
            # without an explicit name filter, only USB devices qualify -
            # keeps built-ins like vc4-hdmi CEC out of the keyboard list
            dev.close()
            continue
        root = phys_root(dev.phys)
        # prefer the node with the most keys per physical device
        if root in found and len(found[root].capabilities()[ecodes.EV_KEY]) >= len(keys):
            dev.close()
            continue
        if root in found:
            found[root].close()
        found[root] = dev
    return sorted(found.values(), key=usb_sort_key)


CONFIG_PATH = os.path.expanduser('~/key_control/forwarder_config.json')
CTRL_PORT = 8737


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=2)
    except OSError as ex:
        log.warning('cannot save config %s: %s', CONFIG_PATH, ex)


class Forwarder:
    def __init__(self, args):
        self.args = args
        self.add_keys = set(args.add_keys)
        self.del_keys = set(args.del_keys)
        self.sio = None
        self.devices = []            # index in this list == kb index
        self.readers = {}            # dev.path -> Thread
        self.lock = threading.Lock()
        self.host = pysocket.gethostname()
        # server URL priority: saved config (set remotely by the plugin's
        # "Link Key Control") > --server argument
        self.server = load_config().get('server') or args.server
        self.new_server = None       # set by the control endpoint

    # ------------------------------------------------------------- socket io
    def connect(self):
        import socketio  # lazy: not needed for --list / --learn
        self.sio = socketio.Client(reconnection=True, reconnection_delay=2,
                                   reconnection_delay_max=15, logger=False)

        @self.sio.event
        def connect():
            log.info('connected to %s', self.server)
            self.send_hello()

        @self.sio.event
        def disconnect():
            log.warning('disconnected from server')

        while True:
            try:
                self.sio.connect(self.server,
                                 transports=['websocket', 'polling'])
                return
            except Exception as ex:
                log.warning('connect failed (%s), retrying in 5s', ex)
                time.sleep(5)
                if self.new_server:  # re-pointed while unreachable
                    self.server = self.new_server
                    self.new_server = None

    def set_server(self, url):
        '''Control endpoint: persist a new server URL and reconnect.'''
        save_config({'server': url})
        self.new_server = url
        log.info('re-pointing to %s', url)

    def apply_new_server(self):
        if not self.new_server:
            return
        url, self.new_server = self.new_server, None
        self.server = url
        try:
            if self.sio and self.sio.connected:
                self.sio.disconnect()
        except Exception:
            pass
        self.connect()
        self.send_hello()

    # --------------------------------------------------------- control http
    def start_ctrl_server(self):
        import http.server

        fwd = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/status':
                    self._json(200, {
                        'host': fwd.host,
                        'server': fwd.server,
                        'connected': bool(fwd.sio and fwd.sio.connected),
                        'devices': fwd.device_summary(),
                    })
                else:
                    self._json(404, {'error': 'unknown path'})

            def do_POST(self):
                if self.path != '/server':
                    self._json(404, {'error': 'unknown path'})
                    return
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    data = json.loads(self.rfile.read(length) or b'{}')
                    url = str(data.get('url') or '')
                except (ValueError, TypeError):
                    self._json(400, {'error': 'bad json'})
                    return
                if not url.startswith('http://') and not url.startswith('https://'):
                    self._json(400, {'error': 'url must be http(s)://host:port'})
                    return
                fwd.set_server(url)
                self._json(200, {'ok': True, 'server': url})

            def log_message(self, fmt, *a):
                log.debug('ctrl: ' + fmt, *a)

        try:
            srv = http.server.ThreadingHTTPServer(('0.0.0.0', CTRL_PORT), Handler)
        except OSError as ex:
            # port busy (another forwarder instance) - endpoint is optional
            log.warning('control endpoint unavailable on :%d (%s)', CTRL_PORT, ex)
            return
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name='ctrl-http').start()
        log.info('control endpoint on :%d (/status, POST /server)', CTRL_PORT)

    def device_summary(self):
        with self.lock:
            return [{'kb': i, 'name': d.name, 'phys': d.phys, 'path': d.path}
                    for i, d in enumerate(self.devices)]

    def payload(self):
        return {'host': self.host, 'devices': self.device_summary(),
                'server': self.server, 'ctrl_port': CTRL_PORT}

    def send_hello(self):
        self.emit(EV_HELLO, self.payload())

    def emit(self, event, data):
        try:
            if self.sio and self.sio.connected:
                self.sio.emit(event, data)
        except Exception as ex:
            log.debug('emit %s failed: %s', event, ex)

    # -------------------------------------------------------------- keyboards
    def rescan(self):
        keyboards = find_keyboards(self.args.name_filter)
        with self.lock:
            known = {d.path for d in self.devices}
            new = [d for d in keyboards if d.path not in known]
            if not new and len(keyboards) == len(self.devices):
                for d in keyboards:
                    d.close()
                return
            # full re-index so kb numbering always matches USB port order
            self.devices = keyboards
        for dev in keyboards:
            if dev.path not in self.readers or not self.readers[dev.path].is_alive():
                t = threading.Thread(target=self.read_device, args=(dev,),
                                     daemon=True, name='kb-' + dev.path)
                self.readers[dev.path] = t
                t.start()
        log.info('keyboards: %s',
                 ', '.join('KB{} {} ({})'.format(i + 1, d.name, d.phys)
                           for i, d in enumerate(self.devices)) or 'none')
        self.send_hello()

    def kb_index(self, dev):
        with self.lock:
            for i, d in enumerate(self.devices):
                if d.path == dev.path:
                    return i
        return None

    def read_device(self, dev):
        try:
            dev.grab()  # exclusive: keep presses out of any console/session
        except (OSError, IOError) as ex:
            log.warning('cannot grab %s (%s) - continuing ungrabbed', dev.path, ex)
        log.info('reading %s (%s)', dev.name, dev.path)
        try:
            for event in dev.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                key = categorize(event)
                if key.keystate != key.key_down:
                    continue
                names = set(key_names(event.code))
                kb = self.kb_index(dev)
                if kb is None:
                    continue
                if names & self.add_keys:
                    button = 'add'
                elif names & self.del_keys:
                    button = 'del'
                else:
                    log.info('KB%d: unmapped key %s (add with --add-keys/--del-keys)',
                             kb + 1, sorted(names))
                    continue
                log.info('KB%d %s (%s)', kb + 1, button, sorted(names))
                self.emit(EV_KEY, {'kb': kb, 'button': button,
                                   'ts': int(time.time() * 1000)})
        except OSError as ex:
            log.warning('device %s vanished (%s)', dev.path, ex)
            with self.lock:
                self.devices = [d for d in self.devices if d.path != dev.path]
            self.send_hello()

    # ------------------------------------------------------------------- run
    def run(self):
        self.start_ctrl_server()
        self.connect()
        self.rescan()
        last_beat = last_scan = 0.0
        while True:
            now = time.monotonic()
            if self.new_server:
                try:
                    self.apply_new_server()
                except Exception:
                    log.exception('re-point failed')
            if now - last_beat >= HEARTBEAT_SEC:
                self.emit(EV_HEARTBEAT, self.payload())
                last_beat = now
            if now - last_scan >= RESCAN_SEC:
                try:
                    self.rescan()
                except Exception:
                    log.exception('rescan failed')
                last_scan = now
            time.sleep(1)


def cmd_list(args):
    keyboards = find_keyboards(args.name_filter)
    if not keyboards:
        print('No keyboards found. Are you in the "input" group (or root)?')
        return
    for i, d in enumerate(keyboards):
        print('KB{}  {}  phys={}  path={}'.format(i + 1, d.name, d.phys, d.path))
        d.close()


def cmd_learn(args):
    keyboards = find_keyboards(args.name_filter)
    if not keyboards:
        print('No keyboards found.')
        return
    print('Press buttons; Ctrl-C to stop.\n')
    import selectors
    sel = selectors.DefaultSelector()
    for i, d in enumerate(keyboards):
        sel.register(d, selectors.EVENT_READ, i)
    try:
        while True:
            for key_sel, _ in sel.select():
                dev, i = key_sel.fileobj, key_sel.data
                for event in dev.read():
                    if event.type == ecodes.EV_KEY:
                        k = categorize(event)
                        if k.keystate == k.key_down:
                            print('KB{}  {}  -> use in --add-keys / --del-keys'
                                  .format(i + 1, key_names(event.code)))
    except KeyboardInterrupt:
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--server', default=os.environ.get('RH_SERVER', 'http://localhost:5000'),
                   help='RotorHazard server URL (default: %(default)s)')
    p.add_argument('--name-filter', default=os.environ.get('KB_NAME_FILTER', ''),
                   help='only use input devices whose name contains this substring')
    p.add_argument('--add-keys', default=os.environ.get('KB_ADD_KEYS', ''),
                   help='comma-separated keycodes for the ADD-lap button')
    p.add_argument('--del-keys', default=os.environ.get('KB_DEL_KEYS', ''),
                   help='comma-separated keycodes for the DELETE-lap button')
    p.add_argument('--list', action='store_true', help='list detected keyboards and exit')
    p.add_argument('--learn', action='store_true', help='print keycodes as you press buttons')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    args.add_keys = [k.strip() for k in args.add_keys.split(',') if k.strip()] or DEFAULT_ADD_KEYS
    args.del_keys = [k.strip() for k in args.del_keys.split(',') if k.strip()] or DEFAULT_DEL_KEYS

    if args.list:
        cmd_list(args)
    elif args.learn:
        cmd_learn(args)
    else:
        Forwarder(args).run()


if __name__ == '__main__':
    main()
