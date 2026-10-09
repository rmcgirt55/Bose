import Foundation

struct SleepSound: Identifiable, Hashable {
    let id: UInt16
    let name: String

    static let library: [SleepSound] = [
        .init(id: 10972, name: "Swell"),
        .init(id: 58776, name: "Tranquility"),
        .init(id: 31567, name: "Warm Static"),
        .init(id: 25575, name: "Celesta"),
        .init(id: 32302, name: "Staccato Bells"),
        .init(id: 28072, name: "Gentle Bells"),
        .init(id: 20000, name: "Rinse"),
        .init(id: 20005, name: "Starboard"),
        .init(id: 20017, name: "Ode"),
        .init(id: 20018, name: "Wanderlust"),
        .init(id: 20019, name: "Boardwalk"),
        .init(id: 20028, name: "Windowseat")
    ]
}
