'''
KEY CONTROL - cloud judge relay.

Gives every occupied seat a phone-sized judge page on the public internet, so
marshals can add and remove laps from anywhere with a signal instead of
standing at a USB keyboard. The timer keeps the outbound connection, so the
RotorHazard server needs no port forwarding, no public IP and no inbound
firewall rule.

Two greenlets while the relay is on:

  * _poll_loop  - polls the relay for judge presses and hands each one to the
                  controller as if it had come from a button keyboard; fast
                  while a race runs, slowly otherwise.
  * _state_loop - pushes a snapshot (pilots, channels, lap counts, race state)
                  whenever it changes, and at least every few seconds so the
                  judge pages can tell a quiet timer from a dead one.

A press carries the age of the tap, not a wall-clock time: the judge's phone
measures how long ago the button went down, the relay adds its queue time, and
this side records the lap that far back on the race clock. Network latency
therefore never moves a lap time, and the judges' phone clocks never matter.
'''

import json
import logging
import secrets
from time import monotonic

import gevent
from gevent.event import Event
import requests

logger = logging.getLogger(__name__)

# room codes and judge tokens: Crockford base32 without I, L, O and U, so a
# code read out over a radio cannot be mistyped
ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

DEFAULT_URL = 'https://judge.airmode.app'
ROOM_LEN = 6
TOKEN_LEN = 12
SECRET_LEN = 40

POLL_RACING = 0.7       # s between command polls while a race is running
POLL_IDLE = 3.0         # s between command polls the rest of the time
POLL_TIMEOUT = 10       # s before we give up on a poll request
STATE_TIMEOUT = 10      # s for a snapshot push
HEARTBEAT = 8           # s between snapshots when nothing changes
BACKOFF_MAX = 30        # s between retries after repeated failures
MAX_AGE_MS = 30000      # a tap older than this is not placed on the race clock


def _code(length):
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))


