'''
KEY CONTROL - controller.

Receives key events from the Pi-side forwarder (pi_forwarder/keyboard_forwarder.py)
over Socket.IO and turns them into lap actions on the running race.

Two work modes (header switch, like the Auto Marshalling school selector):

  * MANUAL - RotorHazard's own RSSI passes are suppressed (deleted the moment
    they are recorded); every counted lap comes from the buttons.
  * SEMI   - RotorHazard counts laps as usual and the buttons CONFIRM them
    inside a +-threshold window (set in the panel, default 2 s):
       green  = timer lap confirmed by a key press within the window
       yellow = timer lap with no confirming key press
       blue   = key press with no timer lap inside the window -> a manual
                lap is added at the moment the key was pressed
       red    = lap removed with the delete key (strikethrough time)

Buttons:  key 1 adds/confirms a lap, key 2 deletes the seat's last lap.

Mapping: keyboard N controls the Nth occupied seat of the current heat, a
per-keyboard fixed-seat option overrides, and the panel's Calibrate flow
assigns keyboards to seats by pressing a key on each keyboard in turn.
'''

import json
import logging
import socket as pysocket
from time import monotonic

import gevent
import requests

from eventmanager import Evt
from RHRace import RaceStatus
from RHUI import UIField, UIFieldType, UIFieldSelectOption
import RHUtils

from .cloud import CloudRelay, DEFAULT_URL

logger = logging.getLogger(__name__)

PLUGIN_ID = 'key_control'

# socket events (forwarder -> server)
EV_HELLO = 'button_kb_hello'          # {host, devices:[{kb,name,phys}], server?, ctrl_port?}
EV_HEARTBEAT = 'button_kb_heartbeat'  # same payload, every few seconds
EV_KEY = 'button_kb_event'            # {kb:int 0-based, button:'add'|'del', ts:epoch_ms}
# socket events (panel <-> server)
EV_GET_STATE = 'button_kb_get_state'
EV_STATE = 'button_kb_state'
EV_SET_MODE = 'button_kb_set_mode'          # {mode:'manual'|'semi'}
EV_SET_THRESHOLD = 'button_kb_set_threshold'  # {sec:float}
EV_CALIBRATE = 'button_kb_calibrate'        # {action:'start'|'cancel'|'reset'}
EV_LINK = 'button_kb_link'                  # {} -> contact forwarder at bk_kc_ip
EV_PRESS = 'button_kb_press'                # server -> panel: {kb, button} live flash
EV_CLOUD = 'button_kb_cloud'                # {action:'on'|'off'|'newroom'}

# options
OPT_ENABLED = 'bk_enabled'
OPT_MODE = 'bk_mode'                  # 'manual' | 'semi'
OPT_THRESHOLD = 'bk_threshold'        # seconds, +- window for Semi confirmation
OPT_KC_IP = 'bk_kc_ip'                # Key Control (forwarder) IP, optional
OPT_KB_COUNT = 'bk_kb_count'
OPT_NOTIFY = 'bk_notify'
OPT_SPEAK = 'bk_speak'
OPT_DEL_WHEN_STOPPED = 'bk_del_when_stopped'
OPT_THEME = 'bk_theme'
OPT_SEAT_PREFIX = 'bk_seat_kb'  # bk_seat_kb1..bk_seat_kb8; 0 = automatic
OPT_CLOUD_ENABLED = 'bk_cloud_enabled'
OPT_CLOUD_URL = 'bk_cloud_url'
# bk_cloud_room / bk_cloud_secret / bk_cloud_tokens are written by CloudRelay
# and deliberately not registered: they are identity, not settings.

PLUGIN_VERSION = '1.3.0'

MODE_MANUAL = 'manual'
MODE_SEMI = 'semi'

MAX_KEYBOARDS = 8
DEBOUNCE_SEC = 0.25       # ignore repeats faster than this per (kb, button)
FORWARDER_TIMEOUT = 15    # heartbeat older than this -> link shown offline
FORWARDER_CTRL_PORT = 8737
OWN_LAP_WINDOW = 1.5      # s: a MANUAL-source lap this soon after our own
                          # simulate-lap call is ours, not an external one
CALIBRATION_TIMEOUT = 60  # s without a key press -> calibration cancelled
FEED_LAPS = 8             # laps kept per seat in the panel feed

# lap sources (BaseHardwareInterface.LAP_SOURCE_*)
SRC_REALTIME = 0
SRC_MANUAL = 1


