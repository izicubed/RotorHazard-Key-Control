# KEY CONTROL

Per-pilot lap marshalling for RotorHazard, from a two-key USB keyboard at the
timer or from a judge's phone anywhere on the internet.

* **Key 1 / ADD LAP** — add or confirm a lap
* **Key 2 / REMOVE LAP** — delete the seat's last recorded lap (also works
  between race stop and save, switchable)

One marshal watches one pilot, and every press is dated to the moment it
happened rather than the moment it arrived.

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

## Using one half only

The panel footer carries a **Using** row with a switch for each half:
**Keyboards** and **Cloud judges**. A half that is switched off leaves the
panel completely, so an event run entirely from phones never shows the
keyboard rows, the Calibrate flow or the forwarder link, and an event with no
internet never shows the judge links. The switches themselves stay put, and
the same two settings live in Settings → KEY CONTROL.

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

## Cloud judges — one phone per pilot

Settings → **Cloud judges**, or the **Cloud judges** button in the Run-page
panel. The plugin opens a room on the judge relay
([judge.airmode.app](https://judge.airmode.app), see [`cloud/`](https://github.com/izicubed/RotorHazard-Key-Control/tree/main/cloud)) and
prints **one link per occupied seat**. Send each link to the marshal watching
that channel; they open it on any phone and get a full-screen **ADD LAP** and
**REMOVE LAP** pair for exactly that pilot, with the callsign, channel, pass
count and last/best lap above the buttons.

The timer **dials out**, so there is no port forwarding, no public IP and no
inbound firewall rule. A judge link is per channel, so it keeps working as
heats change and the pilot on that channel changes with them.

* Both judge pages carry the **race clock**, counting down on a timed format
  and up on an open one. It ticks on the phone between updates and freezes
  where the race stopped.
* **New room** (panel, or Settings → *New cloud room code*) issues a fresh
  code and invalidates every link handed out so far.
* The read-only board at `/r/<room code>` shows every seat at once, for the
  race director's own screen.
* Taps made while the phone has no signal are kept on the phone and sent when
  it comes back, still carrying the time they were made.

### Why a late tap still lands on the right second

A phone sends **how long ago** its judge tapped, not a clock reading. The relay
adds the time the command waited in its queue, and the plugin records the lap
that far back on the race clock. Network latency and wrong phone clocks
therefore never move a lap time.

## Architecture

```
4 × two-key USB keyboards                    judges' phones
        │ (evdev, exclusive grab)                  │ HTTPS, tap age travels with the press
Key Control box (Raspberry Pi)              judge.airmode.app (Vercel + Postgres)
   keyboard_forwarder.py  +  :8737             room, per-seat judge tokens, press queue
        │ Socket.IO: button_kb_event                │ the timer polls out for presses
        └──────────────► RotorHazard server — this plugin ◄────────┘
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
| Keyboard control | on | use the USB button keyboards; off hides them from the panel |
| Cloud judges | off | open a judge room and issue one phone link per seat |
| Judge relay address | https://judge.airmode.app | where the judge pages live |
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
* A judge link only ever reaches its own seat; the room code alone grants no
  control, and the read-only board never carries judge tokens.