class CloudRelay:
    '''Owns the connection to the judge relay and the room's identity.'''

    def __init__(self, rhapi, controller):
        self._rhapi = rhapi
        self._ctl = controller
        self._greenlets = []
        self._running = False
        self._state = {
            'online': False,
            'room': '',
            'error': '',
            'judges': 0,
            'last_ok': None,
            'durable': True,
        }
        self._session = requests.Session()
        self._dirty = Event()
        self._seq = 0
        # half the round trip of the last short request: the server -> timer
        # leg of a command's age
        self._leg_ms = 0.0

    # ------------------------------------------------------------- identity

    def _opt(self, name, default=''):
        try:
            value = self._rhapi.db.option(name)
        except Exception:
            return default
        return default if value is None or value == '' else value

    def _set(self, name, value):
        self._rhapi.db.option_set(name, value)

    @property
    def base_url(self):
        return str(self._opt('bk_cloud_url', DEFAULT_URL) or DEFAULT_URL).rstrip('/')

    @property
    def room(self):
        return str(self._opt('bk_cloud_room', '') or '').upper()

    def _secret(self):
        value = str(self._opt('bk_cloud_secret', '') or '')
        if len(value) < 32:
            value = secrets.token_hex(SECRET_LEN // 2)
            self._set('bk_cloud_secret', value)
        return value

    def _tokens(self):
        '''seat index -> judge token, stable for the life of the room so a
        judge's link keeps working between heats.'''
        try:
            tokens = json.loads(str(self._opt('bk_cloud_tokens', '{}')))
        except (TypeError, ValueError):
            tokens = {}
        return tokens if isinstance(tokens, dict) else {}

    def _token_for(self, seat):
        tokens = self._tokens()
        key = str(seat)
        if not tokens.get(key):
            tokens[key] = _code(TOKEN_LEN)
            self._set('bk_cloud_tokens', json.dumps(tokens))
        return tokens[key]

    def judge_links(self):
        '''[(seat, label, callsign, url)] for the occupied seats of this heat.'''
        if not self.room:
            return []
        links = []
        for seat in self._ctl.cloud_seats():
            token = self._token_for(seat['seat'])
            links.append({
                'seat': seat['seat'],
                'label': seat['label'],
                'callsign': seat['callsign'],
                'url': '{}/j/{}'.format(self.base_url, token),
                # the same link as a QR image, for a judge to scan off the
                # race director's screen
                'qr': '{}/qr/{}'.format(self.base_url, token),
            })
        return links

    def new_room(self):
        '''Fresh room code and fresh judge links; every old link stops working.'''
        self._close_room()
        self._set('bk_cloud_room', _code(ROOM_LEN))
        self._set('bk_cloud_tokens', '{}')
        self._seq = 0
        self.restart()

    # ------------------------------------------------------------ lifecycle

    def enabled(self):
        return self._opt('bk_cloud_enabled', False) in (True, 1, '1', 'true', 'True')

    def start(self):
        if self._running or not self.enabled():
            return
        if not self.room:
            self._set('bk_cloud_room', _code(ROOM_LEN))
        self._running = True
        self._greenlets = [gevent.spawn(self._poll_loop), gevent.spawn(self._state_loop)]
        logger.info('key_control: cloud relay starting, room %s at %s',
                    self.room, self.base_url)

    def stop(self):
        self._running = False
        for job in self._greenlets:
            job.kill(block=False)
        self._greenlets = []
        self._state['online'] = False
        self._dirty.set()

    def restart(self):
        self.stop()
        gevent.sleep(0)
        self.start()

    def notify_change(self):
        '''Something a judge can see has changed - push a snapshot now.'''
        self._dirty.set()

    def status(self):
        state = dict(self._state)
        state['enabled'] = self.enabled()
        state['url'] = self.base_url
        state['room'] = self.room
        state['board'] = '{}/r/{}'.format(self.base_url, self.room) if self.room else ''
        state['links'] = self.judge_links()
        return state

    # ----------------------------------------------------------- http helper

    def _headers(self):
        return {'authorization': 'Bearer ' + self._secret(),
                'content-type': 'application/json'}

    def _claim(self):
        '''Open (or re-open) the room. A taken code means somebody else got
        there first, so take another one.'''
        for _ in range(5):
            room = self.room or _code(ROOM_LEN)
            resp = self._session.post(
                self.base_url + '/api/room/claim',
                json={'room': room, 'version': self._ctl.version},
                headers=self._headers(), timeout=STATE_TIMEOUT)
            if resp.status_code == 409:
                self._set('bk_cloud_room', _code(ROOM_LEN))
                self._set('bk_cloud_tokens', '{}')
                continue
            resp.raise_for_status()
            body = resp.json()
            self._set('bk_cloud_room', room)
            self._state['durable'] = bool(body.get('durable', True))
            return True
        raise RuntimeError('could not claim a room code')

    def _close_room(self):
        if not self.room:
            return
        try:
            self._session.post(self.base_url + '/api/room/close',
                               json={'room': self.room},
                               headers=self._headers(), timeout=STATE_TIMEOUT)
        except Exception:
            logger.debug('key_control: could not close room %s', self.room)

    # -------------------------------------------------------- state pushing

    def _snapshot(self):
        snap = self._ctl.cloud_snapshot()
        for seat in snap['seats']:
            seat['token'] = self._token_for(seat['seat'])
        self._seq += 1
        snap['seq'] = self._seq
        snap['room'] = self.room
        return snap

    def _state_loop(self):
        claimed = False
        backoff = 1
        while self._running:
            # Cleared before the snapshot is built, so a change that lands
            # while we are pushing schedules the next push instead of being
            # swallowed by this one.
            self._dirty.clear()
            try:
                if not claimed:
                    self._claim()
                    claimed = True
                sent = monotonic()
                resp = self._session.post(self.base_url + '/api/room/state',
                                          json=self._snapshot(),
                                          headers=self._headers(),
                                          timeout=STATE_TIMEOUT)
                resp.raise_for_status()
                # a short request: half its round trip is the relay -> timer leg
                self._leg_ms = min(1000.0, (monotonic() - sent) * 500.0)
                self._state.update(online=True, error='', last_ok=monotonic())
                backoff = 1
            except Exception as ex:
                claimed = False
                self._state.update(online=False,
                                   error='{}: {}'.format(ex.__class__.__name__, ex))
                logger.warning('key_control: cloud state push failed (%s)', ex)
                gevent.sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue
            finally:
                self._ctl.broadcast_state()
            self._dirty.wait(timeout=HEARTBEAT)

    # ------------------------------------------------------- command pulling

    def _poll_loop(self):
        backoff = 1
        while self._running:
            if not self.room:
                gevent.sleep(1)
                continue
            try:
                resp = self._session.get(
                    self.base_url + '/api/room/commands',
                    params={'room': self.room},
                    headers=self._headers(), timeout=POLL_TIMEOUT)
                if resp.status_code in (401, 403, 404):
                    # the room went away under us - re-claim on the next push
                    gevent.sleep(2)
                    continue
                resp.raise_for_status()
                body = resp.json()
                backoff = 1
            except Exception as ex:
                logger.debug('key_control: cloud poll failed (%s)', ex)
                gevent.sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue

            server_now = float(body.get('serverTime') or 0)
            for cmd in body.get('commands') or []:
                self._dispatch(cmd, server_now)

            # A press carries its own age, so a slow poll costs nothing but the
            # delay before the lap appears on the timer's screen. Poll fast only
            # while that matters.
            gevent.sleep(POLL_RACING if self._ctl.cloud_racing() else POLL_IDLE)

    def _dispatch(self, cmd, server_now):
        try:
            seat = int(cmd['seat'])
            button = cmd['button']
        except (KeyError, TypeError, ValueError):
            return
        if button not in ('add', 'del'):
            return

        # How long ago the judge actually tapped: their own measurement, plus
        # the time the command waited in the relay, plus the leg back to us.
        queued_ms = max(0.0, server_now - float(cmd.get('recvAt') or server_now))
        age_ms = float(cmd.get('ageAtSend') or 0) + queued_ms + self._leg_ms
        age_ms = max(0.0, min(MAX_AGE_MS, age_ms))

        source = 'Judge {}'.format(cmd.get('judge') or '?')
        logger.info('key_control: cloud %s %s seat %d (%.0f ms ago)',
                    source, button, seat + 1, age_ms)
        self._ctl.on_remote_press(seat, button, age_ms, source)
        self.notify_change()


def demo():
    '''Self-check for the age arithmetic - the one place a wrong number
    silently corrupts lap times.'''

    class FakeCtl:
        version = 'test'
        presses = []

        def on_remote_press(self, seat, button, age_ms, source):
            FakeCtl.presses.append((seat, button, round(age_ms), source))

        def broadcast_state(self):
            pass

        def cloud_seats(self):
            return []

        def cloud_racing(self):
            return False

    relay = CloudRelay.__new__(CloudRelay)
    relay._ctl = FakeCtl()
    relay._leg_ms = 40.0
    relay._dirty = gevent.event.Event()

    # tap 300 ms before sending, then 900 ms sat in the relay queue
    relay._dispatch({'seat': 2, 'button': 'add', 'ageAtSend': 300, 'recvAt': 1000,
                     'judge': 'ab12'}, server_now=1900)
    assert FakeCtl.presses[-1] == (2, 'add', 1240, 'Judge ab12'), FakeCtl.presses[-1]

    # a clock that ran backwards must not produce a negative age
    relay._dispatch({'seat': 0, 'button': 'del', 'ageAtSend': 0, 'recvAt': 5000,
                     'judge': 'cd34'}, server_now=1000)
    assert FakeCtl.presses[-1] == (0, 'del', 40, 'Judge cd34'), FakeCtl.presses[-1]

    # a stale offline replay is clamped, never projected past the clamp
    relay._dispatch({'seat': 1, 'button': 'add', 'ageAtSend': 999999, 'recvAt': 0,
                     'judge': 'ef56'}, server_now=0)
    assert FakeCtl.presses[-1] == (1, 'add', MAX_AGE_MS, 'Judge ef56'), FakeCtl.presses[-1]

    # junk from the wire is dropped rather than turned into a lap
    before = len(FakeCtl.presses)
    relay._dispatch({'seat': 'x', 'button': 'add'}, server_now=0)
    relay._dispatch({'seat': 1, 'button': 'shrug'}, server_now=0)
    assert len(FakeCtl.presses) == before

    codes = {_code(ROOM_LEN) for _ in range(500)}
    assert len(codes) > 490, 'room codes are not random enough'
    assert not (set(''.join(codes)) & set('ILOU')), 'ambiguous letters in room codes'
    print('cloud.py self-check ok')


if __name__ == '__main__':
    demo()