class ButtonKeyboardController:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self._last_key = {}        # (kb, button) -> monotonic of last accepted press
        self._forwarder = None     # {'host':…, 'devices':[…], 'server':…}
        self._forwarder_seen = 0.0
        # ---- semi/manual race state (reset every stage) ----
        self._marks = {}           # (seat, lap_time_stamp_ms) -> 'green'|'yellow'|'blue'|'suppressed'
        self._own_expect = {}      # seat -> [monotonic, ...] of our simulate-lap calls
        self._pending = {}         # seat -> [{'ts': race_ms, 'mono': monotonic, 'consumed': bool}]
        # ---- calibration ----
        self._cal = None           # {'seats':[…], 'idx':int, 'used':set(kb), 'last': monotonic}
        # ---- cloud judges ----
        self.version = PLUGIN_VERSION
        self._last_remote = {}     # (seat, button) -> monotonic of last accepted tap
        self.cloud = CloudRelay(rhapi, self)

    # ------------------------------------------------------------------ setup

    def register_ui(self, _args=None):
        ui = self._rhapi.ui
        fields = self._rhapi.fields

        ui.register_panel(PLUGIN_ID, 'KEY CONTROL', 'settings', order=0)

        def opt(name, label, ftype, value, desc, options=None):
            kw = dict(name=name, label=label, field_type=ftype, value=value, desc=desc)
            if options is not None:
                kw['options'] = options
            fields.register_option(UIField(**kw), PLUGIN_ID)

        opt(OPT_ENABLED, 'Enabled', UIFieldType.CHECKBOX, True,
            'Master switch. When off, key presses from the forwarder are ignored '
            'and RotorHazard behaves as if the plugin were not installed.')
        opt(OPT_MODE, 'Work mode', UIFieldType.SELECT, MODE_SEMI,
            'Manual: RotorHazard\'s own RSSI laps are suppressed - every lap '
            'comes from the buttons. Semi: the timer counts laps as usual and '
            'key presses confirm them (green confirmed / yellow unconfirmed / '
            'blue button-only / red deleted). Also switchable from the panel '
            'header.', options=[
                UIFieldSelectOption(MODE_SEMI, 'Semi (confirm timer laps)'),
                UIFieldSelectOption(MODE_MANUAL, 'Manual (buttons only)')])
        opt(OPT_THRESHOLD, 'Semi confirmation window (seconds)',
            UIFieldType.TEXT, '2',
            'A key press within this many seconds of a timer lap confirms it; '
            'a press with no timer lap inside the window adds a manual lap at '
            'the moment of the press. Editable in the panel header too.')
        opt(OPT_KC_IP, 'Key Control IP', UIFieldType.TEXT, '',
            'IP address of the Key Control box (the Pi running the keyboard '
            'forwarder), e.g. 192.168.100.53. Used by "Link Key Control": the '
            'server contacts the forwarder on port {} and points it at itself.'
            .format(FORWARDER_CTRL_PORT))
        opt(OPT_KB_COUNT, 'Number of keyboards', UIFieldType.BASIC_INT, 4,
            'How many two-key keyboards the forwarder carries (1-8).')
        opt(OPT_NOTIFY, 'Show a notification on every button action',
            UIFieldType.CHECKBOX, False,
            'Pop a priority message on all pages when a lap is added, confirmed '
            'or deleted from a button keyboard. Off by default - the panel\'s '
            'live press lights and lap dots carry the same information '
            'without covering the screen.')
        opt(OPT_SPEAK, 'Voice callout on every button action',
            UIFieldType.CHECKBOX, False,
            'Speak "<callsign> lap added / confirmed / deleted" through the '
            'audio callouts.')
        opt(OPT_DEL_WHEN_STOPPED, 'Allow deleting after the race is stopped',
            UIFieldType.CHECKBOX, True,
            'When on, the delete key still works between race stop and save. '
            'Adding laps always requires a running race.')
        for i in range(1, MAX_KEYBOARDS + 1):
            opt('{}{}'.format(OPT_SEAT_PREFIX, i),
                'Keyboard {} fixed seat (0 = automatic)'.format(i),
                UIFieldType.BASIC_INT, 0,
                '0: keyboard {n} controls the {n}. occupied seat of the current '
                'heat. 1-8: always control that seat number. The panel\'s '
                'Calibrate flow fills these in for you.'.format(n=i))
        opt(OPT_CLOUD_ENABLED, 'Cloud judges (phones over the internet)',
            UIFieldType.CHECKBOX, False,
            'Open a room on the judge relay and give every occupied seat its '
            'own phone page with an ADD LAP / REMOVE LAP pair. The timer makes '
            'the connection outwards, so no port forwarding or public IP is '
            'needed. Links appear in the Run-page panel.')
        opt(OPT_CLOUD_URL, 'Judge relay address', UIFieldType.TEXT, DEFAULT_URL,
            'Where the judge pages are hosted. Leave as {} unless you run your '
            'own copy of the relay.'.format(DEFAULT_URL))
        opt(OPT_THEME, 'Panel theme', UIFieldType.SELECT, 'dark',
            'Colour scheme of the Run-page panel. Auto follows each viewer\'s '
            'browser/OS light-dark preference.', options=[
                UIFieldSelectOption('dark', 'Dark'),
                UIFieldSelectOption('light', 'Light'),
                UIFieldSelectOption('auto', 'Auto (follow browser/OS)')])

        ui.register_quickbutton(PLUGIN_ID, 'bk_link_btn', 'Link Key Control now',
                                self._quick_link)
        ui.register_quickbutton(PLUGIN_ID, 'bk_cloud_new_btn',
                                'New cloud room code',
                                lambda _a=None: self.cloud.new_room())

        # Run-page front-end loader (same pattern as Auto Marshalling: a tiny
        # markdown snippet whose <script> tag pulls the panel JS).
        loader = '<script src="/key_control/static/key_control.js"></script>'
        panel = PLUGIN_ID + '_load_run'
        ui.register_panel(panel, 'KEY CONTROL', 'run', order=0)
        ui.register_markdown(panel, PLUGIN_ID + '_boot_run', loader)
        fields.register_option(UIField(
            name='_' + PLUGIN_ID + '_boot_run', label='', value='',
            field_type=UIFieldType.TEXT, private=True, desc=loader), panel)

    # -------------------------------------------------------------- option io

    def _opt(self, name, default=None):
        try:
            val = self._rhapi.db.option(name)
        except Exception:
            return default
        return default if val is None or val == '' else val

    def _opt_bool(self, name, default=False):
        return self._opt(name, default) in (True, 1, '1', 'true', 'True')

    def _opt_int(self, name, default):
        try:
            return int(float(self._opt(name, default)))
        except (TypeError, ValueError):
            return default

    def _opt_float(self, name, default):
        try:
            return float(self._opt(name, default))
        except (TypeError, ValueError):
            return default

    def _mode(self):
        return MODE_MANUAL if self._opt(OPT_MODE, MODE_SEMI) == MODE_MANUAL \
            else MODE_SEMI

    def _threshold(self):
        return max(0.2, min(30.0, self._opt_float(OPT_THRESHOLD, 2.0)))

    # ------------------------------------------------------------- rh access

    @property
    def _racecontext(self):
        return self._rhapi.db._racecontext

    def _kb_count(self):
        return max(1, min(MAX_KEYBOARDS, self._opt_int(OPT_KB_COUNT, 4)))

    def _occupied_seats(self):
        '''Seat indexes of the current heat that have a frequency and a pilot,
        in seat order.'''
        race = self._racecontext.race
        try:
            freqs = json.loads(race.profile.frequencies).get('f') or []
        except Exception:
            freqs = []
        pilots = race.node_pilots or {}
        seats = []
        for seat in range(race.num_nodes):
            freq = freqs[seat] if seat < len(freqs) else 0
            pilot_id = pilots.get(seat, RHUtils.PILOT_ID_NONE)
            if freq and freq != RHUtils.FREQUENCY_ID_NONE \
                    and pilot_id != RHUtils.PILOT_ID_NONE:
                seats.append(seat)
        return seats

    def _seat_for_keyboard(self, kb_index):
        '''Resolve keyboard (0-based) to a seat index, or None + reason.'''
        fixed = self._opt_int('{}{}'.format(OPT_SEAT_PREFIX, kb_index + 1), 0)
        if fixed > 0:
            race = self._racecontext.race
            if fixed > race.num_nodes:
                return None, 'seat {} does not exist'.format(fixed)
            return fixed - 1, None
        occupied = self._occupied_seats()
        if kb_index < len(occupied):
            return occupied[kb_index], None
        return None, 'only {} occupied seat(s) in this heat'.format(len(occupied))

    def _seat_label(self, seat):
        '''"R1" style band/channel label for a seat, falling back to seat number.'''
        try:
            fdata = json.loads(self._racecontext.race.profile.frequencies)
            band = (fdata.get('b') or [])[seat]
            chan = (fdata.get('c') or [])[seat]
            if band and chan:
                return '{}{}'.format(band, chan)
        except Exception:
            pass
        return 'Seat {}'.format(seat + 1)

    def _seat_callsign(self, seat):
        try:
            pilot_id = self._racecontext.race.node_pilots.get(
                seat, RHUtils.PILOT_ID_NONE)
            if pilot_id != RHUtils.PILOT_ID_NONE:
                pilot = self._racecontext.rhdata.get_pilot(pilot_id)
                if pilot:
                    return pilot.callsign
        except Exception:
            pass
        return None

    def _race_ms(self):
        '''Milliseconds since race start (same scale as lap_time_stamp).'''
        race = self._racecontext.race
        if not race.start_time_monotonic:
            return None
        return (monotonic() - race.start_time_monotonic) * 1000.0

    _delete_style = None  # cached: 'dict' (RH <= 4.4) or 'args' (newer)

    def _delete_lap_compat(self, seat, lap_index):
        '''RH 4.4 has delete_lap(data-dict); newer servers take
        (node_index, lap_index). Detect once and call accordingly.'''
        race = self._racecontext.race
        if ButtonKeyboardController._delete_style is None:
            import inspect
            try:
                first = next(iter(
                    inspect.signature(race.delete_lap).parameters))
            except (ValueError, TypeError, StopIteration):
                first = 'data'
            ButtonKeyboardController._delete_style = \
                'dict' if first == 'data' else 'args'
        if ButtonKeyboardController._delete_style == 'dict':
            race.delete_lap({'node': seat, 'lap_index': lap_index})
        else:
            race.delete_lap(seat, lap_index)

    # -------------------------------------------------------------- messaging

    def _feedback(self, message, speak_text=None, force=False):
        if force or self._opt_bool(OPT_NOTIFY, False):
            try:
                self._rhapi.ui.message_notify(message)
            except Exception:
                logger.exception('notify failed')
        if speak_text and self._opt_bool(OPT_SPEAK, False):
            try:
                self._rhapi.ui.message_speak(speak_text)
            except Exception:
                logger.exception('speak failed')

    # ----------------------------------------------------------- key handling

    def on_key_event(self, data=None):
        '''EV_KEY from the forwarder: {kb, button, ts?}.'''
        if not isinstance(data, dict):
            return
        if not self._opt_bool(OPT_ENABLED, True):
            return
        try:
            kb = int(data.get('kb'))
        except (TypeError, ValueError):
            return
        button = data.get('button')
        if button not in ('add', 'del') or not (0 <= kb < self._kb_count()):
            return

        now = monotonic()
        if now - self._last_key.get((kb, button), 0) < DEBOUNCE_SEC:
            return
        self._last_key[(kb, button)] = now

        # live press indicator in the panel (lightweight, before any handling)
        try:
            self._rhapi.ui.socket_broadcast(EV_PRESS, {'kb': kb, 'button': button})
        except Exception:
            pass

        # calibration consumes every key press
        if self._cal:
            self._cal_key(kb)
            return

        seat, problem = self._seat_for_keyboard(kb)
        if seat is None:
            self._feedback('KEY CONTROL KB{}: no seat mapped ({})'.format(kb + 1, problem))
            return

        if button == 'add':
            self._add_pressed('KB{}'.format(kb + 1), seat)
        else:
            self._delete_last_lap('KB{}'.format(kb + 1), seat)
        self.on_change()

    def on_remote_press(self, seat, button, age_ms=0.0, source='Judge'):
        '''A press from a cloud judge. Same lap handling as a button keyboard,
        addressed by seat instead of keyboard, and dated back to the moment the
        judge actually tapped.'''
        if not self._opt_bool(OPT_ENABLED, True):
            return
        if not isinstance(seat, int) or not (0 <= seat < self._racecontext.race.num_nodes):
            return
        # Debounce on when the judge tapped, not on when the tap arrived: a
        # fat-fingered double tap is one lap, while two taps a judge made
        # seconds apart while offline still both count.
        press_mono = monotonic() - age_ms / 1000.0
        last = self._last_remote.get((seat, button))
        if last is not None and abs(press_mono - last) < DEBOUNCE_SEC:
            return
        self._last_remote[(seat, button)] = press_mono
        if button == 'add':
            self._add_pressed(source, seat, age_ms)
        elif button == 'del':
            self._delete_last_lap(source, seat)
        self.on_change()

    # -------------------------------------------------- add-key press (modes)

    def _add_pressed(self, src, seat, age_ms=0.0):
        '''`src` labels the press in messages ("KB2", "Judge ab12"). `age_ms` is
        how long ago the press happened - zero for a local keyboard, the relay
        round trip for a cloud judge.'''
        race = self._racecontext.race
        if race.race_status != RaceStatus.RACING:
            self._feedback('KEY CONTROL {}: race is not running - lap not added'
                           .format(src))
            return
        callsign = self._seat_callsign(seat) or self._seat_label(seat)

        if self._mode() == MODE_MANUAL:
            self._do_add_lap(seat, age_ms)
            self._feedback('KEY CONTROL {}: lap added for {} ({})'
                           .format(src, callsign, self._seat_label(seat)),
                           speak_text='{} lap added'.format(callsign))
            return

        # ---- SEMI: confirm a recent unconfirmed timer lap, else start a
        # pending window; when it expires with no timer lap, add manually.
        now_ms = self._race_ms()
        if now_ms is None:
            return
        press_ms = now_ms - age_ms
        thr_ms = self._threshold() * 1000.0

        lap_ts = self._find_unconfirmed_lap(seat, press_ms, thr_ms)
        if lap_ts is not None:
            self._marks[(seat, lap_ts)] = 'green'
            self._feedback('KEY CONTROL {}: lap confirmed for {} ({})'
                           .format(src, callsign, self._seat_label(seat)),
                           speak_text='{} lap confirmed'.format(callsign))
            return

        entry = {'ts': press_ms, 'mono': monotonic() - age_ms / 1000.0,
                 'consumed': False}
        self._pending.setdefault(seat, []).append(entry)
        gevent.spawn(self._pending_expire, seat, entry, src, callsign)

    def _pending_expire(self, seat, entry, src, callsign):
        # The window runs from the press itself, so a press that reached us
        # late waits correspondingly less.
        gevent.sleep(max(0.0, self._threshold() - (monotonic() - entry['mono'])))
        if entry['consumed']:
            return
        entry['consumed'] = True
        race = self._racecontext.race
        if race.race_status != RaceStatus.RACING:
            return
        # no timer lap arrived inside the window: manual lap at press time
        elapsed_ms = (monotonic() - entry['mono']) * 1000.0
        self._do_add_lap(seat, elapsed_ms)
        self._feedback('KEY CONTROL {}: manual lap for {} ({}) - no timer lap '
                       'within {:g}s'.format(src, callsign,
                                             self._seat_label(seat),
                                             self._threshold()),
                       speak_text='{} manual lap'.format(callsign))
        self.on_change()

    def _find_unconfirmed_lap(self, seat, press_ms, thr_ms):
        '''Newest non-deleted timer lap of this seat marked yellow within the
        window before the press.'''
        best = None
        for (s, lap_ts), mark in self._marks.items():
            if s != seat or mark != 'yellow':
                continue
            if abs(press_ms - lap_ts) <= thr_ms:
                if best is None or lap_ts > best:
                    best = lap_ts
        if best is None:
            return None
        # skip if that lap got deleted meanwhile
        for lap in (self._racecontext.race.node_laps or {}).get(seat) or []:
            if lap.lap_time_stamp == best and lap.deleted:
                return None
        return best

    def _do_add_lap(self, seat, elapsed_ms):
        '''Add a manual lap through the interface (same as the Run page
        '+ Lap' button), registering it as OURS so the lap-recorded hook can
        tell it apart from the timer's own passes.'''
        race = self._racecontext.race
        self._own_expect.setdefault(seat, []).append(monotonic())
        try:
            self._racecontext.events.trigger(Evt.CROSSING_EXIT, {
                'nodeIndex': seat,
                'color': race.seat_colors[seat],
            })
        except Exception:
            logger.exception('CROSSING_EXIT trigger failed')
        self._racecontext.interface.intf_simulate_lap(seat, elapsed_ms)
        logger.info('key_control: added lap, seat %d (offset %.0f ms)',
                    seat + 1, elapsed_ms)

    # ------------------------------------------------------ lap-recorded hook

    def on_lap_recorded(self, args=None):
        '''Evt.RACE_LAP_RECORDED: classify the lap (ours / timer's), apply the
        work mode.'''
        if not args or not self._opt_bool(OPT_ENABLED, True):
            return
        lap = args.get('lap')
        seat = args.get('node_index')
        if lap is None or seat is None:
            return
        lap_ts = lap.lap_time_stamp

        if self._is_own_lap(seat, lap):
            self._marks[(seat, lap_ts)] = 'blue' \
                if self._mode() == MODE_SEMI else 'blue'
            self.on_change()
            return

        if self._mode() == MODE_MANUAL:
            # timer lap in Manual mode: suppress it on the spot
            self._marks[(seat, lap_ts)] = 'suppressed'
            gevent.spawn(self._suppress_lap, seat, lap_ts)
            return

        # ---- SEMI: timer lap - confirmed if a pending press is in window
        thr_ms = self._threshold() * 1000.0
        pending = self._pending.get(seat) or []
        for entry in pending:
            if not entry['consumed'] and abs(lap_ts - entry['ts']) <= thr_ms:
                entry['consumed'] = True
                self._marks[(seat, lap_ts)] = 'green'
                self.on_change()
                return
        self._marks[(seat, lap_ts)] = 'yellow'
        self.on_change()

    def _is_own_lap(self, seat, lap):
        '''A MANUAL-source lap right after our own simulate-lap call is ours.'''
        if lap.source != SRC_MANUAL:
            return False
        expects = self._own_expect.get(seat) or []
        now = monotonic()
        for i, t in enumerate(expects):
            if now - t <= OWN_LAP_WINDOW:
                expects.pop(i)
                return True
        # drop stale expectations
        self._own_expect[seat] = [t for t in expects if now - t <= OWN_LAP_WINDOW]
        return False

    def _suppress_lap(self, seat, lap_ts):
        '''Manual mode: delete a timer-recorded lap (runs off the event hook
        so the recording call stack finishes first).'''
        gevent.sleep(0.05)
        race = self._racecontext.race
        laps = (race.node_laps or {}).get(seat) or []
        for idx in range(len(laps) - 1, -1, -1):
            lap = laps[idx]
            if lap.lap_time_stamp == lap_ts and not lap.deleted:
                self._delete_lap_compat(seat, idx)
                callsign = self._seat_callsign(seat) or self._seat_label(seat)
                logger.info('key_control: suppressed timer lap, seat %d (manual mode)',
                            seat + 1)
                self._feedback('KEY CONTROL: timer lap for {} suppressed (Manual mode)'
                               .format(callsign))
                break
        self.on_change()

    # ------------------------------------------------------------- delete key

    def _delete_last_lap(self, src, seat):
        race = self._racecontext.race
        allowed = (RaceStatus.RACING, RaceStatus.DONE) \
            if self._opt_bool(OPT_DEL_WHEN_STOPPED, True) else (RaceStatus.RACING,)
        if race.race_status not in allowed:
            self._feedback('KEY CONTROL {}: race is not running - nothing deleted'
                           .format(src))
            return
        laps = (race.node_laps or {}).get(seat) or []
        last_index = None
        for i in range(len(laps) - 1, -1, -1):
            if not laps[i].deleted and self._marks.get(
                    (seat, laps[i].lap_time_stamp)) != 'suppressed':
                last_index = i
                break
        callsign = self._seat_callsign(seat) or self._seat_label(seat)
        if last_index is None:
            self._feedback('KEY CONTROL {}: {} has no laps to delete'
                           .format(src, callsign))
            return
        lap = laps[last_index]
        lap_number = lap.lap_number
        self._marks[(seat, lap.lap_time_stamp)] = 'red'
        self._delete_lap_compat(seat, last_index)
        logger.info('key_control: %s deleted lap idx %s (lap %s), seat %d (%s)',
                    src, last_index, lap_number, seat + 1, callsign)
        what = 'holeshot' if not lap_number else 'lap {}'.format(lap_number)
        self._feedback('KEY CONTROL {}: deleted {} of {} ({})'
                       .format(src, what, callsign, self._seat_label(seat)),
                       speak_text='{} lap deleted'.format(callsign))

    # ------------------------------------------------------------ calibration

    def on_calibrate(self, data=None):
        action = (data or {}).get('action')
        if action == 'start':
            seats = self._occupied_seats()
            if not seats:
                self._feedback('KEY CONTROL: no occupied seats to calibrate '
                               '(assign pilots to the heat first)', force=True)
                return
            self._cal = {'seats': seats, 'idx': 0, 'used': set(),
                         'last': monotonic()}
            gevent.spawn(self._cal_watchdog, self._cal)
            self._feedback('KEY CONTROL calibration: press any key on the '
                           'keyboard for {}'.format(self._cal_target_text()), force=True)
        elif action == 'cancel':
            if self._cal:
                self._cal = None
                self._feedback('KEY CONTROL calibration cancelled', force=True)
        elif action == 'reset':
            for i in range(1, MAX_KEYBOARDS + 1):
                self._rhapi.db.option_set('{}{}'.format(OPT_SEAT_PREFIX, i), 0)
            self._cal = None
            self._feedback('KEY CONTROL: mapping reset to automatic '
                           '(keyboard N = Nth occupied seat)', force=True)
        self.on_change()

    def _cal_target_text(self):
        cal = self._cal
        seat = cal['seats'][cal['idx']]
        callsign = self._seat_callsign(seat)
        return '{} ({})'.format(self._seat_label(seat),
                                callsign or 'seat {}'.format(seat + 1))

    def _cal_key(self, kb):
        cal = self._cal
        if not cal:
            return
        cal['last'] = monotonic()
        if kb in cal['used']:
            self._feedback('KEY CONTROL calibration: keyboard {} is already '
                           'assigned - press a different one for {}'
                           .format(kb + 1, self._cal_target_text()), force=True)
            self.on_change()
            return
        seat = cal['seats'][cal['idx']]
        self._rhapi.db.option_set('{}{}'.format(OPT_SEAT_PREFIX, kb + 1), seat + 1)
        cal['used'].add(kb)
        cal['idx'] += 1
        self._feedback('KEY CONTROL calibration: keyboard {} -> {}'
                       .format(kb + 1, self._cal_target_text_for(seat)), force=True)
        if cal['idx'] >= len(cal['seats']):
            self._cal = None
            self._feedback('KEY CONTROL calibration complete', force=True)
        else:
            self._feedback('KEY CONTROL calibration: press any key on the '
                           'keyboard for {}'.format(self._cal_target_text()), force=True)
        self.on_change()

    def _cal_target_text_for(self, seat):
        callsign = self._seat_callsign(seat)
        return '{} ({})'.format(self._seat_label(seat),
                                callsign or 'seat {}'.format(seat + 1))

    def _cal_watchdog(self, cal):
        while self._cal is cal:
            if monotonic() - cal['last'] > CALIBRATION_TIMEOUT:
                self._cal = None
                self._feedback('KEY CONTROL calibration timed out', force=True)
                self.on_change()
                return
            gevent.sleep(1)

    # -------------------------------------------------------------- mode/thr

    def on_set_mode(self, data=None):
        mode = (data or {}).get('mode')
        if mode in (MODE_MANUAL, MODE_SEMI):
            self._rhapi.db.option_set(OPT_MODE, mode)
            self._feedback('KEY CONTROL mode: {}'.format(
                'Manual (buttons only)' if mode == MODE_MANUAL
                else 'Semi (confirm timer laps)'))
        self.on_change()

    def on_set_threshold(self, data=None):
        try:
            sec = float((data or {}).get('sec'))
        except (TypeError, ValueError):
            return
        sec = max(0.2, min(30.0, sec))
        self._rhapi.db.option_set(OPT_THRESHOLD, '{:g}'.format(sec))
        self.on_change()

    # -------------------------------------------------------------- link (ip)

    def _quick_link(self, _args=None):
        self.on_link()

    def on_link(self, _data=None):
        ip = str(self._opt(OPT_KC_IP, '') or '').strip()
        if not ip:
            self._feedback('KEY CONTROL: set the Key Control IP first '
                           '(Settings or panel)')
            self.on_change()
            return
        gevent.spawn(self._link_worker, ip)

    def _link_worker(self, ip):
        try:
            own_ip = self._own_ip_toward(ip)
            port = self._racecontext.serverconfig.get_item_int(
                'GENERAL', 'HTTP_PORT') or 5000
            url = 'http://{}:{}'.format(own_ip, port)
            resp = requests.post(
                'http://{}:{}/server'.format(ip, FORWARDER_CTRL_PORT),
                json={'url': url}, timeout=5)
            if resp.ok:
                self._feedback('KEY CONTROL: forwarder at {} linked to {}'
                               .format(ip, url))
            else:
                self._feedback('KEY CONTROL: forwarder at {} answered {}'
                               .format(ip, resp.status_code))
        except Exception as ex:
            self._feedback('KEY CONTROL: cannot reach forwarder at {}:{} ({})'
                           .format(ip, FORWARDER_CTRL_PORT,
                                   ex.__class__.__name__))
        self.on_change()

    @staticmethod
    def _own_ip_toward(ip):
        s = pysocket.socket(pysocket.AF_INET, pysocket.SOCK_DGRAM)
        try:
            s.connect((ip, FORWARDER_CTRL_PORT))
            return s.getsockname()[0]
        finally:
            s.close()

    # ------------------------------------------------------ forwarder link io

    def on_hello(self, data=None):
        self._forwarder = data if isinstance(data, dict) else {}
        self._forwarder_seen = monotonic()
        host = (self._forwarder or {}).get('host', '?')
        n = len((self._forwarder or {}).get('devices') or [])
        logger.info('key_control: forwarder connected from %s with %d keyboard(s)',
                    host, n)
        self._rhapi.ui.message_notify(
            'KEY CONTROL online: {} keyboard(s) on {}'.format(n, host))
        self.on_change()

    def on_heartbeat(self, data=None):
        if isinstance(data, dict):
            self._forwarder = data
        self._forwarder_seen = monotonic()
        self.on_change()

    # ---------------------------------------------------------- race lifecycle

    def on_race_reset(self, _args=None):
        self._marks = {}
        self._own_expect = {}
        self._pending = {}
        self.on_change()

    # ------------------------------------------------------------ panel state

    def on_get_state(self, _data=None):
        self.broadcast_state()

    def on_change(self, _args=None):
        '''State that a panel or a cloud judge can see has moved.'''
        self.broadcast_state()
        self.cloud.notify_change()

    def broadcast_state(self, _args=None):
        try:
            self._rhapi.ui.socket_broadcast(EV_STATE, self._state())
        except Exception:
            logger.exception('state broadcast failed')

    def _lap_feed(self, seat):
        '''Recent laps of a seat with their marks, for the panel dots.'''
        laps = (self._racecontext.race.node_laps or {}).get(seat) or []
        feed = []
        for lap in laps:
            # RH 4.4 marks deleted laps invalid+deleted; keep those visible
            # (red/strikethrough). Skip only invalid laps that never counted.
            if lap.invalid and not lap.deleted:
                continue
            mark = self._marks.get((seat, lap.lap_time_stamp))
            if lap.deleted:
                mark = 'red' if mark != 'suppressed' else 'suppressed'
            elif mark is None:
                mark = 'yellow' if self._mode() == MODE_SEMI else 'blue'
            feed.append({
                'n': lap.lap_number,
                't': lap.lap_time_formatted,
                'ts': lap.lap_time_stamp,
                'mark': mark,
                'deleted': bool(lap.deleted),
            })
        return feed[-FEED_LAPS:]

    def _state(self):
        race = self._racecontext.race
        mapping = []
        for kb in range(self._kb_count()):
            seat, problem = self._seat_for_keyboard(kb)
            fixed = self._opt_int('{}{}'.format(OPT_SEAT_PREFIX, kb + 1), 0) > 0
            mapping.append({
                'kb': kb,
                'seat': seat,
                'label': self._seat_label(seat) if seat is not None else None,
                'callsign': self._seat_callsign(seat) if seat is not None else None,
                'fixed': fixed,
                'problem': problem,
                'laps': self._lap_feed(seat) if seat is not None else [],
            })
        age = monotonic() - self._forwarder_seen if self._forwarder_seen else None
        cal = None
        if self._cal:
            cal = {
                'active': True,
                'step': self._cal['idx'] + 1,
                'total': len(self._cal['seats']),
                'target': self._cal_target_text(),
            }
        return {
            'enabled': self._opt_bool(OPT_ENABLED, True),
            'mode': self._mode(),
            'threshold': self._threshold(),
            'race_status': race.race_status,
            'mapping': mapping,
            'calibration': cal,
            'kc_ip': str(self._opt(OPT_KC_IP, '') or ''),
            'forwarder': {
                'online': age is not None and age < FORWARDER_TIMEOUT,
                'age': round(age, 1) if age is not None else None,
                'host': (self._forwarder or {}).get('host'),
                'server': (self._forwarder or {}).get('server'),
                'devices': (self._forwarder or {}).get('devices') or [],
            },
            'theme': self._opt(OPT_THEME, 'dark'),
            'cloud': self.cloud.status(),
        }

    def on_option_set(self, args=None):
        option = str((args or {}).get('option', ''))
        if option in (OPT_CLOUD_ENABLED, OPT_CLOUD_URL):
            if self.cloud.enabled():
                self.cloud.restart()
            else:
                self.cloud.stop()
        if option.startswith('bk_'):
            self.on_change()

    # -------------------------------------------------------- cloud judges

    def on_cloud(self, data=None):
        action = (data or {}).get('action')
        if action == 'on':
            self._rhapi.db.option_set(OPT_CLOUD_ENABLED, '1')
            self.cloud.start()
        elif action == 'off':
            self._rhapi.db.option_set(OPT_CLOUD_ENABLED, '0')
            self.cloud.stop()
        elif action == 'newroom':
            self.cloud.new_room()
            self._feedback('KEY CONTROL: new cloud room - every old judge link '
                           'has stopped working', force=True)
        self.on_change()

    def cloud_seats(self):
        '''The seats a cloud judge can be given, in seat order.'''
        return [{'seat': seat,
                 'label': self._seat_label(seat),
                 'callsign': self._seat_callsign(seat)}
                for seat in self._occupied_seats()]

    def cloud_racing(self):
        '''True while laps can still be added - the relay polls fast then.'''
        return self._racecontext.race.race_status == RaceStatus.RACING

    def cloud_snapshot(self):
        '''What the judge pages show. Counts every crossing the timer still
        holds, holeshot included, so a judge sees their own tap land.'''
        race = self._racecontext.race
        seats = []
        for seat in self.cloud_seats():
            laps = (race.node_laps or {}).get(seat['seat']) or []
            live = [lap for lap in laps if not lap.deleted and not lap.invalid]
            timed = [lap for lap in live if lap.lap_number]
            best = min(timed, key=lambda lap: lap.lap_time, default=None)
            seats.append({
                'seat': seat['seat'],
                'label': seat['label'],
                'callsign': seat['callsign'],
                'laps': len(live),
                'lastLap': live[-1].lap_time_formatted if live else None,
                'bestLap': best.lap_time_formatted if best else None,
            })
        return {
            'event': self._heat_name(),
            'mode': self._mode(),
            'raceStatus': race.race_status,
            'seats': seats,
        }

    def _heat_name(self):
        try:
            heat = self._racecontext.rhdata.get_heat(
                self._racecontext.race.current_heat)
            if heat:
                return heat.display_name or ''
        except Exception:
            pass
        return ''
