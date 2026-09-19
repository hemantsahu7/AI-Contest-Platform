---
title: Integer overflow and choosing the right type
tags: overflow int long long sum product add multiply large numbers arithmetic sum-of-two product 64-bit
updated: 2026-08-01
concepts: integer-overflow integer-types
status: current
---
A 32-bit `int` holds values up to about 2.1 billion. Adding or multiplying two large inputs can exceed that and silently wrap to a wrong number. When the statement allows values near 10^9 or larger, or asks for a sum or product, use `long long`. A program that passes the small public samples but fails on hidden tests is a classic sign of overflow.
