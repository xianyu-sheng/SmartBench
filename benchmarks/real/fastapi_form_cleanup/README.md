# FastAPI FormData cleanup benchmark

This case is taken from FastAPI pull request [#5465](https://github.com/fastapi/fastapi/pull/5465),
“Close FormData (uploaded files) after the request is done”. The `before` and `after`
directories contain the complete `fastapi/routing.py` file at the parent commit and
the fixing commit respectively:

- before: `ed9425ef5049910251dd2302b9dd3095cefe1b1c`
- after: `ac9f56ea5ecc738eabd9282feae4679852155669`

The rule checks that a `request.form()` event registers `push_async_callback` before
the response headers are emitted. A passing benchmark must report one finding before
the fix and none after it.
