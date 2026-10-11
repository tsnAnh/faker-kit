import 'package:fixture/features/cart/data/cart_api.dart';

class CartPage {
  const CartPage(this.api);

  final CartApi api;

  Future<String> title() async {
    final items = await api.items();
    return 'Cart (${items.length})';
  }
}
