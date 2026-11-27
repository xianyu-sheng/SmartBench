function run(enabled) {
  acquire();
  release();
  if (enabled) {
    consume();
  }
}
