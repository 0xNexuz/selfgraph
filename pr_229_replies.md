Replies for the two review threads on the connect-rollback PR:

---

On the Python thread:

Done. _release_store(key) now holds the decrement and the final-cache close,
and both the connect failure path and close() call it. The noqa is gone too.

---

On the TypeScript thread:

Done. KuzuStore.releaseStore(key) sits beside closeCachedDatabase and both the
Lazy factory catch and close() call it.
