import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "kdm.tablet-display"
  ipcTarget: "kdm.tablet-display"
  manageIpc: false // BarWidget.qml already owns the "kdm.tablet-display" IpcHandler

  property var anchorItem: null
  property var hostWidget: null

  readonly property var status: hostWidget ? hostWidget.status : ({
    state: "stopped", bind_ip: null, vnc_port: null, setup_url: null,
    qr_url: null, width: null, height: null, position: "auto-right",
    display_mode: "extend", mirror_source: null,
    client_connected: false, last_error: null,
  })
  readonly property bool daemonReachable: hostWidget ? hostWidget.daemonReachable : false
  readonly property bool running: status.state === "running"
  readonly property bool hasError: status.state === "error"
  readonly property bool mirroring: status.display_mode === "mirror"

  readonly property color foreground: root.bar ? root.bar.foreground : Color.foreground
  readonly property color background: root.bar ? root.bar.background : Color.background
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  readonly property string presetsPath: {
    var url = String(Qt.resolvedUrl("presets.json"))
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }
  property var presets: []
  property string selectedPresetId: "auto"

  readonly property var presetOptions: {
    var opts = [{ value: "auto", label: "Auto-detected" }]
    for (var i = 0; i < presets.length; i++)
      opts.push({ value: presets[i].id, label: presets[i].label })
    return opts
  }

  readonly property var positionOptions: [
    { value: "auto-right", label: "Right of main screen" },
    { value: "auto-left", label: "Left of main screen" },
    { value: "auto-up", label: "Above main screen" },
    { value: "auto-down", label: "Below main screen" },
  ]

  // "Extend" is Hyprland's only non-mirrored multi-monitor mode -- every
  // output, physical or virtual, already gets its own independent
  // workspace, so there is no separate "own workspaces" mode beyond this.
  readonly property var displayModeOptions: [
    { value: "extend", label: "Extend (own workspace)" },
    { value: "mirror", label: "Duplicate main screen" },
  ]

  function open() { controller.show() }
  function close() { controller.hide() }
  function toggle() { opened ? close() : open() }

  function applyPreset(id) {
    root.selectedPresetId = id
    if (id === "auto" || !hostWidget) return
    for (var i = 0; i < presets.length; i++) {
      if (presets[i].id === id) {
        hostWidget.setResolution(presets[i].width, presets[i].height, presets[i].refresh)
        return
      }
    }
  }

  function statusLine() {
    if (!daemonReachable) return "Daemon not running"
    if (hasError) return "Error"
    if (running) return "On"
    return "Off"
  }

  function resolutionLine() {
    if (!running || !status.width) return ""
    if (root.mirroring) return "Duplicating " + (status.mirror_source || "main screen") +
      " — " + status.width + "×" + status.height
    return status.width + "×" + status.height +
      (status.scale && status.scale !== 1 ? " (scale " + status.scale + ")" : "")
  }

  FileView {
    path: root.presetsPath
    printErrors: false
    onLoaded: {
      try { root.presets = JSON.parse(text()) } catch (e) { root.presets = [] }
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root
    bar: root.bar
    open: root.opened
    centerOnBar: true
    contentWidth: popup.fittedContentWidth(Style.space(320))
    contentHeight: popup.fittedContentHeight(content.implicitHeight, Style.space(560))

    Column {
      id: content
      width: parent.width
      spacing: Style.spacing.lg

      Row {
        width: parent.width
        spacing: Style.spacing.md

        Text {
          text: "Tablet Display"
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Text {
          text: root.statusLine()
          textFormat: Text.PlainText
          color: root.hasError ? Color.urgent : (root.running ? Color.accent : Qt.darker(root.foreground, 1.4))
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }
      }

      Text {
        visible: root.hasError
        width: parent.width
        text: root.status.last_error || ""
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        color: Color.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Text {
        visible: !root.daemonReachable
        width: parent.width
        text: "kdm-tablet-displayd isn't reachable. Check: systemctl --user status kdm-tablet-displayd"
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        color: Qt.darker(root.foreground, 1.35)
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      // -- QR + connection details, only meaningful once a session is running --

      Column {
        visible: root.running
        width: parent.width
        spacing: Style.spacing.md

        Image {
          id: qrImage
          anchors.horizontalCenter: parent.horizontalCenter
          width: Style.space(180)
          height: Style.space(180)
          source: hostWidget ? hostWidget.qrDataUri : ""
          asynchronous: true
          cache: false
          fillMode: Image.PreserveAspectFit
          visible: status === Image.Ready
        }

        Text {
          visible: qrImage.status !== Image.Ready
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: "Loading QR code…"
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Text {
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: "Scan with the tablet, or open:"
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: root.status.setup_url || ""
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideMiddle
        }

        BorderSurface {
          width: parent.width
          implicitHeight: connColumn.implicitHeight + Style.spacing.lg * 2
          color: Style.controlFill(false, false, root.foreground, Color.accent)
          borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
          radius: Style.cornerRadius

          Column {
            id: connColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.spacing.lg
            spacing: Style.spacing.xs

            Text {
              width: parent.width
              text: "Manual VNC address"
              textFormat: Text.PlainText
              color: Qt.darker(root.foreground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Text {
              width: parent.width
              text: (root.status.bind_ip || "") + ":" + (root.status.vnc_port || "")
              textFormat: Text.PlainText
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Text {
              width: parent.width
              visible: root.resolutionLine() !== ""
              text: root.resolutionLine()
              textFormat: Text.PlainText
              color: Qt.darker(root.foreground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Text {
              width: parent.width
              text: root.status.client_connected ? "● Tablet connected" : "○ Waiting for connection…"
              textFormat: Text.PlainText
              color: root.status.client_connected ? Color.accent : Qt.darker(root.foreground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }

        Dropdown {
          visible: !root.mirroring
          width: parent.width
          label: "Resolution preset (overrides auto-detect)"
          value: root.selectedPresetId
          options: root.presetOptions
          foreground: root.foreground
          background: root.background
          onChanged: function(value) { root.applyPreset(value) }
        }
      }

      Dropdown {
        width: parent.width
        label: "Display mode"
        value: root.status.display_mode || "extend"
        options: root.displayModeOptions
        foreground: root.foreground
        background: root.background
        // A standing preference, not tied to a running session: persists
        // on the daemon side and applies immediately if already running,
        // or takes effect on the next Start otherwise.
        onChanged: function(value) { hostWidget.setDisplayMode(value) }
      }

      // Position and per-tablet resolution both mean nothing while
      // duplicating another screen -- the mirrored output takes that
      // screen's own position and resolution, not a chosen one.
      Dropdown {
        visible: !root.mirroring
        width: parent.width
        label: "Position relative to main screen"
        value: root.status.position || "auto-right"
        options: root.positionOptions
        foreground: root.foreground
        background: root.background
        onChanged: function(value) { hostWidget.setPosition(value) }
      }

      Button {
        width: parent.width
        text: root.running ? "Stop" : "Start"
        bordered: true
        enabled: root.daemonReachable
        foreground: root.foreground
        background: root.background
        onClicked: root.running ? hostWidget.stop() : hostWidget.start()
      }
    }
  }
}
