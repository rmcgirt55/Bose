import Foundation
import CoreBluetooth
import Combine

final class SleepbudsBluetoothManager: NSObject, ObservableObject {

    @Published var bluetoothReady = false
    @Published var discoveredDevices: [UUID: CBPeripheral] = [:]
    @Published var connectedDevices: Set<UUID> = []
    @Published var readyDevices: Set<UUID> = []
    @Published var batteryLevels: [UUID: Int] = [:]

    private var centralManager: CBCentralManager!

    private var pendingVolume: [UUID: UInt8] = [:]
    private var pendingServiceDiscovery: [UUID: Int] = [:]
    private var pendingNotifications: [UUID: Set<CBUUID>] = [:]
    private var pendingHandshakeWrites: Set<UUID> = []

    override init() {
        super.init()
        centralManager = CBCentralManager(
            delegate: self,
            queue: nil
        )
    }

    func startScanning() {
        guard centralManager.state == .poweredOn else {
            print("Bluetooth is not ready")
            return
        }

        print("Scanning for Bose Sleepbuds II...")

        centralManager.scanForPeripherals(
            withServices: [
                CBUUID(string: BoseProtocol.drowsyService)
            ],
            options: [
                CBCentralManagerScanOptionAllowDuplicatesKey: false
            ]
        )
    }

    func stopScanning() {
        centralManager.stopScan()
    }

    func connect(to peripheral: CBPeripheral) {
        peripheral.delegate = self
        centralManager.connect(peripheral, options: nil)
    }
}


extension SleepbudsBluetoothManager {

    private func audioCharacteristic(
        for peripheral: CBPeripheral
    ) -> CBCharacteristic? {
        peripheral.services?
            .flatMap { $0.characteristics ?? [] }
            .first {
                $0.uuid == CBUUID(string: BoseProtocol.audioData)
            }
    }

    func playSound(
        on peripheral: CBPeripheral,
        soundID: UInt16,
        volume: UInt8 = 35,
        timeout: UInt16 = 0
    ) {
        guard peripheral.state == .connected,
              let characteristic = audioCharacteristic(for: peripheral)
        else {
            print("Earbud is not ready for playback")
            return
        }

        let frame = BoseProtocol.audioFrame(
            playing: true,
            volume: volume,
            soundID: soundID,
            timeout: timeout
        )

        let writeType: CBCharacteristicWriteType =
            characteristic.properties.contains(.write)
            ? .withResponse : .withoutResponse

        peripheral.writeValue(
            frame,
            for: characteristic,
            type: writeType
        )
    }

    func stopSound(on peripheral: CBPeripheral) {
        guard peripheral.state == .connected,
              let characteristic = audioCharacteristic(for: peripheral)
        else {
            print("Earbud is not ready")
            return
        }

        let frame = BoseProtocol.audioFrame(
            playing: false,
            volume: 0,
            soundID: 65440
        )

        let writeType: CBCharacteristicWriteType =
            characteristic.properties.contains(.write)
            ? .withResponse : .withoutResponse

        peripheral.writeValue(
            frame,
            for: characteristic,
            type: writeType
        )
    }

    func setVolume(
        on peripheral: CBPeripheral,
        volume: UInt8
    ) {
        guard peripheral.state == .connected,
              let characteristic = audioCharacteristic(for: peripheral)
        else {
            print("Earbud is not ready")
            return
        }

        pendingVolume[peripheral.identifier] = volume
        peripheral.readValue(for: characteristic)

        print("Reading current audio state for volume change")
    }
}


extension SleepbudsBluetoothManager {



