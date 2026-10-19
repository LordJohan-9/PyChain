# PyChain concepts and modules

This guide connects the implementation to the blockchain mechanisms it
implements. Source paths are relative to the repository root.

## Module map

| Module | Blockchain concept and responsibility |
| --- | --- |
| `pychain/config.py` | Shared consensus and capacity parameters. Keeping them together makes protocol changes visible and consistent across nodes. |
| `pychain/core/hashing.py` | Canonical JSON serialization and SHA-256. Deterministic serialization ensures nodes compute the same identifiers and hashes from the same data. |
| `pychain/core/block.py` | Block header and body, header hashing, and the fixed genesis block. A block commits to its parent and transaction Merkle root. |
| `pychain/core/pow.py` | Proof-of-work target checks, nonce search, cancellation/progress reporting, and difficulty retargeting. |
| `pychain/core/transaction.py` | Signed account transfers and unsigned coinbase rewards. Transaction IDs commit to transfer fields; signatures authorize the sender. |
| `pychain/core/wallet.py` | secp256k1 key generation, address derivation, signing support, and JSON wallet persistence. |
| `pychain/core/chain.py` | Chain state, balances and nonces, block acceptance, historical validation, cumulative work, and chain replacement. |
| `pychain/core/mempool.py` | Pending transaction validation, deduplication, nonce/balance checks, and recovery of transactions after a reorganization. |
| `pychain/node/app.py` | FastAPI application, REST endpoints, node startup/shutdown, and static files mounted at `/ui`. |
| `pychain/node/schemas.py` | Strict Pydantic request/response models, separating wire contracts from internal objects. |
| `pychain/node/hardening.py` | P2P request-body bounds and a per-client fixed-window request limiter. |
| `pychain/node/miner.py` | Node runtime state, atomic coordination with mining, auto-mining lifecycle, persistence, and received block/transaction handling. |
| `pychain/node/peers.py` | Peer records, handshakes/discovery, health checks, gossip deduplication, peer penalties, and network synchronization entry points. |
| `pychain/node/consensus.py` | Full-chain retrieval, validation, cumulative-work fork choice, chain adoption, and mempool repair on reorganization. |
| `pychain/node/storage.py` | Atomic JSON persistence and full chain validation when loading stored state. |
| `pychain/node/__main__.py` | `python -m pychain.node` command-line node launcher. |
| `pychain/ui/index.html`, `styles.css`, `app.js` | Static block explorer and node controls; fetches JSON from the same node origin. |
| `scripts/run_demo.py` | Starts three node processes, waits for health checks, connects the peers, and opens the browser UI. |
| `tools/wallet_cli.py` | Generates wallets, displays addresses, and signs transfers without submitting them. |
| `tests/unit`, `tests/api`, `tests/integration` | Tests core invariants, HTTP contracts, and multi-node gossip/consensus behavior. |

## Protocol and runtime constants

Change shared consensus parameters in `pychain/config.py` together and
restart every node using the same configuration. Changes to consensus values
can make chains incompatible: do not mix nodes with different consensus
parameters on one network.

