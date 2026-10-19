# PyChain

PyChain is a Python proof-of-work blockchain node with signed account-based
transactions, peer-to-peer gossip, cumulative-work fork choice, a REST API, and
a browser block explorer. It runs as a local network of independent processes
and persists each node's chain, peers, and wallet as JSON files.

> **Security:** PyChain is a local prototype, not a production cryptocurrency.
> Its API has no authentication, wallet files contain unencrypted private keys,
> and nodes should only be exposed to trusted networks. Do not use real funds or
> expose a node to the public internet.

## Quickstart

Requires Python 3.11 or newer. From the repository root, install the package
and development tools:

```console
python -m pip install -e ".[dev]"
```

Start three connected nodes and open the browser UI:

```console
python scripts/run_demo.py
```

The launcher creates `data/demo/node1`, `data/demo/node2`, and
`data/demo/node3`, waits for all nodes, connects the peers, and opens
`http://127.0.0.1:8000/ui`. Keep the launcher terminal open; press **Ctrl+C**
there to stop the three nodes. Add `--no-browser` to start without opening a
browser, or `--data-root PATH` to choose another data directory. Existing chain
and wallet files are retained across runs.

To start a single node instead:

```console
python -m pychain.node --port 8000 --data-dir data/node1
```

Connect it to existing nodes at startup with `--peers URL ...`:

```console
python -m pychain.node --port 8000 --data-dir data/node1 --peers http://127.0.0.1:8001
```

The node serves the UI at `/ui`, API documentation at `/docs`, and an OpenAPI
schema at `/openapi.json`. The browser refreshes node data every three seconds.
Use the node selector in the top bar to switch between nodes.

## Demo walkthrough

1. Run `python scripts/run_demo.py`, open the UI at node 1, and check **Peers**
   to confirm that two nodes are connected.
2. Open **Mining & Validation** and choose **Mine one block**. Node 1's wallet
   receives the 50-coin block reward.
3. Switch the node selector to `http://127.0.0.1:8001`, open **Wallet & Send**,
   and copy node 2's address. Switch back to node 1, send 10 coins to that
   address, then check **Pending** on each node as the transaction propagates.
4. Switch to node 3 and mine one block. The transaction is confirmed and the
   chain and balances update on connected nodes.
5. To demonstrate a fork, open **Peers** on node 1 and remove node 2. Switch to
   node 2 and remove node 1 as well (peer removal is local to each node). Mine
   a block on each side, reconnect node 1 and node 2 from either Peers view,
   then use **Sync now**. The nodes adopt the valid chain with greater
   cumulative work.
6. To demonstrate tamper detection, stop the launcher with Ctrl+C. Edit a
   non-coinbase transaction amount in `data/demo/node1/chain.json`, then start
   node 1 with `python -m pychain.node --port 8000 --data-dir data/demo/node1`.
   Startup validation rejects the altered chain and reports the failing block
   and rule in the terminal. Stop this node with Ctrl+C before restarting the
   demo launcher.

The node processes and persisted JSON files are managed by the operator; the
browser UI cannot start or restart operating-system processes or edit the
on-disk chain file.

## Architecture

Each process hosts the same API and static UI, while owning its own ledger,
wallet, mempool, miner, storage, and peer manager.

```text
                    +---------------------+
                    |   Browser at /ui    |
                    +----------+----------+
                               | REST / JSON
                    +----------v----------+
                    | FastAPI node app    |
                    +----------+----------+
                               |
          +--------------------+---------------------+
          |                    |                     |
  +-------v-------+    +-------v-------+     +-------v--------+
  | Blockchain &  |    | Miner and     |     | Peer manager & |
  | transaction  |    | mempool       |     | consensus      |
  | validation    |    +---------------+     +----------------+
  +-------+-------+                                  |
          |                                  HTTP peer gossip/sync
  +-------v-------+                                  |
  | JSON chain,  |                         +--------v--------+
  | wallet, peers|                         | Other PyChain   |
  +---------------+                         | node processes  |
                                             +-----------------+
```

The core package implements deterministic block and transaction rules. The
node layer coordinates persistence, mining, API calls, peer messaging, and
fork resolution. Peer messages are bounded and rate-limited by the node's
in-process P2P middleware.

See [docs/CONCEPTS.md](docs/CONCEPTS.md) for the module-to-concept guide and
[the requirements document](PyChain%20Project%20Requirements%20Document.md)
for the full API and protocol requirements.

## Wallet CLI

Create a wallet (the private key is stored only in the wallet file; the command
prints the public address and file path):

```console
python tools/wallet_cli.py new --wallet data/wallet-a.json
```

Print an existing wallet's address:

```console
python tools/wallet_cli.py address --wallet data/wallet-a.json
```

Sign a transfer offline and print its transaction JSON:

```console
python tools/wallet_cli.py sign --wallet data/wallet-a.json --recipient 0123456789abcdef0123456789abcdef01234567 --amount 10 --nonce 0
```

`sign` requires the sender's next nonce. The optional `--timestamp` sets the
transaction time; otherwise the current Unix timestamp is used. The CLI does
not submit the transaction. To replace a wallet intentionally, pass
`new --force`; otherwise the command refuses to overwrite an existing file.

## API overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/status`, `/health` | Node status and liveness |
| `GET` | `/chain`, `/blocks/latest`, `/blocks/{index}` | Chain and block inspection |
| `GET` | `/wallet`, `/balances/{address}` | Wallet and account balance |
| `POST` | `/wallet/send` | Sign and submit using the node wallet |
| `GET` | `/transactions/pending`, `/transactions/{tx_id}` | Mempool and transaction lookup |
| `POST` | `/transactions` | Submit a signed transaction |
| `POST` | `/mine`, `/mine/start`, `/mine/stop` | Mine one block or control auto-mining |
| `GET` | `/validate` | Validate the chain; returns the first failing rule |
| `GET`, `POST`, `DELETE` | `/peers` | List, add, or remove a peer |
| `POST` | `/sync` | Synchronize with known peers |

Peer protocol routes are under `/p2p/`. They have a 1,000,000-byte request
body limit and a default rate limit of 60 requests per client IP per 60-second
fixed window. These in-memory limits apply independently to each node process.

## Development

Run the tests and core coverage report:

```console
python -m pytest --cov=pychain.core --cov-report=term-missing
```

Run the linter and type checker:

```console
ruff check pychain scripts tools tests
mypy pychain scripts/run_demo.py tools/wallet_cli.py
```

Core coverage should remain at or above 85%. Protocol and runtime tuning
constants, including their effects, are documented in
[docs/CONCEPTS.md](docs/CONCEPTS.md).
