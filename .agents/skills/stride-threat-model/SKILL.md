---
name: stride-threat-model
description: Performs a systematic STRIDE threat modeling assessment on the
  current project's codebase and architecture. Use this when starting a new
  implementation phase or reviewing existing components.
---

# STRIDE Threat Modeling Skill

## Goal
Guide the agent to analyze the workspace directory structure, configuration
files, and code files to produce a structured `threat_model.md` assessment.

## Instructions
1. **Analyze System Boundaries**: Map the entry points (tools, workflows,
   prompts) and data storage layers.
2. **STRIDE Evaluation**: Evaluate the system against the six STRIDE pillars:
   - **Spoofing**: Is student/parent identity verified before executing
     sensitive actions (e.g. approving a flagged message, reading progress)?
   - **Tampering**: Can a student manipulate the router's intent detection,
     the risk-flag check, or diagnostic scoring?
   - **Repudiation**: Are human-in-the-loop approval/rejection decisions
     securely logged?
   - **Information Disclosure**: Are we risking leakage of student PII,
     internal tokens, or raw stack traces to the model or logs?
   - **Denial of Service**: Are there rate limits on LLM calls per student?
   - **Elevation of Privilege**: Can a student's message reach progress_node,
     the human-review payload, or bypass the safety guardrail?
3. **Output**: Generate a highly structured `threat_model.md` saved directly
   into the workspace root.