| Constant | Default | Effect when changed |
| --- | ---: | --- |
| `BLOCK_REWARD` (`pychain/config.py`) | `50` | Number of whole coins assigned to each valid coinbase transaction. Changing it changes issuance and validation rules. |
| `TARGET_BLOCK_TIME` (`pychain/config.py`) | `10` seconds | Expected time per block used by difficulty adjustment. Increasing it makes a given observed interval appear faster relative to target. |
| `RETARGET_INTERVAL` (`pychain/config.py`) | `10` blocks | Number of blocks in the timing window before adjusting proof-of-work difficulty. |
| `INITIAL_DIFFICULTY_BITS` (`pychain/config.py`) | `16` bits | Genesis and initial mining difficulty. Each added bit halves the expected fraction of hashes accepted, approximately doubling expected work. |
| `MIN_DIFFICULTY_BITS` (`pychain/config.py`) | `8` bits | Lower bound for retargeted difficulty; lowering it permits easier mining. |
| `MAX_DIFFICULTY_BITS` (`pychain/config.py`) | `40` bits | Upper bound for retargeted difficulty; raising it can make blocks impractical to mine. |
| `MAX_TX_PER_BLOCK` (`pychain/config.py`) | `100` | Maximum transactions in a block, including the coinbase reward. Increasing it permits larger blocks and more validation work. |
| `MAX_FUTURE_DRIFT` (`pychain/config.py`) | `120` seconds | Maximum permitted block timestamp lead over the local clock. Increasing it tolerates clock skew but weakens the future-time bound. |
| `MAX_PEERS` (`pychain/config.py`) | `8` | Maximum concurrently tracked peers per node. Raising it increases possible network fan-out and HTTP activity. |
| `GENESIS_TIMESTAMP` (`pychain/core/block.py`) | `1700000000` | Fixed genesis timestamp. Changing it changes the genesis header and hash; all nodes must agree. |
| `GENESIS_NONCE` (`pychain/core/block.py`) | `186063` | Fixed genesis proof-of-work nonce. Changing it changes the genesis hash and therefore chain identity. |
| `GENESIS_HASH` (`pychain/core/block.py`) | `00004137331b73d5343f99eeff4a2668c6950adfa9130530a5d77303d1bfa84d` | Expected hash for the fixed genesis header. If genesis fields change, this value must match the recomputed hash on every node. |
| `GENESIS_BLOCK` (`pychain/core/block.py`) | Fixed block object | Composite genesis block built from the fixed timestamp, nonce, hash, initial difficulty, zero parent hash, and empty Merkle root. Changing its fields changes the chain's identity and must be coordinated with all genesis constants. |
| `ADDRESS_LENGTH` (`pychain/core/wallet.py`) | `40` hex characters | Address length used by address derivation and validation. Changing it changes wallet/address compatibility. |
| `PUBLIC_KEY_LENGTH` (`pychain/core/wallet.py`) | `128` hex characters | Expected uncompressed secp256k1 public key length. It must remain consistent with the key encoding. |
| `PRIVATE_KEY_LENGTH` (`pychain/core/wallet.py`) | `64` hex characters | Expected secp256k1 private key length. It must remain consistent with the key encoding. |
| `COINBASE` (`pychain/core/transaction.py`) | `"COINBASE"` | Reserved sender marker for block rewards. It is a protocol tag, not a tunable wallet address. |
| `timestamp_refresh_interval` (`pychain/core/pow.py`) | `1000` attempts | Refresh frequency for a mining candidate's timestamp and hash template. Lowering it refreshes more often; raising it performs more nonce work between refreshes. |
| `PEER_TIMEOUT_SECONDS` (`pychain/node/peers.py`, `pychain/node/consensus.py`) | `3.0` seconds | HTTP timeout for peer management/gossip and chain synchronization respectively. A longer timeout tolerates slow peers but delays failure detection and requests. |
| `SYNC_INTERVAL_SECONDS` (`pychain/node/consensus.py`) | `60.0` seconds | Period between periodic peer synchronization rounds. Lowering it syncs more frequently at the cost of more network traffic. |
| `MAX_SEEN_IDS` (`pychain/node/peers.py`) | `10000` IDs | Maximum recent transaction/block IDs retained to suppress repeated gossip. Increasing it uses more memory; decreasing it forgets messages sooner. |
| `MAX_PEER_STRIKES` (`pychain/node/peers.py`) | `5` strikes | Invalid-message threshold for banning a peer for the current process session. |
| `MAX_PEER_FAILURES` (`pychain/node/peers.py`) | `3` failures | Consecutive peer HTTP/health failure threshold before removing a peer. |
| `MAX_P2P_BODY_BYTES` (`pychain/node/hardening.py`) | `1000000` bytes | Maximum body size accepted on `/p2p/*`; increasing it permits larger allocations and payloads. |
| `P2P_REQUESTS_PER_WINDOW` (`pychain/node/hardening.py`) | `60` requests | Requests allowed per client IP per fixed rate-limit window, independently per node process. |
| `P2P_RATE_WINDOW_SECONDS` (`pychain/node/hardening.py`) | `60` seconds | Duration of the fixed window used with `P2P_REQUESTS_PER_WINDOW`. |
| `DEFAULT_PORTS` (`scripts/run_demo.py`) | `8000, 8001, 8002` | Ports used by the three-process demo launcher. Change these if the defaults are already occupied. |
| `ROOT` (`scripts/run_demo.py`) | Repository root derived from the script path | Working directory for child node processes. It is computed from the script's location; change only if the launcher moves relative to the project root. |

The P2P body and rate-limit values can be changed per application instance via
the `create_app` arguments `p2p_max_body_bytes`,
`p2p_requests_per_window`, and `p2p_rate_window_seconds`. Other consensus
values are source-level protocol parameters.

## How a transaction moves through the network

1. A wallet constructs a transfer with its next nonce, derives the transaction
   ID from the unsigned fields, and signs the ID with ECDSA.
2. The receiving node verifies the signature and checks the balance, nonce,
   and duplicate state against both the chain and pending pool.
3. The node forwards each accepted transaction once to its peers. The
   bounded seen-ID cache prevents gossip loops.
4. A miner builds a candidate with pending transfers and a coinbase reward,
   then searches nonces until the header hash meets the target.
5. Nodes validate extending blocks before appending them and persist the chain.
   For competing branches, they validate the full candidate and choose the
   branch with the most cumulative work.
6. On adopting a heavier branch, the node rebuilds account state and attempts
   to return orphaned transactions to the mempool.

## Configuration cautions

- A genesis timestamp, nonce, reward, difficulty rule, timestamp bound,
  transaction limit, address format, or coinbase marker change affects
  consensus or data compatibility. Coordinate such changes across all nodes
  and stored chains.
- Increasing size, peer-count, or rate-limit thresholds increases resource
  exposure. The P2P limits are basic per-process controls, not a substitute for
  authentication or network isolation.
- Wallet JSON contains a private key without encryption. Protect wallet files
  with operating-system permissions and do not share them.
