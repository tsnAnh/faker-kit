import Foundation
import os

@MainActor
final class CartViewModel {
    private let logger = Logger(subsystem: "com.example.cart", category: "cart")
    private let api: CartAPI
    private(set) var title = ""

    init(api: CartAPI) {
        self.api = api
    }

    func load() async {
        do {
            let items = try await api.items()
            title = "Cart (\(items.count))"
        } catch {
            logger.error("Could not load the cart: \(error.localizedDescription)")
            title = "Cart"
        }
    }
}

protocol CartAPI: Sendable {
    func items() async throws -> [String]
}
