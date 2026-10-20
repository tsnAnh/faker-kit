package com.example.cart.ui

class CartScreen(private val repository: CartRepository) {
    fun title(): String = when (val result = repository.items()) {
        is CartItems.Loaded -> "Cart (${result.items.size})"
        CartItems.Unavailable -> "Cart"
    }
}

sealed interface CartItems {
    data class Loaded(val items: List<String>) : CartItems
    data object Unavailable : CartItems
}

fun interface CartRepository {
    fun items(): CartItems
}
