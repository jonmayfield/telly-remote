// Prints every BLE advertisement seen, with name, RSSI, services and manufacturer data.
// macOS only lets apps with a Bluetooth usage description scan, so build it as an
// app bundle and launch it with `open`:
//   A=BLEScan.app; mkdir -p $A/Contents/MacOS
//   cp tools/ble_scan.Info.plist $A/Contents/Info.plist
//   swiftc -O tools/ble_scan.swift -o $A/Contents/MacOS/ble_scan && codesign -s - --force $A
//   open -W --stdout scan.txt --stderr scan.txt $A --args 30
import CoreBluetooth
import Foundation

let seconds = Double(CommandLine.arguments.dropFirst().first ?? "60") ?? 60

final class Scanner: NSObject, CBCentralManagerDelegate {
    var central: CBCentralManager!
    var seen: [UUID: String] = [:]

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        print("state:", c.state.rawValue, "(5 = powered on)")
        if c.state == .poweredOn {
            c.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData ad: [String: Any], rssi: NSNumber) {
        var parts: [String] = []
        let name = (ad[CBAdvertisementDataLocalNameKey] as? String) ?? p.name ?? "-"
        parts.append("name=\(name)")
        if let s = ad[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] { parts.append("svc=\(s.map { $0.uuidString })") }
        if let m = ad[CBAdvertisementDataManufacturerDataKey] as? Data { parts.append("mfg=\(m.map { String(format: "%02x", $0) }.joined())") }
        if let sd = ad[CBAdvertisementDataServiceDataKey] as? [CBUUID: Data] {
            parts.append("svcdata=\(sd.map { "\($0.key.uuidString):\($0.value.map { String(format: "%02x", $0) }.joined())" })")
        }
        if let tx = ad[CBAdvertisementDataTxPowerLevelKey] { parts.append("tx=\(tx)") }
        if let conn = ad[CBAdvertisementDataIsConnectable] { parts.append("conn=\(conn)") }
        let line = parts.joined(separator: " ")
        if seen[p.identifier] != line {
            seen[p.identifier] = line
            let t = String(format: "%6.1f", Date().timeIntervalSince(start))
            print("\(t) rssi=\(rssi) id=\(p.identifier.uuidString.prefix(8)) \(line)")
            fflush(stdout)
        }
    }
}

let start = Date()
let scanner = Scanner()
RunLoop.main.run(until: Date().addingTimeInterval(seconds))
