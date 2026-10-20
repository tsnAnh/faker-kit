package com.example.cart

import android.util.Log
import java.io.File

class CartStore(private val path: String) {
    fun save(items: List<String>) {
        File(path).writeText(items.joinToString("\n"))
        Log.d("CartStore", "saved")
    }
}
