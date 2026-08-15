'''
KEY CONTROL - controller.

Receives key events from the Pi-side forwarder (pi_forwarder/keyboard_forwarder.py)
over Socket.IO and turns them into lap actions on the running race:

  * button 'add'    -> manual lap for the mapped seat, identical to the Run
                       page '+ Lap' button (interface simulate-lap, source MANUAL)
  * button 'del'    -> delete the mapped seat's last non-deleted lap, identical
                       to the Run page's lap 'x' button

Mapping: keyboard N controls the Nth occupied seat (frequency set AND pilot
assigned) of the current heat, in seat order - so with pilots on R1/R3/R6/R8,
keyboard 1 is the R1 pilot, keyboard 2 the R3 pilot, and so on. A per-keyboard
fixed-seat option overrides the automatic mapping when a keyboard must always
drive one physical seat.
'''

import json
import logging
from time import monotonic

from eventmanager import Evt
from RHRace import RaceStatus
from RHUI import UIField, UIFieldType, UIFieldSelectOption
import RHUtils

logger = logging.getLogger(__name__)

PLUGIN_ID = 'key_control'

# socket events (forwarder -> server)
EV_HELLO = 'button_kb_hello'          # {host, devices:[{kb,name,phys}]}
EV_HEARTBEAT = 'button_kb_heartbeat'  # same payload, every few seconds
EV_KEY = 'button_kb_event'            # {kb:int 0-based, button:'add'|'del', ts:epoch_ms}
# socket events (panel <-> server)
EV_GET_STATE = 'button_kb_get_state'
EV_STATE = 'button_kb_state'

# options
OPT_ENABLED = 'bk_enabled'
OPT_KB_COUNT = 'bk_kb_count'
OPT_NOTIFY = 'bk_notify'
OPT_SPEAK = 'bk_speak'
OPT_DEL_WHEN_STOPPED = 'bk_del_when_stopped'
OPT_THEME = 'bk_theme'
OPT_SEAT_PREFIX = 'bk_seat_kb'  # bk_seat_kb1..bk_seat_kb8; 0 = automatic

MAX_KEYBOARDS = 8
DEBOUNCE_SEC = 0.25       # ignore repeats faster than this per (kb, button)
FORWARDER_TIMEOUT = 15    # heartbeat older than this -> link shown offline


