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
    display_mode: "extend", mirror_source: null,
    encryption_enabled: true, vnc_username: null, vnc_password: null,
    client_connected: false, last_error: null,
  })
  property var status: offlineStatus
  readonly property bool daemonReachable: !!backendSocket && backendSocket.connected
  readonly property bool running: status.state === "running"
  readonly property bool hasError: status.state === "error"

  // `omarchy plugin add`/`remove` only do file operations (clone, enable,
  // delete) -- confirmed against the plugin docs, there is no install/remove
  // hook that would run scripts/install-daemon.sh automatically. Without
  // this, a fresh install would silently never start the daemon. Mirrors
  // kdm.presets' own pattern for its post-boot hook: ask consent and run it
  // from inside the plugin, not a silent background action on load.
  readonly property string installScriptPath: {
    var url = String(Qt.resolvedUrl("scripts/install-daemon.sh"))
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }
  property bool installing: false
  property string installOutput: ""

  function runInstall() {
    if (root.installing) return
    root.installing = true
    root.installOutput = ""
    installProc.running = true
  }

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
  function regeneratePassword() { _send("regenerate_password", {}) }
  function setPassword(password) { _send("set_password", { password: password }) }
  function setUsername(username) { _send("set_username", { username: username }) }
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
  property bool _reconnectPending: false

  // Hard-recreate: destroy the Loader's item and build a fresh one, rather
  // than toggling `connected` on a possibly-wedged existing Socket -- live
  // testing confirmed toggling `connected` on the same instance after a
  // daemon restart never recovers it. Guarded by `_reconnectPending` so the
  // watchdog below (which polls unconditionally) can't stack overlapping
  // teardown/rebuild cycles on top of each other.
  function _reconnect() {
    if (root._reconnectPending) return
    root._reconnectPending = true
    socketLoader.active = false
    reconnectTimer.restart()
  }

  Loader {
    id: socketLoader
    active: true
    // Stamped on every (re)creation, not just left at its 0 default -- gives
    // the watchdog timer below something to compare against instead of the
    // "never received a message" 0 default, which would otherwise never
    // look stale enough to act on.
    onLoaded: root._lastMessageTime = Date.now()
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
          }
        }
        onError: function(error) { connected = false }
      }
    }
  }

  Timer {
    id: reconnectTimer
    interval: 3000
    onTriggered: {
      root._reconnectPending = false
      socketLoader.active = true
    }
  }

  // The single source of truth for reconnection: an unconditional poll,
  // not a reaction to onError/onConnectedChanged. Confirmed live that the
  // signal-driven chain above can silently stop producing any further
  // connection attempts at all after the very first failure (e.g. daemon
  // not installed yet when the widget first loads) -- no further Socket
  // error ever gets logged, and nothing else notices. This timer doesn't
  // care why the connection is down or whether any signal fired; it just
  // checks the observable state every tick and forces a hard reconnect
  // whenever it looks wrong, which is what actually recovers every case
  // seen so far (boot-ordering, daemon restart, daemon crash-recover).
  Timer {
    interval: 3000
    running: true
    repeat: true
    onTriggered: {
      var disconnected = !root.backendSocket || !root.backendSocket.connected
      var stale = root._lastMessageTime > 0 && (Date.now() - root._lastMessageTime > 8000)
      if (disconnected || stale) {
        root._lastMessageTime = 0
        root._reconnect()
        return
      }
      root.refreshStatus() // doubles as the liveness probe
    }
  }

  Process {
    id: installProc
    command: ["bash", root.installScriptPath]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.installOutput += String(text || "")
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.installOutput += String(text || "")
    }
    onExited: function(exitCode) {
      root.installing = false
      // The ufw step inside the script needs an interactive sudo prompt
      // this backgrounded process cannot provide (confirmed live -- sudo
      // fails immediately with "a terminal is required" when run this
      // way); the script already prints the exact command to run manually
      // in that case, which installOutput surfaces in the panel as-is.
      if (!root.daemonReachable) reconnectTimer.restart()
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
    function setup(): string { root.runInstall(); return "installing" }
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
