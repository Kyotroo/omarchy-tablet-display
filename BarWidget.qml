import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon + thin Unix-socket client for kdm-tablet-displayd. Mirrors the
// Socket{parser: SplitParser} pattern hyprmoncfg's Omarchy panel uses to
// talk to its own daemon: the daemon owns every hyprctl/wayvnc/HTTP call,
// this widget only ever sends {method, params} lines and applies whatever
// status comes back. Never shells out to hyprctl/wayvnc directly.
BarWidget {
  id: root
  moduleName: "kdm.tablet-display"

  readonly property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/kdm-tablet-displayd.sock"

  readonly property var offlineStatus: ({
    state: "stopped", output_name: null, bind_ip: null, vnc_port: null,
    setup_port: null, setup_url: null, qr_url: null, width: null,
    height: null, refresh: null, scale: null, position: "auto-right",
    display_mode: "extend", mirror_source: null, client_connected: false,
    last_error: null,
  })
  property var status: offlineStatus
  readonly property bool daemonReachable: !!backendSocket && backendSocket.connected
  readonly property bool running: status.state === "running"
  readonly property bool hasError: status.state === "error"

  // Delivered over this same socket as base64, not as an Image{source:
  // qr_url} network fetch -- nothing else in this shell loads Image from a
  // network URL, and testing confirmed it never resolves here (stays
  // Loading forever). A data: URI needs no network stack at all.
  property string qrDataUri: ""
  property string _qrRequestedForOutput: ""

  readonly property bool opened: panelLoader.item
    ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  property int _nextRequestId: 1

  function _send(method, params) {
    if (!backendSocket || !backendSocket.connected) return
    var request = {
      type: "request", protocol_version: 1,
      id: String(root._nextRequestId++), method: method, params: params || {},
    }
    backendSocket.write(JSON.stringify(request) + "\n")
    backendSocket.flush()
  }

  function start() { _send("start", {}) }
  function stop() { _send("stop", {}) }
  function refreshStatus() { _send("status", {}) }
  function setResolution(width, height, refresh) {
    _send("set_resolution", { width: width, height: height, refresh: refresh })
  }
  function setPosition(position) { _send("set_position", { position: position }) }
  function setDisplayMode(mode) { _send("set_display_mode", { mode: mode }) }
  function requestQr() { _send("get_qr", {}) }

  function _applyStatus(newStatus) {
    root.status = newStatus
    if (newStatus.state === "running" && newStatus.output_name !== root._qrRequestedForOutput) {
      root._qrRequestedForOutput = newStatus.output_name
      root.requestQr()
    } else if (newStatus.state !== "running") {
      root._qrRequestedForOutput = ""
      root.qrDataUri = ""
    }
  }

  function _handleMessage(line) {
    var message
    try { message = JSON.parse(line) } catch (e) { return }
    root._lastMessageTime = Date.now()
    if (message.type === "response" && message.result) {
      if ("png_base64" in message.result) root.qrDataUri = "data:image/png;base64," + message.result.png_base64
      else root._applyStatus(message.result)
    } else if (message.type === "event" && message.event === "status" && message.data) {
      root._applyStatus(message.data)
    }
  }

  function _tooltipText() {
    if (!root.daemonReachable) return "Tablet Display: daemon not running"
    if (root.hasError) return "Tablet Display: " + (root.status.last_error || "error")
    if (root.running) return "Tablet Display: on — " + root.status.bind_ip + ":" + root.status.vnc_port
    return "Tablet Display: off"
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  // The Socket lives inside a Loader so a stuck connection can be
  // recovered by fully destroying and recreating the native object, not by
  // toggling a property on a possibly-wedged one. Live testing showed
  // toggling `connected` on the same Socket instance after a daemon
  // restart never recovers: Quickshell logs one PeerClosedError +
  // ServerNotFoundError pair and then goes silent, with no further
  // connection attempts at all even minutes later -- whatever state that
  // leaves the object in, only tearing it down and building a fresh one
  // (via Loader.active) has been confirmed to actually reconnect.
  readonly property var backendSocket: socketLoader.item
  property real _lastMessageTime: 0

  function _reconnect() {
    socketLoader.active = false
    reconnectTimer.restart()
  }

  Loader {
    id: socketLoader
    active: true
    sourceComponent: Component {
      Socket {
        path: root.socketPath
        connected: true
        parser: SplitParser {
          splitMarker: "\n"
          onRead: function(line) { root._handleMessage(line) }
        }
        onConnectedChanged: {
          if (connected) {
            root._lastMessageTime = Date.now()
            root._send("subscribe", {})
          } else {
            root.status = root.offlineStatus
            root._reconnect()
          }
        }
        // Quickshell does not flip `connected` back to false on its own when
        // the peer errors out -- confirmed live: without this explicit
        // write, onConnectedChanged above never fires at all after a daemon
        // restart (the property genuinely never changes value from this
        // Socket's perspective), so nothing ever notices the connection is
        // dead. Setting it here is what makes onConnectedChanged run.
        onError: function(error) { connected = false }
      }
    }
  }

  // Covers both the boot-ordering case (daemon not up yet) and recovery
  // after `socketLoader.active` was cycled false -> true.
  Timer {
    id: reconnectTimer
    interval: 3000
    onTriggered: socketLoader.active = true
  }

  // A remote-side close (daemon restart/crash-recover) is not guaranteed
  // to be observable through the Socket's own signals in every case,
  // confirmed live: after `systemctl --user restart kdm-tablet-displayd`,
  // this widget sometimes kept reporting stale status indefinitely with no
  // further Socket error logged at all. This app-level heartbeat treats
  // "no message in two whole intervals" as proof the connection is dead
  // and forces the Loader-based hard reconnect above.
  Timer {
    interval: 5000
    running: true
    repeat: true
    onTriggered: {
      if (!root.backendSocket || !root.backendSocket.connected) return
      if (root._lastMessageTime > 0 && Date.now() - root._lastMessageTime > interval * 2) {
        root._lastMessageTime = 0
        root._reconnect()
        return
      }
      root.refreshStatus() // doubles as the liveness probe
    }
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "kdm.tablet-display"

    function toggle(): void { root.togglePanel() }
    function start(): string { root.start(); return "queued" }
    function stop(): string { root.stop(); return "queued" }
    function status(): string { return JSON.stringify(root.status) }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\u{f0379}" // "monitor" glyph -- confirmed codepoint, same one omarchy.monitor's Display panel uses
    active: root.running
    useActiveColor: true
    tooltipText: root._tooltipText()
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton) root.refreshStatus()
      else root.togglePanel()
    }
  }
}
