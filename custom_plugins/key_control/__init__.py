'''
KEY CONTROL plugin for RotorHazard.

Physical two-key USB keyboards as per-pilot lap marshals: key 1 adds a manual
lap for the mapped seat (same as the Run page '+ Lap' button), key 2 deletes
that seat's last recorded lap. Key events arrive from a small forwarder script
(pi_forwarder/keyboard_forwarder.py) running on the machine the keyboards are
plugged into - typically a Raspberry Pi - over the server's Socket.IO port.

Keyboard N maps to the Nth occupied seat of the current heat by default
(pilots on R1/R3/R6/R8 -> KB1=R1, KB2=R3, KB3=R6, KB4=R8), with per-keyboard
fixed-seat overrides in the settings panel.
'''

from flask import Blueprint

from eventmanager import Evt
from .controller import (
    ButtonKeyboardController, PLUGIN_ID,
    EV_HELLO, EV_HEARTBEAT, EV_KEY, EV_GET_STATE,
)


def initialize(rhapi):
    controller = ButtonKeyboardController(rhapi)

    # static assets for the Run-page panel
    bp = Blueprint(PLUGIN_ID, __name__, static_folder='static',
                   static_url_path='/key_control/static')
    rhapi.ui.blueprint_add(bp)

    rhapi.events.on(Evt.STARTUP, controller.register_ui,
                    name='key_control_ui')

    # forwarder -> server
    rhapi.ui.socket_listen(EV_KEY, controller.on_key_event)
    rhapi.ui.socket_listen(EV_HELLO, controller.on_hello)
    rhapi.ui.socket_listen(EV_HEARTBEAT, controller.on_heartbeat)
    # panel <-> server
    rhapi.ui.socket_listen(EV_GET_STATE, controller.on_get_state)

    # keep the panel's keyboard->pilot mapping fresh
    rhapi.events.on(Evt.HEAT_SET, controller.broadcast_state,
                    name='key_control_heat')
    rhapi.events.on(Evt.RACE_STAGE, controller.broadcast_state,
                    name='key_control_stage')
    rhapi.events.on(Evt.RACE_START, controller.broadcast_state,
                    name='key_control_start')
    rhapi.events.on(Evt.RACE_STOP, controller.broadcast_state,
                    name='key_control_stop')
    rhapi.events.on(Evt.LAPS_CLEAR, controller.broadcast_state,
                    name='key_control_clear')
    rhapi.events.on(Evt.OPTION_SET, controller.on_option_set,
                    name='key_control_opts')