    private func performBoseHandshake(on peripheral: CBPeripheral) {
        guard let services = peripheral.services else { return }

        let characteristics = services.flatMap {
            $0.characteristics ?? []
        }

        let targets: Set<CBUUID> = [
            CBUUID(string: BoseProtocol.settings),
            CBUUID(string: BoseProtocol.audioData),
            CBUUID(string: BoseProtocol.controlPoint)
        ]

        let notifiable = characteristics.filter {
            targets.contains($0.uuid) &&
            ($0.properties.contains(.notify) ||
             $0.properties.contains(.indicate))
        }

        pendingNotifications[peripheral.identifier] =
            Set(notifiable.map { $0.uuid })

        if notifiable.isEmpty {
            finishBoseHandshake(on: peripheral)
            return
        }

        for characteristic in notifiable {
            peripheral.setNotifyValue(true, for: characteristic)
        }
    }

    private func finishBoseHandshake(on peripheral: CBPeripheral) {
        guard let services = peripheral.services else { return }

        let characteristics = services.flatMap {
            $0.characteristics ?? []
        }

        if let control = characteristics.first(where: {
            $0.uuid == CBUUID(string: BoseProtocol.controlPoint)
        }), control.properties.contains(.write) {
            pendingHandshakeWrites.insert(peripheral.identifier)

            peripheral.writeValue(
                Data([0x01, 0x03]),
                for: control,
                type: .withResponse
            )
            print("Bose device query requested")
        }

        let readable: Set<CBUUID> = [
            CBUUID(string: BoseProtocol.settings),
            CBUUID(string: BoseProtocol.sounds),
            CBUUID(string: BoseProtocol.audioData)
        ]

        for characteristic in characteristics {
            if readable.contains(characteristic.uuid) &&
               characteristic.properties.contains(.read) {
                peripheral.readValue(for: characteristic)
            }
        }
    }

}

extension SleepbudsBluetoothManager: CBCentralManagerDelegate {

    func centralManagerDidUpdateState(
        _ central: CBCentralManager
    ) {
        bluetoothReady = central.state == .poweredOn

        if bluetoothReady {
            startScanning()
        }
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        discoveredDevices[peripheral.identifier] = peripheral

        print(
            "Found Sleepbud: \(peripheral.name ?? "Unnamed")"
        )
    }

    func centralManager(
        _ central: CBCentralManager,
        didConnect peripheral: CBPeripheral
    ) {
        connectedDevices.insert(peripheral.identifier)
        readyDevices.remove(peripheral.identifier)

        print("Connected: \(peripheral.identifier)")

        peripheral.discoverServices(nil)
    }

    func centralManager(
        _ central: CBCentralManager,
        didFailToConnect peripheral: CBPeripheral,
        error: Error?
    ) {
        print("Connection failed: \(error?.localizedDescription ?? "Unknown error")")
    }

    func centralManager(
        _ central: CBCentralManager,
        didDisconnectPeripheral peripheral: CBPeripheral,
        error: Error?
    ) {
        connectedDevices.remove(peripheral.identifier)
        readyDevices.remove(peripheral.identifier)
        batteryLevels.removeValue(forKey: peripheral.identifier)
        pendingVolume.removeValue(forKey: peripheral.identifier)
        pendingServiceDiscovery.removeValue(forKey: peripheral.identifier)
        pendingNotifications.removeValue(forKey: peripheral.identifier)
        pendingHandshakeWrites.remove(peripheral.identifier)

        print("Disconnected: \(peripheral.identifier)")
    }
}

extension SleepbudsBluetoothManager: CBPeripheralDelegate {

    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateNotificationStateFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        guard var pending = pendingNotifications[
            peripheral.identifier
        ] else { return }

        guard pending.contains(characteristic.uuid) else {
            return
        }

