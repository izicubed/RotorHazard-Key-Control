# KEY CONTROL

Physical two-key USB button keyboards as per-pilot lap marshals for RotorHazard.

* **Key 1 — add / confirm lap**
* **Key 2 — delete lap** (the seat's last recorded lap; also works between
  race stop and save, switchable)

## Work modes

Switchable from the panel header (segmented switch, like the Auto Marshalling
school selector) or Settings → KEY CONTROL.

### Manual — buttons only

RotorHazard's own RSSI passes are **suppressed** the moment they are recorded;
every counted lap comes from the buttons. Key 1 adds a lap instantly (same as
the Run page **+ Lap** button, lap source *manual*), key 2 deletes.

### Semi — confirm the timer (default)

RotorHazard counts laps as usual; key presses **confirm** them inside a
**± threshold window** (default 2 s, editable in the panel header and in
Settings). Each lap gets a colored mark in the panel:

| Mark | Meaning |
|------|---------|
| 🟢 green | timer lap confirmed by a key press within the window |
| 🟡 yellow | timer lap with no confirming key press (unconfirmed) |
| 🔵 blue | key press with no timer lap inside the window — a manual lap is added at the moment the key was pressed |
| 🔴 red | lap removed with the delete key (time shown ~~struck through~~, like RotorHazard's own deleted laps) |

Press-before-lap and press-after-lap both confirm, as long as the two are
within the window of each other.

## Keyboard → pilot mapping

By default **keyboard N controls the Nth occupied seat** (frequency + pilot)
of the current heat, in seat order — with pilots on R1, R3, R6, R8: KB1→R1,
KB2→R3, KB3→R6, KB4→R8. The mapping follows the heat automatically.

### Calibration

Panel → **Calibrate**: for each occupied seat in turn, press any key on the
keyboard that should own it. The bindings are stored as fixed-seat pins
(Settings → "Keyboard N fixed seat"). **Auto map** clears all pins.

## Key Control IP / Link

The forwarder runs a small control endpoint on port **8737**. Enter the
forwarder machine's IP in Settings → **Key Control IP** (e.g. 192.168.100.53)
and press **Link** (panel) or **Link Key Control now** (Settings): the server
contacts the forwarder and points it at itself. The URL is persisted on the
forwarder (`~/key_control/forwarder_config.json`), so it survives reboots —
no SSH needed when moving the keyboards between timers.

```
GET  http://<kc-ip>:8737/status            → {host, server, connected, devices}
POST http://<kc-ip>:8737/server {url: ...} → re-point + persist
```

## Architecture

```
4 × two-key USB keyboards
        │ (evdev, exclusive grab, USB-port-ordered)
Key Control box (Raspberry Pi, e.g. keycontrol.local)
   keyboard_forwarder.py  +  control endpoint :8737
        │ Socket.IO: button_kb_event {kb, button, ts}
RotorHazard server — this plugin
   Manual: suppress timer laps, buttons add/delete
   Semi:   confirm timer laps in a ±window, add on miss
```

Works on RotorHazard 4.4+ (the lap-delete API change in newer servers is
detected at runtime).

## Install — server side

Copy `custom_plugins/key_control` into your RotorHazard data dir's `plugins/`
folder and restart the server.

## Install — keyboard Pi

```bash
scp -r custom_plugins/key_control/pi_forwarder pi@<kc-ip>:
ssh pi@<kc-ip>
cd pi_forwarder && ./install.sh http://<rotorhazard-host>:5000
```

The installer sets up the systemd service `key-control-forwarder` (starts on
boot, reconnects automatically). Common two-key macro pads send `1`/`2`,
`a`/`b` or volume up/down — accepted out of the box; run
`python3 ~/key_control/keyboard_forwarder.py --learn` to discover other
keycodes and pass them via `--add-keys` / `--del-keys`.

## Options (Settings → KEY CONTROL)

| Option | Default | Meaning |
|--------|---------|---------|
| Enabled | on | master switch |
| Work mode | Semi | Manual (buttons only) / Semi (confirm timer laps) |
| Semi confirmation window | 2 s | ± window for confirmation / manual add |
| Key Control IP | — | forwarder address for the Link button |
| Number of keyboards | 4 | how many keyboards the forwarder carries |
| Show a notification | on | UI message on every button action |
| Voice callout | off | speak "<callsign> lap added/confirmed/deleted" |
| Allow deleting after race stop | on | delete key works until laps are saved |
| Keyboard N fixed seat | 0 (auto) | pin keyboard N to a seat (Calibrate fills these) |
| Panel theme | dark | Run-page panel colours (dark/light/auto) |

## Safety behaviour

* Adding a lap requires a **running** race; presses at any other time only
  produce a notice.
* The timer's **Minimum Lap Time** still applies to button-added laps — an
  under-minimum lap is discarded by RotorHazard itself (shown red).
* 250 ms per-button debounce absorbs key repeat and switch bounce.
* Unmapped keyboards (heat has fewer pilots) are ignored with a notice.
* All marks are per-race and reset when a race is staged or laps discarded.
