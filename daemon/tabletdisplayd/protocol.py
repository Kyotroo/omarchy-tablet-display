"""IPC wire protocol for kdm-tablet-displayd.

Newline-delimited JSON over a Unix domain socket, one JSON object per line.
Shape mirrors hyprmoncfg's protocol (request/response/event with an explicit
protocol_version) so a client can feature-detect instead of guessing from a
release number.
"""

PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1

METHOD_STATUS = "status"
METHOD_SUBSCRIBE = "subscribe"
METHOD_START = "start"
METHOD_STOP = "stop"
METHOD_SET_RESOLUTION = "set_resolution"
METHOD_GET_QR = "get_qr"
METHOD_SET_POSITION = "set_position"
METHOD_SET_DISPLAY_MODE = "set_display_mode"
METHOD_SET_ENCRYPTION = "set_encryption"
METHOD_REGENERATE_PASSWORD = "regenerate_password"
METHOD_SET_PASSWORD = "set_password"
METHOD_SET_USERNAME = "set_username"

EVENT_STATUS = "status"

KNOWN_METHODS = {
    METHOD_STATUS,
    METHOD_SUBSCRIBE,
    METHOD_START,
    METHOD_STOP,
    METHOD_SET_RESOLUTION,
    METHOD_GET_QR,
    METHOD_SET_POSITION,
    METHOD_SET_DISPLAY_MODE,
    METHOD_SET_ENCRYPTION,
    METHOD_REGENERATE_PASSWORD,
    METHOD_SET_PASSWORD,
    METHOD_SET_USERNAME,
}


def make_response(request, result=None, error=None):
    response = {
        "type": "response",
        "protocol_version": request.get("protocol_version", PROTOCOL_VERSION),
        "server_protocol_version": PROTOCOL_VERSION,
        "id": request.get("id"),
    }
    if error is not None:
        response["error"] = error
    elif result is not None:
        response["result"] = result
    return response


def error_response(request, code, message, data=None):
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return make_response(request, error=error)


def make_event(event, data):
    return {
        "type": "event",
        "protocol_version": PROTOCOL_VERSION,
        "event": event,
        "data": data,
    }
