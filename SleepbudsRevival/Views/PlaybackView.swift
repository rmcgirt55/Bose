import SwiftUI
import CoreBluetooth

struct PlaybackView: View {

    @ObservedObject var bluetooth: SleepbudsBluetoothManager

    @State private var selectedSound: UInt16 = 31567
    @State private var volume: Double = 35
    @State private var timerMinutes: Int = 0

    private var connectedBuds: [CBPeripheral] {
        bluetooth.discoveredDevices.values.filter {
            bluetooth.readyDevices.contains($0.identifier)
        }
    }

    var body: some View {
        Form {
            Section("Ready Earbuds") {
                Text("\(connectedBuds.count) ready")
                    .foregroundStyle(.secondary)
            }

            Section("Sleep Sound") {
                Picker("Sound", selection: $selectedSound) {
                    ForEach(SleepSound.library) { sound in
                        Text(sound.name)
                            .tag(sound.id)
                    }
                }
            }

            Section("Volume") {
                HStack {
                    Image(systemName: "speaker.fill")

                    Slider(
                        value: $volume,
                        in: 0...100,
                        step: 1
                    )

                    Text("\(Int(volume))")
                        .monospacedDigit()
                }

                Button("Apply Volume") {
                    for bud in connectedBuds {
                        bluetooth.setVolume(
                            on: bud,
                            volume: UInt8(volume)
                        )
                    }
                }
                .disabled(connectedBuds.isEmpty)
            }

            Section("Sleep Timer") {
                Picker("Stop after", selection: $timerMinutes) {
                    Text("Never").tag(0)
                    Text("15 minutes").tag(15)
                    Text("30 minutes").tag(30)
                    Text("60 minutes").tag(60)
                }
            }

            Section("Playback") {
                Button {
                    let seconds = UInt16(timerMinutes * 60)

                    for bud in connectedBuds {
                        bluetooth.playSound(
                            on: bud,
                            soundID: selectedSound,
                            volume: UInt8(volume),
                            timeout: seconds
                        )
                    }
                } label: {
                    Label("Play on Connected Sleepbuds",
                          systemImage: "play.fill")
                }
                .disabled(connectedBuds.isEmpty)

                Button(role: .destructive) {
                    for bud in connectedBuds {
                        bluetooth.stopSound(on: bud)
                    }
                } label: {
                    Label("Stop Playback",
                          systemImage: "stop.fill")
                }
                .disabled(connectedBuds.isEmpty)
            }
        }
        .navigationTitle("Sleep Sounds")
    }
}
