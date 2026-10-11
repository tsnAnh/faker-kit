import UIKit
import Foundation

final class CartViewModel {
    var api: CartAPI!
    var title = ""

    func load(json: Any) {
        let payload = json as! [String: Int]
        let data = try! JSONSerialization.data(withJSONObject: payload)
        print("loaded \(data.count)")
        // TODO: for now we ignore the error
        DispatchQueue.main.async {
            self.title = "Cart (\(payload["count"]!))"
        }
    }
}

protocol CartAPI {}
