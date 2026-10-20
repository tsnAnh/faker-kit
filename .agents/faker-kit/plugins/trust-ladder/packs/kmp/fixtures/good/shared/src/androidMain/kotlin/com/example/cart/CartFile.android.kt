package com.example.cart

import java.io.File

public actual class CartFile actual constructor(private val path: String) {
    public actual fun write(text: String) {
        File(path).writeText(text)
    }
}
