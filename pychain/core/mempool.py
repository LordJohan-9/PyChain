"""Pending transaction admission and repair against confirmed chain state."""

from collections import OrderedDict
from collections.abc import Iterable, Iterator

from pychain.core.chain import Blockchain
from pychain.core.transaction import Transaction


class MempoolError(ValueError):
    """Raised when a pending transaction is invalid or conflicts with pool state."""


class Mempool:
    """An arrival-ordered pool validated against confirmed state and earlier pending spends."""

    def __init__(self, blockchain: Blockchain) -> None:
        self._blockchain = blockchain
        self._transactions: OrderedDict[str, Transaction] = OrderedDict()

    def __len__(self) -> int:
        return len(self._transactions)

    def __iter__(self) -> Iterator[Transaction]:
        return iter(self._transactions.values())

    def __contains__(self, tx_id: object) -> bool:
        return tx_id in self._transactions

    @property
    def transactions(self) -> list[Transaction]:
        """Return a copy of pending transactions in arrival order."""
        return list(self._transactions.values())

    def _confirmed_tx_ids(self) -> set[str]:
        return {
            str(payload["tx_id"])
            for block in self._blockchain.chain
            for payload in block.transactions
            if isinstance(payload, dict) and isinstance(payload.get("tx_id"), str)
        }

    def _pending_state(self) -> tuple[dict[str, int], dict[str, int]]:
        expected_nonces = dict(self._blockchain.nonces)
        remaining_balances = dict(self._blockchain.balances)
        for transaction in self._transactions.values():
            sender = transaction.sender_address
            expected_nonces[sender] = expected_nonces.get(sender, 0) + 1
            remaining_balances[sender] = (
                remaining_balances.get(sender, 0) - transaction.amount
            )
        return expected_nonces, remaining_balances

    def add(self, transaction: Transaction) -> bool:
        """Validate and queue a transaction; return false for an already-known transaction ID."""
        if not isinstance(transaction, Transaction):
            raise MempoolError("transaction must be a Transaction")
        if transaction.is_coinbase:
            raise MempoolError("coinbase transactions cannot enter the mempool")
        if not transaction.verify():
            raise MempoolError("transaction has invalid fields, ID, sender, or signature")
        if transaction.tx_id in self._confirmed_tx_ids():
            raise MempoolError("transaction is already confirmed")
        if transaction.tx_id in self._transactions:
            return False

        try:
            sender = transaction.sender_address
        except ValueError as error:
            raise MempoolError(f"invalid sender public key: {error}") from error

        expected_nonces, remaining_balances = self._pending_state()
        expected_nonce = expected_nonces.get(sender, 0)
        if transaction.nonce != expected_nonce:
            raise MempoolError(
                f"sender {sender} expected nonce {expected_nonce}, got {transaction.nonce}"
            )
        available_balance = remaining_balances.get(sender, 0)
        if transaction.amount > available_balance:
            raise MempoolError(
                f"sender {sender} has available balance {available_balance}, "
                f"needs {transaction.amount}"
            )

        self._transactions[transaction.tx_id] = transaction
        return True

    def remove_included(self, transactions: Iterable[Transaction | str]) -> None:
        """Remove transactions included in a newly appended block and revalidate survivors."""
        for item in transactions:
            tx_id = item.tx_id if isinstance(item, Transaction) else item
            self._transactions.pop(tx_id, None)
        self.revalidate()

    def revalidate(self) -> None:
        """Drop pending entries that no longer validate against current confirmed state."""
        pending = list(self._transactions.values())
        self._transactions.clear()
        for transaction in pending:
            try:
                self.add(transaction)
            except MempoolError:
                continue

    def repair_after_reorg(
        self,
        orphaned_transactions: Iterable[Transaction],
    ) -> None:
        """Reconsider orphaned transactions before revalidating the existing pool."""
        pending = list(self._transactions.values())
        self._transactions.clear()
        for transaction in (*orphaned_transactions, *pending):
            try:
                self.add(transaction)
            except MempoolError:
                continue
