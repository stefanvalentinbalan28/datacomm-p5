"""
Sends one batch's Merkle root to the blockchain: anchor(root, count, ts),
and waits until the transaction is confirmed in a block.

Where to send it is read from chain/deployment.json, written by chain/deploy.py.
If that file does not exist, CHAIN_READY is False and nothing is sent.
"""
import os
import json
import time
import threading
from web3 import Web3

DEPLOYMENT_FILE = "chain/deployment.json"
RECEIPT_POLL_SECONDS = 0.05   # how often we ask "is it in a block yet?" -> precision of t_confirmed
RECEIPT_TIMEOUT_SECONDS = 120
GAS_LIMIT = 200_000           # more than enough for one anchor() call

CHAIN_READY = os.path.exists(DEPLOYMENT_FILE)

w3 = None
contract = None
account = None
gas_price = None
next_nonce = None
send_lock = threading.Lock()  # several batches are anchored at the same time (see below)

if CHAIN_READY:
    with open(DEPLOYMENT_FILE) as f:
        deployment = json.load(f)
    # din container nodul se vede ca besu-node1, nu ca 127.0.0.1 -> RPC_URL bate fisierul
    w3 = Web3(Web3.HTTPProvider(os.environ.get("RPC_URL", deployment["rpc_url"])))
    contract = w3.eth.contract(address=deployment["address"], abi=deployment["abi"])
    account = w3.eth.account.from_key(deployment["gateway_private_key"])
    gas_price = w3.eth.gas_price
    # Every transaction of an account has a number (nonce): 0, 1, 2, ... The node rejects
    # duplicates, so we count them ourselves, starting from what the chain already knows.
    next_nonce = w3.eth.get_transaction_count(account.address, "pending")


def send_anchor(root, count, ts_ms):
    """Send anchor(root, count, ts) and wait until it is confirmed in a block.

    root   - Merkle root, 32 bytes
    count  - number of messages in the batch
    ts_ms  - time the batch was closed, in milliseconds since 1970

    Returns (t_tx_sent, t_confirmed, block_number, status).
    """
    global next_nonce

    if not CHAIN_READY:
        return time.time(), None, None, "no_chain_yet"

    # Build, sign and send. Only one batch at a time may do this part, so that
    # two batches never get the same nonce.
    with send_lock:
        transaction = contract.functions.anchor(root, count, ts_ms).build_transaction({
            "from": account.address,
            "nonce": next_nonce,
            "gas": GAS_LIMIT,
            "gasPrice": gas_price,
            "chainId": deployment["chain_id"],
        })
        signed = account.sign_transaction(transaction)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        t_tx_sent = time.time()
        next_nonce = next_nonce + 1

    # Wait for the receipt. Many batches can be waiting here at the same time:
    # with a block every 2 s, about 4 batches are on their way at any moment.
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_SECONDS,
                                                  poll_latency=RECEIPT_POLL_SECONDS)
    t_confirmed = time.time()

    if receipt.status == 1:
        status = "confirmed"
    else:
        status = "failed"
    return t_tx_sent, t_confirmed, receipt.blockNumber, status


def is_anchored(root):
    """For the tamper test: how many messages the chain says this root has (0 = never anchored)."""
    batch = contract.functions.batches(root).call()
    return batch[0]
