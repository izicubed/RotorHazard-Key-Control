'''
KEY CONTROL plugin for RotorHazard.

Physical two-key USB keyboards as per-pilot lap marshals: key 1 adds/confirms
a lap for the mapped seat, key 2 deletes that seat's last recorded lap. Key
events arrive from a small forwarder script (pi_forwarder/keyboard_forwarder.py)
running on the machine the keyboards are plugged into - typically a Raspberry
Pi - over the server's Socket.IO port.

Work modes: MANUAL (the timer's own RSSI laps are suppressed, buttons count
every lap) and SEMI (the timer counts as usual and key presses confirm laps
inside a +-threshold window: green confirmed / yellow unconfirmed / blue
button-only / red deleted).

Keyboard N maps to the Nth occupied seat of the current heat by default, with
per-keyboard fixed-seat overrides and a panel-driven Calibrate flow that binds
keyboards to channels by pressing a key on each in turn.

The same two actions are also available to judges on their own phones: turn on
Cloud judges and the plugin opens a room on the judge relay and prints one
link per occupied seat (see cloud.py). The timer dials out, so no port
forwarding is involved.
'''

from flask import Blueprint

from eventmanager import Evt
from .controller import (
    ButtonKeyboardController, PLUGIN_ID,
    EV_HELLO, EV_HEARTBEAT, EV_KEY, EV_GET_STATE,
    EV_SET_MODE, EV_SET_THRESHOLD, EV_CALIBRATE, EV_LINK, EV_CLOUD,
)


def initialize(rhapi):
    controller = ButtonKeyboardController(rhapi)

    # static assets for the Run-page panel
    bp = Blueprint(PLUGIN_ID, __name__, static_folder='static',
                   static_url_path='/key_control/static')
    rhapi.ui.blueprint_add(bp)

    rhapi.events.on(Evt.STARTUP, controller.register_ui,
                    name='key_control_ui')
    rhapi.events.on(Evt.STARTUP, lambda _a=None: controller.cloud.start(),
                    name='key_control_cloud_up', priority=200)
    rhapi.events.on(Evt.SHUTDOWN, lambda _a=None: controller.cloud.stop(),
                    name='key_control_cloud_down')

    # forwarder -> server
    rhapi.ui.socket_listen(EV_KEY, controller.on_key_event)
    rhapi.ui.socket_listen(EV_HELLO, controller.on_hello)
    rhapi.ui.socket_listen(EV_HEARTBEAT, controller.on_heartbeat)
    # panel <-> server
    rhapi.ui.socket_listen(EV_GET_STATE, controller.on_get_state)
    rhapi.ui.socket_listen(EV_SET_MODE, controller.on_set_mode)
    rhapi.ui.socket_listen(EV_SET_THRESHOLD, controller.on_set_threshold)
    rhapi.ui.socket_listen(EV_CALIBRATE, controller.on_calibrate)
    rhapi.ui.socket_listen(EV_LINK, controller.on_link)
    rhapi.ui.socket_listen(EV_CLOUD, controller.on_cloud)

    # work modes: classify every recorded lap (ours / timer's)
    rhapi.events.on(Evt.RACE_LAP_RECORDED, controller.on_lap_recorded,
                    name='key_control_lap')

    # per-race state reset + keep the panel mapping fresh
    rhapi.events.on(Evt.RACE_STAGE, controller.on_race_reset,
                    name='key_control_stage')
    rhapi.events.on(Evt.LAPS_CLEAR, controller.on_race_reset,
                    name='key_control_clear')
    rhapi.events.on(Evt.HEAT_SET, controller.on_change,
                    name='key_control_heat')
    rhapi.events.on(Evt.RACE_START, controller.on_change,
                    name='key_control_start')
    rhapi.events.on(Evt.RACE_STOP, controller.on_change,
                    name='key_control_stop')
    rhapi.events.on(Evt.LAP_DELETE, controller.on_change,
                    name='key_control_lapdel')
    rhapi.events.on(Evt.OPTION_SET, controller.on_option_set,
                    name='key_control_opts')
