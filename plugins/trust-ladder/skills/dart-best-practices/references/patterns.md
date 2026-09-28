# Dart patterns

Examples for each rule in `SKILL.md`. The snippet is one library, `lib/cart.dart` in a package named `app`, and passes `dart analyze --fatal-infos` with the flutter-dart lint pack's configuration.

```dart
import 'package:logging/logging.dart';

final Logger _log = Logger('cart');

extension type const OrderId._(String value) {
  static OrderId? parse(String raw) =>
      RegExp(r'^ord_[a-z0-9]{12}$').hasMatch(raw) ? OrderId._(raw) : null;
}

enum Failure { network, signedOut }

sealed class CartState {
  const CartState();
}

final class Loading extends CartState {
  const Loading();
}

final class Ready extends CartState {
  const Ready(this.items);

  final List<String> items;
}

final class Failed extends CartState {
  const Failed(this.reason);

  final Failure reason;
}

String title(CartState state) => switch (state) {
      Loading() => 'Loading',
      Ready(:final items) => 'Cart (${items.length})',
      Failed(:final reason) => _failed(reason),
    };

String _failed(Failure reason) {
  _log.info('cart unavailable: ${reason.name}');
  return 'Cart';
}

Ready parseCart(Object? json) => switch (json) {
      {'items': final List<Object?> items}
          when items.every((item) => item is String) =>
        Ready(List.unmodifiable(items.cast<String>())),
      _ => throw const FormatException(
          "cart JSON must be {'items': [String, ...]}"),
    };
```
