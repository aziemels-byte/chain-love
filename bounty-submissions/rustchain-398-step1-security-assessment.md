# RustChain #398 Step 1 — Security Assessment

Submission target: `Scottcjn/rustchain-bounties#398`, **Step 1 — Security Explorer (10 RTC)**.

**Reporter:** @aziemels-byte  
**RTC wallet:** `RTCcd14e195e2c583ea27c2f6fb21a0717c0bbe4212`  
**RustChain commit reviewed:** `aa584b344a766f6c0f8613ba7198d1cc7ffbae35`

## 1. Attestation flow

The current implementation is in `node/rustchain_v2_integrated_v2.2.1_rip200.py`. `POST /attest/submit` first validates that the request body is a JSON object, normalizes the miner identity, report, nonce and device fields, and then passes the submission through identity, replay, hardware and fingerprint checks before recording success.

The identity layer supports Ed25519 signatures. The current canonical-JSON scheme signs the full attestation payload before `signature`, `signature_type` and `public_key` are appended. The server reconstructs those canonical bytes and verifies them. A narrower legacy four-field signature remains for compatibility, but an explicitly canonical request cannot silently fall back to the weaker legacy path.

For self-certifying `RTC...` identities, the server also checks that the submitted public key derives to the claimed RTC address. Existing signing keys cannot simply be replaced by a different verified key; rotation is held for the admin path. New symbolic identities may be pinned through first-use rules, while grandfathered symbolic identities are deliberately not public-TOFU pinned.

Freshness is separate from signature verification. The challenge nonce is server-issued, expires, is single-use and can be bound to an identity. The submit path calls the nonce validation/store logic and rejects missing, replayed, expired or identity-mismatched challenges. This blocks direct replay of a previously accepted envelope.

## 2. Hardware fingerprinting and VM-farm resistance

RustChain uses several layers rather than trusting one client-supplied `is_vm` bit. The request carries device data, signals and a fingerprint object. The server applies IP-rate controls, hardware binding, OUI checks, replay/collision detection, fingerprint validation and server-side VM checks.

The stateful hardware-binding design is important. `hardware_bindings` associates a hardened hardware identity with a miner. The current schema also tracks `stable_hw_id`, which represents the serial-independent machine identity. Before creating a fresh binding, the server checks whether that stable identity already belongs to another miner. This prevents the simple farm strategy of changing a client-supplied serial while reusing the same physical host under many wallets.

The fingerprint path adds additional friction. Replay-defense code hashes fingerprint and entropy material, checks reuse and collisions, rate-limits repeated submissions and records accepted history. RIP-309 rotates active fingerprint checks per epoch and derives a server-known measurement nonce. Temporal consistency logic compares a miner against its own historical measurements, while operator clustering remains observe-only because honest labs can share network signals. Together these mechanisms make a static VM template or one centrally generated fake profile harder to scale across many identities.

The economic gate is explicit: if fingerprint validation fails, the code marks the fingerprint as failed and the auto-enrollment path assigns zero reward weight. VM/fingerprint failure therefore does not merely produce a warning; it affects reward eligibility.

## 3. Epoch reward calculation and settlement

The RIP-200 settlement implementation is in `node/rewards_implementation_rip200.py`. The fixed epoch budget is `1.5 RTC` (`1,500,000 uRTC`), bounded by the total supply cap. Settlement rejects future epochs and starts with `BEGIN IMMEDIATE`, so the `already settled` check happens while the SQLite write lock is held.

That transaction structure is security-relevant because it prevents two settlement workers from simultaneously observing an unsettled epoch and crediting it twice. The anti-double-mining path runs inside the same locked connection. In production, the current default is fail-closed when anti-double-mining is required: if the ADM module is unavailable or fails, settlement is rolled back instead of silently continuing on a weaker path.

If compatibility fallback is allowed, the code rolls back partial ADM writes, reacquires the write lock and re-checks the settled flag before calculating standard rewards. That second check closes the race where another worker could settle the epoch during the unlock/relock window.

Successful settlement updates miner balances, writes a ledger record, writes `epoch_rewards`, and marks the epoch settled. This creates both atomicity and an audit trail. The reward layer is therefore reasonably robust against duplicate settlement; the larger security dependency is whether the upstream attestation/enrollment layer admitted the correct physical identities and weights.

## 4. Residual attack surface: unsigned attestation rollout gap

The clearest remaining risk I found is a **rollout-mode identity gap**, not a broken primitive.

The node uses `_attest_enforce_mode()` to phase in signature enforcement. In `enforce_new` and `enforce_all`, miners that must sign are rejected if no Ed25519 signature is present. In `log_only`, however, an unsigned attestation is still accepted and merely logged as something that would be rejected in a later phase.

The repository's own regression test `node/tests/test_attest_pin_survives_unsigned_attest.py` explicitly documents this compatibility state: its test scenario says unsigned submissions are accepted in the default `log_only` rollout mode. The current handler also fails open on an identity-state read exception in `log_only`, substituting `(None, False, False)` and continuing; the enforcing modes correctly return 503 instead.

I would classify this as an **identity-poisoning / denial-of-service risk during compatibility rollout**, not a demonstrated direct funds-theft vulnerability. Hardware and fingerprint gates still constrain economic eligibility, but an attacker with real hardware can submit an unsigned claim for an identity without proving possession of that identity's private key. That can create ambiguity or unwanted state around miner identity/hardware ownership until enforcing mode is active.

## 5. Recommended hardening

The strongest next step is to retire `log_only` on production as legacy-client coverage permits. A safe intermediate policy would be:

1. require Ed25519 signatures immediately for self-certifying `RTC...` addresses;
2. fail closed on identity-state read errors for those addresses even during compatibility rollout;
3. keep unsigned support only for a small explicit operator-maintained legacy allowlist;
4. continue observing measurement-binding adoption, then progressively tie missing challenge-bound measurements to reduced antiquity bonus rather than a hard reject.

Overall, current RustChain has strong layered controls around replay, VM detection, hardware binding and reward-settlement races. The most important remaining question is how quickly compatibility modes that permit unsigned identity claims can be retired without breaking genuine vintage clients.

This completes the Step 1 requirements: attestation flow, VM-farm resistance, epoch reward calculation/distribution, and one concrete attack surface with a suggested fix.
