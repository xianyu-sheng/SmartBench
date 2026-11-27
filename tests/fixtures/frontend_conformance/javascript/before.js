function run(enabled) {
  acquire();
  if (enabled) {
    consume();
  }
}
