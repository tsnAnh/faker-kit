package com.example.cart

public expect class CartFile(path: String) {
    public fun write(text: String)
}

public class CartStore(private val file: CartFile) {
    public fun save(items: List<String>) {
        file.write(items.joinToString("\n"))
    }
}
