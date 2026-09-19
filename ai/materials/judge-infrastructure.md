---
title: Judge errors are not your mistake
tags: judge error infrastructure incident container docker retry system failure
updated: 2026-08-01
concepts: judge-behavior
status: current
---
A JUDGE_ERROR / INFRASTRUCTURE_ERROR verdict means the platform failed while judging (for example a container could not start). It says nothing about the correctness of your code and is never counted as a Wrong Answer. Resubmit the same code; if it repeats, tell an instructor. Instructors can tell infrastructure failures from code errors by checking that the submission has a judge incident and no test results.
