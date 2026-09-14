# RustChain #398 Step 2 — Mock Signature Mode reproduction

Submission target: `Scottcjn/rustchain-bounties#398`, **Step 2 — Reproduce a Known Fix (15 RTC)**.

**Reporter:** @aziemels-byte  
**RTC wallet:** `RTCcd14e195e2c583ea27c2f6fb21a0717c0bbe4212`  
**RustChain commit reviewed:** `aa584b344a766f6c0f8613ba7198d1cc7ffbae35`

## Chosen historical vulnerability

**Mock Signature Mode** from the fixed BuilderFred audit set.

The historical RustChain node kept a test-only compatibility path for signed-header ingest. In the deprecated implementation, the relevant logic set `accepted = True` when `TESTNET_ALLOW_MOCK_SIG` was enabled and the submitted signature matched an obviously synthetic shape such as an all-zero 128-hex-character value. That shortcut was useful for local/testnet workflows, but it is unsafe if production can start with the flag enabled: the node can accept a signature-shaped placeholder without performing Ed25519 verification.

The dangerous condition was therefore not merely “mock signatures exist in test code.” The actual security boundary was configuration: **if production could boot while `TESTNET_ALLOW_MOCK_SIG=True`, a caller could reach a code path where a fake signature is accepted as valid.**

## Pre-fix attack model

An attacker does not need to break Ed25519. They only need a production deployment accidentally configured with mock-signature mode enabled.

A minimal pre-fix sequence is:

1. production node starts with `TESTNET_ALLOW_MOCK_SIG=True`;
2. attacker prepares a syntactically valid request for the signed-header path;
3. instead of generating a real Ed25519 signature, attacker submits a 128-character all-zero mock signature;
4. the historical conditional treats it as accepted because the mock-signature flag is on;
5. normal cryptographic verification is skipped for that request.

This is a configuration-triggered authentication bypass. Its severity depends on which signed action consumes the accepted result, but the important point is that the trust decision can become independent of key ownership.

## Current fix

The current node defines an explicit production runtime guard in `node/rustchain_v2_integrated_v2.2.1_rip200.py`:

- test/dev runtimes are enumerated in `_MOCK_SIG_ALLOWED_ENVS`;
- `enforce_mock_signature_runtime_guard()` reads `RC_RUNTIME_ENV` / `RUSTCHAIN_ENV`;
- when `TESTNET_ALLOW_MOCK_SIG` is true outside the allowed test/dev environments, it raises `RuntimeError`.

The protection is wired into the production startup path in `node/wsgi.py`. Immediately after importing the integrated node module, WSGI calls:

```python
rustchain_main.enforce_mock_signature_runtime_guard()
rustchain_main.enforce_hardware_binding_runtime_guard()
```

Only after both guards pass does startup expose the Flask app and call `init_db()`.

That ordering is security-critical. The fix does not merely log a warning after startup; it prevents a production worker from becoming operational under the unsafe configuration.

The repository also contains `node/tests/test_mock_signature_guard.py`, which covers the intended invariant:

- production + mock signatures enabled => `RuntimeError`;
- test runtime + mock signatures enabled => allowed;
- WSGI executes the guard before database initialization.

## Local reproduction

I reproduced both the historical acceptance predicate and the current startup invariant in an isolated local harness. No production node or live network was touched.

The checks were:

1. feed the historical mock predicate a 128-character all-zero signature with the mock flag enabled;
2. confirm that the historical predicate returns accepted;
3. run the current guard with `TESTNET_ALLOW_MOCK_SIG=True` and runtime `production`;
4. confirm it raises and blocks startup;
5. run the same guard with runtime `test` and confirm it is allowed;
6. reproduce the WSGI ordering with an `init_db()` stub that deliberately raises if reached, then verify the mock-signature guard raises first.

Result:

```text
[PASS] pre_fix_mock_signature_is_accepted_when_flag_true
[PASS] current_guard_blocks_mock_signatures_in_production
[PASS] current_guard_allows_mock_signatures_in_test_runtime
[PASS] wsgi_blocks_startup_before_init_db

Summary: 4/4 PASS
```

## Why the fix is sufficient for the stated bug

For the specific vulnerability, the fix is strong because it moves the safety property to process startup. A production deployment cannot quietly continue with mock-signature mode enabled; it fails before initialization completes. That is better than relying on operators to notice a warning or expecting every downstream signature call site to repeat an environment check.

The allowlist of environments is explicit rather than using a broad truthy “non-production” flag. An unset environment defaults to `production`, which is the safer direction. The WSGI regression test also protects the ordering, so a future refactor that moves `init_db()` or app exposure ahead of the guard should be caught.

## Residual considerations

The guard is sufficient only for entry points that actually start through the guarded production bootstrap. If operators maintain alternate custom launchers that import the node module and expose `app` without calling `enforce_mock_signature_runtime_guard()`, they could bypass the startup invariant. For that reason, the safest long-term design would be to keep the WSGI guard and additionally ensure deployment tooling has one canonical startup path, or invoke the guard from a central application factory that every supported production launcher must use.

That is a defense-in-depth recommendation, not evidence that the current official WSGI path is bypassable.

## Conclusion

Before the fix, enabling mock-signature mode in production could turn a synthetic signature into an accepted authentication result. The current fix makes that unsafe configuration non-startable in production, preserves mock support in explicit test/dev runtimes, and has regression coverage proving the guard executes before DB initialization.

This completes the Step 2 requirements: attack description, current fix location, local reproduction evidence, and an explanation of why the fix closes the original vulnerability.
