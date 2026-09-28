# Swift patterns

Examples for each rule in `SKILL.md`.

## Enums for variants and exhaustive switch

```swift
// Don't. Contradictory states are representable.
struct CartState { var isLoading: Bool; var items: [Item]?; var error: String? }

// Do. Only real states exist.
enum CartState: Equatable, Sendable {
    case loading
    case ready([Item])
    case failed(FailureReason)
}

func title(for state: CartState) -> String {
    switch state {
    case .loading: "Loading"
    case .ready(let items): "Cart (\(items.count))"
    case .failed: "Cart"
    }
}
```

No `default`: adding `.empty` breaks every switch that must handle it.

## Tagged ids

```swift
struct OrderID: Hashable, Sendable {
    let rawValue: String

    init?(_ raw: String) {
        guard raw.hasPrefix("ord_"), raw.count == 16 else { return nil }
        rawValue = raw
    }
}
```

## Typed errors

```swift
enum CheckoutError: Error {
    case cardDeclined
    case outOfStock([Item])
}

func checkout(_ cart: Cart) async throws(CheckoutError) -> OrderID {
    let charge = await payments.charge(cart.total)
    guard charge.approved else { throw .cardDeclined }
    return try await orders.create(cart, charge)
}
```

## Concurrency without Dispatch

```swift
@MainActor
final class CartViewModel {
    private(set) var state: CartState = .loading
    private let repository: CartRepository

    init(repository: CartRepository) { self.repository = repository }

    func load() async {
        do { state = .ready(try await repository.items()) }
        catch { state = .failed(.network) }
    }
}

actor ImageCache {
    private var images: [URL: Data] = [:]
    func image(for url: URL) -> Data? { images[url] }
    func store(_ data: Data, for url: URL) { images[url] = data }
}
```

## In-memory implementations over mocks

```swift
struct InMemoryCartRepository: CartRepository {
    var stored: [Item]
    func items() async throws -> [Item] { stored }
}

@Test func readyStateListsItems() async {
    let model = await CartViewModel(repository: InMemoryCartRepository(stored: [Item(id: "a")]))
    await model.load()
    #expect(await model.state == .ready([Item(id: "a")]))
}
```
