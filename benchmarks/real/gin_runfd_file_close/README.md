# Gin RunFd file cleanup benchmark

This case comes from Gin pull request [#4422](https://github.com/gin-gonic/gin/pull/4422),
which closed the `os.File` wrapper created by `RunFd` before handing it to a listener.

- before: `acc55e049e33b401e810dbd8c0d6dcb6b3ba2b05`
- after: `c3d5a28ed6d3849da820195b6774d212bcc038a9`

The snapshots contain the complete historical `gin.go`. The invariant checks the
language-neutral acquisition, cleanup-registration, and listener-creation sequence.
