package com.example.cart

import java.io.File

fun readCart(path: String): String = File(path).readText()
