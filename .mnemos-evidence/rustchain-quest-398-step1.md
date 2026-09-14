# RustChain Quest #398 — Step 1 Security Assessment

Submission for `Scottcjn/rustchain-bounties#398`.

- Reporter: `@aziemels-byte`
- Wallet: `RTCcd14e195e2c583ea27c2f6fb21a0717c0bbe4212`
- Step claimed: Step 1 — Security Explorer (10 RTC)
- Reviewed source: `Scottcjn/Rustchain@aa584b344a766f6c0f8613ba7198d1cc7ffbae35` (current `main` when reviewed on 2026-09-14)

I reviewed the current integrated node and RIP-200 reward implementation rather than relying on older architecture descriptions. The current code has materially stronger controls than early RustChain versions: phased Ed25519 identity enforcement, server-side hardware binding, replay detection, rotating fingerprint checks, temporal consistency scoring, and transactionally serialized epoch settlement. The residual risk I would prioritize is **measurement freshness during the current RIP-309c phase-0 rollout**: the attestation envelope is fresh, but an unbound client can still submit old measurement content because measurement binding is currently observed rather than reward-gating.

## 1. `/attest/submit` flow and trust boundaries

The live route is in `node/rustchain_v2_integrated_v2.2.1_rip200.py` around [L6000-L6505](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/node/rustchain_v2_integrated_v2.2.1_rip200.py#L6000-L6505). The handler requires an object-shaped JSON payload and runs `_validate_attestation_payload_shape()` before identity or hardware processing. It then extracts the miner identity, normalized report, nonce, and device data.

The signature path is deliberately phased. If a canonical Ed25519 signature is supplied, the current v3 scheme verifies canonical JSON covering the payload rather than only a few identity fields. Legacy four-field signing remains for compatibility, but an explicitly v3-typed request cannot silently fall back to the weaker legacy MAC. Partial signature/public-key pairs are rejected, malformed types return 400, and signed submissions fail closed if PyNaCl is unavailable. The same section prevents arbitrary key rotation and only permits TOFU pinning where identity state makes that safe. Canonical `RTC...` identities must derive from the supplied Ed25519 public key before they can be pinned.

A compatibility choice is visible around [L6200-L6505](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/node/rustchain_v2_integrated_v2.2.1_rip200.py#L6200-L6505): in `log_only` enforcement mode, unsigned attestations are still accepted while the node logs what a later enforcement phase would reject. This is intentional rollout behavior rather than a signature-verification failure. `node/tests/test_attest_pin_survives_unsigned_attest.py` explicitly exercises an accepted unsigned re-attestation in `log_only` mode.

Freshness of the envelope is stronger. A nonce is mandatory and `attest_validate_and_store_nonce()` checks the issued challenge, rejects an expired/already-used nonce, and rejects a challenge issued for another identity. That blocks the simple capture-and-replay-the-same-request attack.

## 2. Hardware fingerprinting and VM-farm resistance

The hardware controls continue around [L6500-L7000](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/node/rustchain_v2_integrated_v2.2.1_rip200.py#L6500-L7000). Hardware binding v2 associates a physical identity with the miner; the legacy path rejects a second miner trying to reuse already-bound hardware. The replay-defense layer computes fingerprint and entropy hashes, checks fingerprint replay, entropy collision, and per-hardware rate limits before recording a new submission.

Fingerprint validation is economically important because the node does not need to reject every suspicious request at HTTP level. `validate_fingerprint_data()` and the independent server-side VM-signature check feed `fingerprint_passed`; failed or VM-like fingerprints remain auditable but are marked for **zero reward weight**. Auto-enrollment derives the reward device server-side and uses rotating fingerprint checks. A failed fingerprint receives `FAILED_FINGERPRINT_WEIGHT_UNITS`, preventing the straightforward VM-farm strategy of inventing a high-value architecture string and collecting its multiplier.

This is a defense-in-depth design: nonce freshness blocks request replay; hardware binding blocks simple one-machine/many-wallet reuse; fingerprint and VM checks determine economic eligibility; rotating checks and temporal history make a static forged profile harder to maintain over time.

## 3. Epoch rewards and distribution

RIP-200 settlement is implemented in `node/rewards_implementation_rip200.py`, especially [L120-L390](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/node/rewards_implementation_rip200.py#L120-L390). Slots are 600 seconds and an epoch is 144 slots. The configured epoch budget is 1.5 RTC (`PER_EPOCH_URTC = 1,500,000`).

`settle_epoch_rip200()` rejects future epochs, opens an SQLite `BEGIN IMMEDIATE` transaction, and checks `epoch_state.settled` while holding the write lock. This prevents two workers from both passing an unlocked “not settled” check and double-crediting an epoch. The function also clamps emission against the fixed supply cap. In production the anti-double-mining path defaults fail-closed if required but unavailable or failing. In the standard path, rewards are calculated, balances are incremented, an auditable `ledger` row and `epoch_rewards` row are written for each miner, and only then is the epoch marked settled and committed. There is a second settled-state check after the rollback/re-lock path to close a concurrent-settlement window.

The settlement layer itself therefore looks substantially race-hardened. Its main dependency is admission quality: if a fake identity acquires valid enrollment weight upstream, the reward engine will correctly and deterministically pay that bad input.

## 4. Potential attack vector: stale measurement content during RIP-309c phase 0

The most interesting current residual risk is documented by the project itself in [`tests/test_measurement_freshness_binding.py`](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/tests/test_measurement_freshness_binding.py#L1-L145). A fresh challenge proves the **submission envelope** is recent, but it does not prove that timing/entropy measurements inside the payload were produced for that challenge. The test notes that a client can wrap values measured earlier in a fresh nonce and that the reference client historically cached fingerprint data for the process lifetime.

The code already contains a strong answer: `derive_measurement_workload()` derives a workload from the challenge, and `verify_measurement_binding()` rejects a binding created for another nonce or with timing implausible relative to known history. However, the current integration explicitly labels this **phase 0: observe only**. Around [L6500-L7000](https://github.com/Scottcjn/Rustchain/blob/aa584b344a766f6c0f8613ba7198d1cc7ffbae35/node/rustchain_v2_integrated_v2.2.1_rip200.py#L6500-L7000), the verdict is logged but deliberately does not modify enrollment weight; the test suite likewise asserts that an absent binding remains a normal non-failing state and that the verdict does not yet gate rewards.

That creates a bounded transition risk: an adversarial client can preserve a favorable measurement snapshot and repeatedly re-wrap it in valid fresh challenges. Replay hashing, hardware binding, rotating checks, VM detection, and temporal consistency reduce the usefulness of this technique, so I would not call it a full attestation bypass. But while challenge-bound measurement is optional, “fresh request” and “fresh physical measurement” are not equivalent.

**Recommended hardening:** keep the compatibility rollout, but once binding adoption is measurable, gate only the **antiquity premium** on a valid challenge-bound measurement. Unbound legacy clients could remain at baseline weight rather than being rejected. That preserves vintage-device compatibility while removing the economic incentive to replay a curated stale measurement profile. A versioned grace period plus telemetry for `absent`, `bound`, `stale`, and `rate_implausible` states would make the cutover measurable and reversible.

This completes Step 1. No production mutation, exploitation, enrollment, or mining was performed; this was a source-level defensive review of the current `main` commit.