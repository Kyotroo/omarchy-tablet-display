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
    encryption_enabled: true, vnc_username: null, vnc_password: null,
    client_connected: false, last_error: null,
  })
  readonly property bool daemonReachable: hostWidget ? hostWidget.daemonReachable : false
  readonly property bool running: status.state === "running"
  readonly property bool hasError: status.state === "error"
  readonly property bool mirroring: status.display_mode === "mirror"

  // Two tabs instead of one long scrolling column (kdm.presets uses the
  // same approach) -- Connect has nothing useful to show before a session
  // exists, so a fresh panel opens on Settings; starting a session jumps
  // to Connect automatically since that is the reason someone just clicked
  // Start. Manual tab clicks afterward are left alone.
  property string currentTab: "settings"
  onRunningChanged: if (running) currentTab = "connect"

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
    // Anchored under the bar icon (KeyboardPanel's default), not centered
    // on the screen -- matches every other plugin's panel on this bar.
    contentWidth: popup.fittedContentWidth(Style.space(360))
    // Comfortably fits either tab's full content (~530-570 units) once
    // status has actually arrived over the socket. Right after a fresh
    // reconnect (shell restart, or the daemon itself restarting) the very
    // first render happens before that first status arrives, so
    // root.status.vnc_password is still null and the credentials block
    // below is briefly not there at all -- shorter content, so a smaller
    // card. The instant status arrives, that block appears and the card
    // grows, but the border's own resize and the newly-visible content
    // are not guaranteed to land in the same composited frame, which is
    // what actually produced the "content outside the border" look seen
    // live -- not a one-time settling delay, as a previous version of
    // this comment claimed. `clip: true` below is what actually
    // guarantees containment regardless of that timing.
    contentHeight: popup.fittedContentHeight(content.implicitHeight, Style.space(620))
    // KeyboardPanel exposes a combined verticalContentInset (used above)
    // but no horizontal equivalent -- computed the same way it does,
    // from its own public padding/borderSpec. Missing this is exactly
    // what let content render popup.contentWidth wide (the *card's* full
    // width) inside contentHolder, which is actually narrower than that
    // by this same inset on each side -- confirmed live as the "out of
    // bounds on the right" look, distinct from the height issue below.
    readonly property real horizontalContentInset:
      popup.padding * 2 + Border.left(popup.borderSpec) + Border.right(popup.borderSpec)

    Column {
      id: content
      // Explicit height (not just clip: true, which does nothing for a
      // Column sized by its own implicitHeight) so content can never
      // render outside the card regardless of load-order timing --
      // verticalContentInset is KeyboardPanel's own padding/border budget
      // already reserved by contentHolder, the actual parent this ends up
      // inside via the `default property alias contentItem` mechanism.
      clip: true
      width: popup.contentWidth - popup.horizontalContentInset
      height: popup.contentHeight - popup.verticalContentInset
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

      Column {
        visible: !root.daemonReachable
        width: parent.width
        spacing: Style.spacing.sm

        Text {
          width: parent.width
          text: "The background service isn't running yet. This is a " +
            "one-time step after installing the plugin."
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.35)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Button {
          width: parent.width
          text: hostWidget.installing ? "Setting up…" : "Set up now"
          iconText: "󰑓"
          iconSpinning: hostWidget.installing
          bordered: true
          enabled: !hostWidget.installing
          foreground: root.foreground
          background: root.background
          onClicked: hostWidget.runInstall()
        }

        Text {
          visible: hostWidget.installOutput !== ""
          width: parent.width
          text: hostWidget.installOutput
          wrapMode: Text.WrapAnywhere
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.35)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        visible: root.daemonReachable
        width: parent.width
        spacing: Style.spacing.sm

        Button {
          width: (parent.width - parent.spacing) / 2
          text: "Connect"
          bordered: true
          selected: root.currentTab === "connect"
          foreground: root.foreground
          background: root.background
          onClicked: root.currentTab = "connect"
        }

        Button {
          width: (parent.width - parent.spacing) / 2
          text: "Settings"
          bordered: true
          selected: root.currentTab === "settings"
          foreground: root.foreground
          background: root.background
          onClicked: root.currentTab = "settings"
        }
      }

      // -- Connect tab: QR + connection details, only meaningful once a --
      // -- session is running --

      Column {
        visible: root.daemonReachable && root.currentTab === "connect"
        width: parent.width
        spacing: Style.spacing.md

        Text {
          visible: !root.running
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: "Click Start to get a QR code."
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Image {
          id: qrImage
          visible: root.running
          anchors.horizontalCenter: parent.horizontalCenter
          width: Style.space(180)
          height: Style.space(180)
          source: hostWidget ? hostWidget.qrDataUri : ""
          asynchronous: true
          cache: false
          fillMode: Image.PreserveAspectFit
        }

        Text {
          visible: root.running && qrImage.status !== Image.Ready
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: "Loading QR code…"
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Text {
          visible: root.running
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          text: "Scan with the tablet, or open:"
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          visible: root.running
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
          visible: root.running
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
              wrapMode: Text.WordWrap
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
          visible: root.running && !root.mirroring
          width: parent.width
          label: "Resolution preset"
          value: root.selectedPresetId
          options: root.presetOptions
          foreground: root.foreground
          background: root.background
          onChanged: function(value) { root.applyPreset(value) }
        }
      }

      // -- Settings tab --

      Column {
        visible: root.daemonReachable && root.currentTab === "settings"
        width: parent.width
        spacing: Style.spacing.lg

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
          label: "Position"
          value: root.status.position || "auto-right"
          options: root.positionOptions
          foreground: root.foreground
          background: root.background
          onChanged: function(value) { hostWidget.setPosition(value) }
        }

        Text {
          // Not a toggle: a marketplace security review found the earlier
          // opt-in design exposed an unauthenticated remote desktop to the
          // whole LAN by default, not just the intended tablet. There is
          // no way to turn this off.
          width: parent.width
          text: "Every session is encrypted (TLS) and password-protected."
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        BorderSurface {
          visible: !!root.status.vnc_password
          width: parent.width
          implicitHeight: passwordColumn.implicitHeight + Style.spacing.lg * 2
          color: Style.controlFill(false, false, root.foreground, Color.accent)
          borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
          radius: Style.cornerRadius

          Column {
            id: passwordColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.spacing.lg
            spacing: Style.spacing.sm

            Text {
              width: parent.width
              text: "VNC username / password (enter these on the tablet)"
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              color: Qt.darker(root.foreground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Text {
              text: root.status.vnc_username || ""
              textFormat: Text.PlainText
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Row {
              width: parent.width
              spacing: Style.spacing.md

              TextField {
                id: customUsernameField
                width: parent.width - setUsernameButton.width - parent.spacing
                placeholderText: "Or choose your own username…"
                foreground: root.foreground
                accent: Color.accent
                onAccepted: setUsernameButton.clicked()
              }

              Button {
                id: setUsernameButton
                text: "Set"
                bordered: true
                enabled: customUsernameField.text.length > 0
                foreground: root.foreground
                background: root.background
                onClicked: {
                  hostWidget.setUsername(customUsernameField.text)
                  customUsernameField.text = ""
                }
              }
            }

            Row {
              width: parent.width
              spacing: Style.spacing.md

              Text {
                // Reserves space for the button explicitly (a plain Row
                // does not shrink children to fit) and wraps rather than
                // eliding -- unlike a label, truncating characters out of
                // a password shown for the user to copy would be
                // misleading.
                width: parent.width - regenerateButton.width - parent.spacing
                text: root.status.vnc_password || ""
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                font.bold: true
              }

              Button {
                id: regenerateButton
                text: "Regenerate"
                iconText: "󰑐"
                bordered: true
                foreground: root.foreground
                background: root.background
                onClicked: hostWidget.regeneratePassword()
              }
            }

            Row {
              width: parent.width
              spacing: Style.spacing.md

              TextField {
                id: customPasswordField
                width: parent.width - setPasswordButton.width - parent.spacing
                password: true
                placeholderText: "Or choose your own password…"
                foreground: root.foreground
                accent: Color.accent
                onAccepted: setPasswordButton.clicked()
              }

              Button {
                id: setPasswordButton
                text: "Set"
                bordered: true
                enabled: customPasswordField.text.length >= 4
                foreground: root.foreground
                background: root.background
                onClicked: {
                  hostWidget.setPassword(customPasswordField.text)
                  customPasswordField.text = ""
                }
              }
            }

            Text {
              width: parent.width
              text: "The tablet's browser also shows the username/password on the setup page. " +
                "Also shown here since you may already be past that step."
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              color: Qt.darker(root.foreground, 1.5)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }
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
