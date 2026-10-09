import Foundation

enum BoseProtocol {

    // Bose Sleepbuds II Bluetooth services
    static let drowsyService =
        "0000FE21-0000-1000-8000-00805F9B34FB"

    static let audioService =
        "FDC34961-888E-4A93-ABF1-B3490D8B231B"

    // Bluetooth characteristics
    static let settings =
        "5E500C28-33DB-4D0A-AFC8-C9B6DF4269BF"

    static let controlPoint =
        "E81359E0-9F35-483A-A46F-7C6D686DAA06"

    static let audioData =
        "1CBD5F8F-6C18-4309-9354-703E7D42A74E"

    static let sounds =
        "12A3D434-C7DE-4760-88C0-4F743EA55E98"

    static let batteryLevel =
        "00002A19-0000-1000-8000-00805F9B34FB"

    // Phone-free mode commands
    static let phoneFreeEnable: [UInt8] =
        [0x01, 0x00, 0x00, 0x00]

    static let phoneFreeDisable: [UInt8] =
        [0x00, 0x00, 0x00, 0x00]

    // Build the 12-byte playback command
    static func audioFrame(
        playing: Bool,
        volume: UInt8,
        soundID: UInt16,
        timeout: UInt16 = 0
    ) -> Data {

        var bytes = [UInt8](repeating: 0, count: 12)

        bytes[0] = playing ? 1 : 0
        bytes[1] = volume

        bytes[2] = UInt8(soundID & 0xFF)
        bytes[3] = UInt8((soundID >> 8) & 0xFF)

        bytes[4] = UInt8(timeout & 0xFF)
        bytes[5] = UInt8((timeout >> 8) & 0xFF)

        // Remaining time starts at zero, matching the Python controller.
        bytes[6] = 0
        bytes[7] = 0

        return Data(bytes)
    }
}