class ButtonKeyboardController:
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self._last_key = {}        # (kb, button) -> monotonic of last accepted press
        self._forwarder = None     # {'host':…, 'devices':[…]}
        self._forwarder_seen = 0.0

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
            'Master switch. When off, key presses from the forwarder are ignored.')
        opt(OPT_KB_COUNT, 'Number of keyboards', UIFieldType.BASIC_INT, 4,
            'How many two-key keyboards the forwarder carries (1-8).')
        opt(OPT_NOTIFY, 'Show a notification on every button action',
            UIFieldType.CHECKBOX, True,
            'Pop a priority message on all pages when a lap is added or deleted '
            'from a button keyboard.')
        opt(OPT_SPEAK, 'Voice callout on every button action',
            UIFieldType.CHECKBOX, False,
            'Speak "<callsign> lap added / lap deleted" through the audio callouts.')
        opt(OPT_DEL_WHEN_STOPPED, 'Allow deleting after the race is stopped',
            UIFieldType.CHECKBOX, True,
            'When on, the delete key still works between race stop and save '
            '(same window in which the Run page shows the lap x buttons). '
            'Adding laps always requires a running race.')
        for i in range(1, MAX_KEYBOARDS + 1):
            opt('{}{}'.format(OPT_SEAT_PREFIX, i),
                'Keyboard {} fixed seat (0 = automatic)'.format(i),
                UIFieldType.BASIC_INT, 0,
                '0: keyboard {n} controls the {n}. occupied seat of the current '
                'heat. 1-8: always control that seat number, regardless of '
                'which seats are occupied.'.format(n=i))
        opt(OPT_THEME, 'Panel theme', UIFieldType.SELECT, 'dark',
            'Colour scheme of the Run-page panel. Auto follows each viewer\'s '
            'browser/OS light-dark preference.', options=[
                UIFieldSelectOption('dark', 'Dark'),
                UIFieldSelectOption('light', 'Light'),
                UIFieldSelectOption('auto', 'Auto (follow browser/OS)')])

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

    # -------------------------------------------------------------- messaging

    def _feedback(self, message, speak_text=None):
        if self._opt_bool(OPT_NOTIFY, True):
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

        seat, problem = self._seat_for_keyboard(kb)
        if seat is None:
            self._feedback('Button KB{}: no seat mapped ({})'.format(kb + 1, problem))
            return

        if button == 'add':
            self._add_lap(kb, seat)
        else:
            self._delete_last_lap(kb, seat)
        self.broadcast_state()

    def _add_lap(self, kb, seat):
        race = self._racecontext.race
        if race.race_status != RaceStatus.RACING:
            self._feedback('Button KB{}: race is not running - lap not added'
                           .format(kb + 1))
            return
        callsign = self._seat_callsign(seat) or self._seat_label(seat)
        # identical to the Run page '+ Lap' button (server on_simulate_lap)
        try:
            self._racecontext.events.trigger(Evt.CROSSING_EXIT, {
                'nodeIndex': seat,
                'color': race.seat_colors[seat],
            })
        except Exception:
            logger.exception('CROSSING_EXIT trigger failed')
        self._racecontext.interface.intf_simulate_lap(seat, 0)
        logger.info('key_control: KB%d added lap, seat %d (%s)',
                    kb + 1, seat + 1, callsign)
        self._feedback('Button KB{}: lap added for {} ({})'
                       .format(kb + 1, callsign, self._seat_label(seat)),
                       speak_text='{} lap added'.format(callsign))

    def _delete_last_lap(self, kb, seat):
        race = self._racecontext.race
        allowed = (RaceStatus.RACING, RaceStatus.DONE) \
            if self._opt_bool(OPT_DEL_WHEN_STOPPED, True) else (RaceStatus.RACING,)
        if race.race_status not in allowed:
            self._feedback('Button KB{}: race is not running - nothing deleted'
                           .format(kb + 1))
            return
        laps = (race.node_laps or {}).get(seat) or []
        last_index = None
        for i in range(len(laps) - 1, -1, -1):
            if not laps[i].deleted:
                last_index = i
                break
        callsign = self._seat_callsign(seat) or self._seat_label(seat)
        if last_index is None:
            self._feedback('Button KB{}: {} has no laps to delete'
                           .format(kb + 1, callsign))
            return
        lap_number = laps[last_index].lap_number
        race.delete_lap(seat, last_index)
        logger.info('key_control: KB%d deleted lap idx %s (lap %s), seat %d (%s)',
                    kb + 1, last_index, lap_number, seat + 1, callsign)
        what = 'holeshot' if not lap_number else 'lap {}'.format(lap_number)
        self._feedback('Button KB{}: deleted {} of {} ({})'
                       .format(kb + 1, what, callsign, self._seat_label(seat)),
                       speak_text='{} lap deleted'.format(callsign))

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
        self.broadcast_state()

    def on_heartbeat(self, data=None):
        if isinstance(data, dict):
            self._forwarder = data
        self._forwarder_seen = monotonic()
        self.broadcast_state()

    # ------------------------------------------------------------ panel state

    def on_get_state(self, _data=None):
        self.broadcast_state()

    def broadcast_state(self, _args=None):
        try:
            self._rhapi.ui.socket_broadcast(EV_STATE, self._state())
        except Exception:
            logger.exception('state broadcast failed')

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
            })
        age = monotonic() - self._forwarder_seen if self._forwarder_seen else None
        return {
            'enabled': self._opt_bool(OPT_ENABLED, True),
            'race_status': race.race_status,
            'mapping': mapping,
            'forwarder': {
                'online': age is not None and age < FORWARDER_TIMEOUT,
                'age': round(age, 1) if age is not None else None,
                'host': (self._forwarder or {}).get('host'),
                'devices': (self._forwarder or {}).get('devices') or [],
            },
            'theme': self._opt(OPT_THEME, 'dark'),
        }

    def on_option_set(self, args=None):
        if args and str(args.get('option', '')).startswith('bk_'):
            self.broadcast_state()
