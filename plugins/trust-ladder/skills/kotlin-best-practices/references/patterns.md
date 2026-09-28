# Kotlin patterns

Examples for each rule in `SKILL.md`.

## Sealed variants and exhaustive `when`

```kotlin
// Don't. Contradictory states are representable.
data class CartState(val loading: Boolean, val items: List<Item>?, val error: String?)

// Do. Only real states exist.
sealed interface CartState {
    data object Loading : CartState
    data class Ready(val items: List<Item>) : CartState
    data class Failed(val reason: FailureReason) : CartState
}

fun title(state: CartState): String = when (state) {
    CartState.Loading -> "Loading"
    is CartState.Ready -> "Cart (${state.items.size})"
    is CartState.Failed -> "Cart"
}
```

No `else`: adding `CartState.Empty` breaks every `when` that must handle it.

## Value classes for ids

```kotlin
@JvmInline
value class OrderId private constructor(val value: String) {
    companion object {
        fun parse(raw: String): OrderId? = raw.takeIf { it.matches(Regex("^ord_[a-z0-9]{12}$")) }?.let(::OrderId)
    }
}

fun cancel(id: OrderId) { /* id is trusted */ }
```

## Expected failures are values

```kotlin
sealed interface Checkout {
    data class Placed(val orderId: OrderId) : Checkout
    data object CardDeclined : Checkout
    data class OutOfStock(val items: List<Item>) : Checkout
}

suspend fun checkout(cart: Cart): Checkout = when (val result = payments.charge(cart.total)) {
    is Charge.Approved -> Checkout.Placed(orders.create(cart, result))
    Charge.Declined -> Checkout.CardDeclined
}
```

## Structured concurrency with injected dispatchers

```kotlin
class CartRepository(
    private val api: CartApi,
    private val io: CoroutineDispatcher,
) {
    suspend fun items(): List<Item> = withContext(io) { api.items().map(ItemDto::toDomain) }
}
```

Tests pass `StandardTestDispatcher()`; production passes `Dispatchers.IO` from one place.

## Fakes over mocks

```kotlin
class FakeCartApi(private val items: List<ItemDto>) : CartApi {
    override suspend fun items(): List<ItemDto> = items
}

@Test
fun `ready state lists items`() = runTest {
    val repository = CartRepository(FakeCartApi(listOf(ItemDto("a", 100))), StandardTestDispatcher(testScheduler))
    assertEquals(listOf(Item("a", Money(100))), repository.items())
}
```
