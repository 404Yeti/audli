# V0.1 plan
1. Strict exercise, evaluation and learner schemas.
2. Deterministic adaptation with configurable 70–85% learning edge.
3. Transactional persistence, audit events and transcript gates.
4. OpenAI structured generation/evaluation, speech and transcription.
5. Mobile listen/record/review/evaluate/continue interface.
6. Boundary, privacy, uncertainty, schema and evaluator fixture tests; setup documentation.

Risks: microphone needs localhost/HTTPS; browser recording formats vary; STT confidence is heuristic; LLM judgments need teacher validation; generated duration is approximate. Provider failures leave attempts retryable. Serialize local operations to prevent double adaptation. Demo mode validates plumbing only.

Scope: typed name/goal onboarding first; verbal goal extraction deferred. First three successful attempts establish a running-mean baseline, following one-variable adaptation. Natural-speed estimates remain unknown. SQLite is sufficient to validate the hypothesis.
