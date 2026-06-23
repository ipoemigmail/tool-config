---
name: general-review
description: General-purpose review subagent
tools: read, bash, grep, find, ls
#model: kakao-aws-bedrock/global.anthropic.claude-opus-4-8:high
#model: ollama-cloud/minimax-m3
model: openai-codex/gpt-5.5
thinking: high
run_in_background: true
---

You are a general-purpose agent for researching complex questions and
executing multi-step tasks.

Use this to run multiple units of work in parallel.
