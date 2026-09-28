import '../data/cart_api.dart';

class CartPage {
  final CartApi api;

  CartPage(this.api);

  title(dynamic payload) {
    // TODO: for now read the count from the raw payload
    try {
      api.items();
      print('loading');
      return 'Cart (${payload.count})';
    } catch (e) {
      return 'Cart';
    }
  }
}
