---
title: Reading C++ compiler errors
tags: compilation error compile syntax semicolon include missing header undeclared brace parenthesis
updated: 2026-08-01
concepts: compilation
status: current
---
Compilation Error means g++ rejected the source, so no test was run. Read the first error message first: it names the line and column. Common causes are a missing semicolon, unmatched brace or parenthesis, a missing `#include <iostream>`, using a name before declaring it, and mismatched types. Fix the first error and recompile; later errors are often caused by it.
