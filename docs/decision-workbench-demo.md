# Decision Workbench Demo

PRD Pal's open-source showcase is an evidence-grounded product decision loop:

```text
Feedback evidence -> AI draft -> human approval -> PRD quality gate -> Feishu delivery
```

## Three-minute local walkthrough

1. Start the backend and frontend as described in `quick-start.md`.
2. Seed the isolated, idempotent demo data (no API key required):

   ```bash
   prd-pal demo seed
   ```

3. Open `/workbench?product_id=demo-mobile-commerce`.
4. Inspect confirmed evidence, the generated insight and opportunity, then follow the trace from the delivery view.

The demo data uses the `demo-mobile-commerce` product only. Re-running the seed command updates that dedicated source and does not delete user-owned data.

## Production boundary

The demo's draft generator is deterministic so contributors can reproduce the flow without model credentials. Production callers should replace it with a configured model-backed agent while retaining the same guardrails: only confirmed evidence can be synthesized; generated opportunities remain `proposed`; an owner must approve an opportunity before a formal PRD can be created; and a PRD must pass or be explicitly waived before delivery.

For real Feishu credentials and webhook verification, see `callback-config.md`.
