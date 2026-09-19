---
title: Time Limit Exceeded - loops and complexity
tags: time limit exceeded tle infinite loop complexity slow timeout while for
updated: 2026-08-01
concepts: time-complexity loops
status: current
---
Time Limit Exceeded means the program did not finish within the limit. First look for an infinite loop: a loop condition that never becomes false, or a `while(true)` without a break. Then estimate the work: about 10^8 simple operations per second is a rough budget. Waiting for more input than provided also hangs a program.
