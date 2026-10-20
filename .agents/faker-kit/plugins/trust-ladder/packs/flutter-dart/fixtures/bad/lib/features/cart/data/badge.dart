enum Badge { fresh, sale }

String label(Badge badge) {
  switch (badge) {
    case Badge.fresh:
      return 'New';
    default:
      return '';
  }
}
