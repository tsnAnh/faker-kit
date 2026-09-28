package com.example.cart.ui

import android.util.Log
import java.util.*

class CartScreen(private val repository: CartRepository?) {
    // TODO: for now we retry twice because the API is flaky
    fun title(): String {
        try {
            val items = repository!!.items()
            return "Cart (${items.size})"
        } catch (e: Exception) {
            Log.d("Cart", "failed")
            return "Cart"
        }
    }
}

interface CartRepository {
    fun items(): List<String>
}

sealed interface Badge {
    data object New : Badge
    data object Sale : Badge
}

fun label(badge: Badge): String = when (badge) {
    Badge.New -> "New"
    else -> ""
}
