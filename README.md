# KEY CONTROL

Physical two-key USB button keyboards as per-pilot lap marshals for RotorHazard.

* **Key 1 — add lap**: records a manual lap for the mapped seat, exactly like
  the Run page **+ Lap** button (lap source *manual*).
* **Key 2 — delete lap**: deletes the mapped seat's last recorded lap, exactly
  like the Run page lap **×** button. Also works between race stop and save
  (switchable).

## How it maps keyboards to pilots

By default **keyboard N controls the Nth occupied seat** (seat with a
frequency and a pilot) of the current heat, in seat order. Racing four pilots
on R1, R3, R6, R8:

| Keyboard | Seat | Channel |
|----------|------|---------|
| KB1 | 1st occupied | R1 |
| KB2 | 2nd occupied | R3 |
| KB3 | 3rd occupied | R6 |
| KB4 | 4th occupied | R8 |

The mapping follows the heat automatically. A per-keyboard **fixed seat**
option (Settings → KEY CONTROL) pins a keyboard to one physical seat instead.

The Run page shows a **Key Control** panel — same look, palette and slim-bar
geometry as the Auto Marshalling panels, sharing their centered plugin dock —
with the live keyboard→pilot mapping and the forwarder link status.

## Architecture

```
4 × two-key USB keyboards
        │ (evdev, exclusive grab)
Raspberry Pi (e.g. keycontrol.local)
   pi_forwarder/keyboard_forwarder.py
        │ Socket.IO: button_kb_event {kb, button, ts}
RotorHazard server — this plugin
   add lap  = interface simulate-lap  (same as '+ Lap')
   del lap  = race.delete_lap(last non-deleted lap)
```

Keyboard numbering is by **physical USB port path**, so it is stable across
reboots. `--list` shows the order; swap cables (or use fixed-seat options) to
adjust.

## Install — server side

Copy `custom_plugins/key_control` into your RotorHazard data dir's
`plugins/` folder (or install through the Community Plugins UI once listed)
and restart the server.

## Install — keyboard Pi

```bash
scp -r custom_plugins/key_control/pi_forwarder pi@keycontrol.local:
ssh pi@keycontrol.local
cd pi_forwarder && ./install.sh http://<rotorhazard-host>:5000
```

The installer sets up a systemd service (`key-control-forwarder`) that
starts on boot and reconnects automatically.

### Which keycodes do my keyboards send?

Common two-key macro pads send `1`/`2`, `a`/`b` or volume up/down — all
accepted out of the box. To check or extend:

```bash
sudo systemctl stop key-control-forwarder
python3 ~/key_control/keyboard_forwarder.py --learn
# press each button, note the keycodes, then e.g.:
# ExecStart=... --add-keys KEY_F13 --del-keys KEY_F14
sudo systemctl start key-control-forwarder
```

## Options (Settings → KEY CONTROL)

| Option | Default | Meaning |
|--------|---------|---------|
| Enabled | on | master switch |
| Number of keyboards | 4 | how many keyboards the forwarder carries |
| Show a notification | on | UI message on every button action |
| Voice callout | off | speak "<callsign> lap added/deleted" |
| Allow deleting after race stop | on | delete key works until laps are saved |
| Keyboard N fixed seat | 0 (auto) | pin keyboard N to a seat number |
| Panel theme | dark | Run-page panel colours (dark/light/auto) |

## Safety behaviour

* Adding a lap requires a **running** race; presses at any other time only
  produce a notice.
* Delete removes the seat's **last non-deleted lap** (holeshot included, last).
* 250 ms per-button debounce absorbs key repeat and switch bounce.
* If a keyboard has no seat (heat has fewer pilots), presses are ignored with
  a notice.
