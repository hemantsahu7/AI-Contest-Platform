---
title: Runtime errors - crashes and non-zero exit
tags: runtime error crash segfault exit code division by zero uninitialized array out of bounds return
updated: 2026-08-01
concepts: runtime-errors memory-and-indexing
status: current
---
Runtime Error means the program started but exited abnormally. Typical causes: array index out of bounds, division by zero, reading uninitialized variables, unbounded recursion, and `main` returning a non-zero value. Make sure `main` ends with `return 0;` and that indexes stay inside the array.
