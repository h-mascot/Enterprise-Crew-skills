# Executor Prompt Template

Use this template when an agent needs to run the council automatically.

```text
You are executing the shared `council` skill.

Task:
[USER REQUEST]

Steps:
1. Classify the topic into one domain: engineering, sales, support, product, growth, ops, strategy, or mixed.
2. Choose quick or full council based on reversibility and stakes.
3. Select the persona pack from Personas.md.
4. If this is a high-stakes or long-running council, enable the light self-healing pattern from SelfHealing.md:
   - checkpoint file
   - fallback model chain
   - proof of round completion
5. Run the council using sessions_spawn for each persona.
6. For full mode:
   - Round 1: initial positions
   - Round 2: responses and challenges
   - Round 3: final positions and convergence
7. Synthesize using OutputFormat.md.

Hard rules:
- Pick personas that create useful friction.
- Later rounds must reference specific persona points.
- Preserve disagreement where real.
- Always end with recommended path, tradeoffs, open questions, and next action.
- Do not mark the council done unless persona outputs for the required rounds actually exist.
```
