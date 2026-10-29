// Ask a Telly TYRC-1 remote to beep ("find me"), per Telly's spec sheet:
// write 0x01 to characteristic B002 of service 00000AA0-3C17-D293-8E48-14FE2E4DA212
// (0x02 stops it). The remote must be awake and connectable, e.g. in pairing
// mode (hold Back + Home) or already connected to this Mac.
// Build/run like ble_scan.swift (as an app bundle):  open -W ... FindMe.app --args [on|off]
// Result on remote firmware V1.0.1_20240517: the write is acknowledged but it never beeps.
import CoreBluetooth
import Foundation

let findMeService = CBUUID(string: "00000AA0-3C17-D293-8E48-14FE2E4DA212")
let findMeChar = CBUUID(string: "B002")
let command: UInt8 = CommandLine.arguments.contains("off") ? 0x02 : 0x01

final class FindMe: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var remote: CBPeripheral?

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        guard c.state == .poweredOn else { return }
        if let known = c.retrieveConnectedPeripherals(withServices: [CBUUID(string: "180A"), findMeService])
            .first(where: { $0.name == "BT38B" }) {
            print("already connected"); connect(known)
        } else {
            print("scanning for BT38B…")
            c.scanForPeripherals(withServices: nil)
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData ad: [String: Any], rssi: NSNumber) {
        let name = (ad[CBAdvertisementDataLocalNameKey] as? String) ?? p.name
        guard name == "BT38B" else { return }
        print("found BT38B, rssi \(rssi)"); c.stopScan(); connect(p)
    }

    func connect(_ p: CBPeripheral) {
        remote = p; p.delegate = self; central.connect(p)
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        print("connected"); p.discoverServices([findMeService])
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        print("connect failed: \(error.map { "\($0)" } ?? "?")"); exit(1)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let s = p.services?.first(where: { $0.uuid == findMeService }) else {
            print("find-me service not found"); exit(1)
        }
        p.discoverCharacteristics([findMeChar], for: s)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        guard let ch = s.characteristics?.first(where: { $0.uuid == findMeChar }) else {
            print("B002 not found"); exit(1)
        }
        // Some firmwares only act once the host subscribes to notifications.
        if ch.properties.contains(.notify) && !ch.isNotifying {
            p.setNotifyValue(true, for: ch)
            return
        }
        send(p, ch)
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic, error: Error?) {
        print(error.map { "subscribe failed: \($0)" } ?? "subscribed")
        send(p, ch)
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        print("notified:", (ch.value ?? Data()).map { String(format: "%02x", $0) }.joined())
    }

    func send(_ p: CBPeripheral, _ ch: CBCharacteristic) {
        let withResponse = ch.properties.contains(.write)
        p.writeValue(Data([command]), for: ch, type: withResponse ? .withResponse : .withoutResponse)
        print("sent 0x0\(command) (\(withResponse ? "with" : "without") response)")
        if !withResponse { DispatchQueue.main.asyncAfter(deadline: .now() + 1) { exit(0) } }
    }

    func peripheral(_ p: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
        print(error.map { "write failed: \($0)" } ?? "write ok")
        // Stay connected briefly in case the remote needs the link up while beeping.
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { exit(error == nil ? 0 : 1) }
    }
}

let app = FindMe()
RunLoop.main.run(until: Date().addingTimeInterval(30))
print("timed out"); exit(1)
