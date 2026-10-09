import SwiftUI
import CoreBluetooth

struct ContentView: View {

    @StateObject private var bluetooth =
        SleepbudsBluetoothManager()

    var body: some View {
        NavigationStack {
            List {
                Section("Bluetooth") {
                    HStack {
                        Image(systemName: "bluetooth")
                            .foregroundStyle(.blue)

                        Text("Bluetooth")

                        Spacer()

                        Text(bluetooth.bluetoothReady
                             ? "Ready" : "Unavailable")
                            .foregroundStyle(
                                bluetooth.bluetoothReady
                                ? .green : .secondary
                            )
                    }

                    Button("Scan for Sleepbuds") {
                        bluetooth.startScanning()
                    }
                    .disabled(!bluetooth.bluetoothReady)
                }

                Section("Playback") {
                    NavigationLink("Sleep Sounds") {
                        PlaybackView(bluetooth: bluetooth)
                    }
                }

                Section("Discovered Sleepbuds") {
                    if bluetooth.discoveredDevices.isEmpty {
                        Text("No earbuds discovered yet")
                            .foregroundStyle(.secondary)
                    }

                    ForEach(
                        bluetooth.discoveredDevices.values
                            .sorted {
                                $0.identifier.uuidString <
                                $1.identifier.uuidString
                            },
                        id: \.identifier
                    ) { peripheral in

                        HStack {
                            Image(systemName: "earbuds")

                            VStack(alignment: .leading) {
                                Text(
                                    peripheral.name ??
                                    "Bose Sleepbud"
                                )
                                .font(.headline)

                                if let battery =
                                    bluetooth.batteryLevels[
                                        peripheral.identifier
                                    ] {
                                    Label(
                                        "\(battery)%",
                                        systemImage: "battery.100percent"
                                    )
                                    .font(.caption)
                                    .foregroundStyle(.green)
                                }

                                Text(
                                    peripheral.identifier
                                        .uuidString
                                )
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            }

                            Spacer()

                            if bluetooth.connectedDevices
                                .contains(peripheral.identifier) {

                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(.green)

                            } else {
                                Button("Connect") {
                                    bluetooth.connect(to: peripheral)
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Sleepbuds Revival")
        }
    }
}
