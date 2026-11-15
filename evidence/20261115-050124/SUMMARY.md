# Evidence Pack

Generated 20261115-050124. Re-run with `make proof`.

Every number here is read from the Bifrost gateway (response bodies,
x-bifrost-* headers, /api/logs, /api/governance quota), not from the
agent's own output.

## Disclosure

Local inference is genuinely free, so Bifrost meters it at $0 and no
budget could ever trip. Scenario 3 uses a SYNTHETIC pricing override
scoped to a dedicated virtual key to make the limit reachable. The
price is fake; the 402, the block before the provider, and the
degradation ladder are real.

Scenario 1 gives every run a fresh cache scope so repeats stay
independent; cross-run reuse is measured separately in 1b. Bifrost
reports the original usage on a cache hit, so hit tokens are counted
as served from cache rather than provider-processed.

Scenario 5 runs its non-interactive half here. The interactive half,
a human seeing the prompt and answering it, must be run at a terminal.

## Results

| Scenario | Verdict | Seconds |
| --- | --- | --- |
| 0-security-tests - Security unit tests (no gateway needed) | **PASS** | 5.7 |
| 1-token-efficiency - Naive baseline vs all levers, 5 repeats at T=0.7, median tokens per completed task | **PASS** | 3270.0 |
| 1b-cache-rerun - Identical re-run served from the gateway cache | **PASS** | 9.6 |
| 2-failover - Provider outage, failover with attribution | **PASS** | 10.2 |
| 3-budget - 402 before the provider call (synthetic pricing, disclosed) | **PASS** | 9.3 |
| 4-privacy - Confidential repo under an adversarial prompt | **PASS** | 28.7 |
| 5-approval - Approval gate versus prompt injection | **PASS** | 13.8 |

## Notes

- INCONCLUSIVE is a real outcome, not a soft failure. Scenario 5
  reports it when the model never attempted a gated action, because
  a non-attempt tests nothing.
- Token counts are gateway-reported. Both benchmark arms are the
  same binary; only the lever flags differ.