        if let error = error {
            print("Notification setup failed: \(error.localizedDescription)")
            pendingNotifications.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        guard characteristic.isNotifying else {
            print("Notification subscription was not enabled")
            pendingNotifications.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        pending.remove(characteristic.uuid)

        if pending.isEmpty {
            pendingNotifications.removeValue(
                forKey: peripheral.identifier
            )
            finishBoseHandshake(on: peripheral)
        } else {
            pendingNotifications[peripheral.identifier] = pending
        }
    }



    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        if characteristic.uuid ==
            CBUUID(string: BoseProtocol.controlPoint) {

            guard error == nil,
                  let data = characteristic.value,
                  !data.isEmpty else {
                print("Bose Control Point response unavailable")
                return
            }

            print("Bose Control Point response: \(data as NSData)")

            if pendingHandshakeWrites.contains(peripheral.identifier) {
                print("Bose response received while write is pending")
            }

            return
        }

        if characteristic.uuid ==
            CBUUID(string: BoseProtocol.batteryLevel) {

            if error == nil,
               let battery = characteristic.value?.first {
                batteryLevels[peripheral.identifier] = Int(battery)
                print("Battery: \(battery)%")
            }
            return
        }

        guard characteristic.uuid ==
                CBUUID(string: BoseProtocol.audioData),
              let volume = pendingVolume.removeValue(
                forKey: peripheral.identifier
              )
        else {
            return
        }

        guard error == nil,
              let data = characteristic.value,
              data.count >= 12
        else {
            print("Could not read audio state; volume unchanged")
            return
        }

        var frame = Data(data.prefix(12))
        frame[1] = volume

        let writeType: CBCharacteristicWriteType =
            characteristic.properties.contains(.write)
            ? .withResponse : .withoutResponse

        guard characteristic.properties.contains(.write) ||
              characteristic.properties.contains(.writeWithoutResponse)
        else {
            print("Audio characteristic is not writable")
            return
        }

        peripheral.writeValue(
            frame,
            for: characteristic,
            type: writeType
        )

        print("Volume update requested: \(volume)")
    }

    func peripheral(
        _ peripheral: CBPeripheral,
        didWriteValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        if characteristic.uuid ==
            CBUUID(string: BoseProtocol.controlPoint),
           pendingHandshakeWrites.remove(peripheral.identifier) != nil {

            if let error = error {
                print("Bose initialization write failed: \(error.localizedDescription)")
                readyDevices.remove(peripheral.identifier)
            } else if peripheral.state == .connected {
                print("Bose initialization write acknowledged; awaiting response")
            }
            return
        }

        if let error = error {
            print("Bluetooth write failed: \(error.localizedDescription)")
        } else {
            print("Bluetooth write acknowledged")
        }
    }


    func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverServices error: Error?
    ) {
        if let error = error {
            print("Service discovery failed: \(error.localizedDescription)")
            pendingServiceDiscovery.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        guard let services = peripheral.services, !services.isEmpty else {
            print("No Bluetooth services discovered")
            pendingServiceDiscovery.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        pendingServiceDiscovery[peripheral.identifier] =
            services.count

        for service in services {
            print("Service: \(service.uuid)")

            peripheral.discoverCharacteristics(
                nil,
                for: service
            )
        }
    }

    func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverCharacteristicsFor service: CBService,
        error: Error?
    ) {
        if let error = error {
            print("Characteristic discovery failed: \(error.localizedDescription)")
            pendingServiceDiscovery.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        guard let characteristics = service.characteristics else {
            print("No characteristics returned for service \(service.uuid)")
            pendingServiceDiscovery.removeValue(
                forKey: peripheral.identifier
            )
            return
        }

        for characteristic in characteristics {
            print("Characteristic: \(characteristic.uuid)")

            if characteristic.uuid ==
                CBUUID(string: BoseProtocol.batteryLevel) {

                if characteristic.properties.contains(.read) {
                    peripheral.readValue(for: characteristic)
                }

                if characteristic.properties.contains(.notify) {
                    peripheral.setNotifyValue(
                        true,
                        for: characteristic
                    )
                }
            }
        }

        if let remaining = pendingServiceDiscovery[
            peripheral.identifier
        ] {
            let updated = remaining - 1
            pendingServiceDiscovery[peripheral.identifier] = updated

            if updated == 0 {
                pendingServiceDiscovery.removeValue(
                    forKey: peripheral.identifier
                )
                performBoseHandshake(on: peripheral)
            }
        }
    }
}
