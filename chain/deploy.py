"""
Puts the Anchor contract on a blockchain and writes chain/deployment.json,
which the gateway reads to know where to send the anchors.

Usage (from the repository folder, after "npx hardhat compile"):
    python chain/deploy.py                          -> Besu devnet at http://127.0.0.1:8545
    python chain/deploy.py http://127.0.0.1:8545    -> any other node (e.g. "npx hardhat node")
"""
import os
import sys
import json
from web3 import Web3

RPC_URL = "http://127.0.0.1:8545"        # adresa pe care o vad uneltele de pe host; asta se scrie in deployment.json
CONNECT_URL = os.environ.get("RPC_URL", RPC_URL)   # din container nodul se vede ca besu-node1
if len(sys.argv) > 1:
    RPC_URL = CONNECT_URL = sys.argv[1]

# The gateway's blockchain account. This is the well-known first test account of Hardhat;
# the Besu genesis gives it money too. Fine for a private test network, NEVER for a real one.
GATEWAY_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

ARTIFACT_FILE = "artifacts/contracts/Anchor.sol/Anchor.json"   # made by "npx hardhat compile"
DEPLOYMENT_FILE = "chain/deployment.json"


def main():
    w3 = Web3(Web3.HTTPProvider(CONNECT_URL))
    if not w3.is_connected():
        print(f"Cannot reach a blockchain node at {CONNECT_URL}")
        sys.exit(1)

    with open(ARTIFACT_FILE) as f:
        artifact = json.load(f)
    abi = artifact["abi"]               # description of the contract's functions
    bytecode = artifact["bytecode"]     # the compiled contract

    account = w3.eth.account.from_key(GATEWAY_PRIVATE_KEY)
    print(f"Deploying from account {account.address} to {CONNECT_URL} (chain id {w3.eth.chain_id})")

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    transaction = contract.constructor().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })
    signed = account.sign_transaction(transaction)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status != 1:
        print("Deployment failed")
        sys.exit(1)

    deployment = {
        "rpc_url": RPC_URL,
        "chain_id": w3.eth.chain_id,
        "address": receipt.contractAddress,
        "gateway_private_key": GATEWAY_PRIVATE_KEY,
        "abi": abi,
    }
    with open(DEPLOYMENT_FILE, "w") as f:
        json.dump(deployment, f, indent=1)

    print(f"Anchor contract deployed at {receipt.contractAddress} (block {receipt.blockNumber})")
    print(f"Saved to {DEPLOYMENT_FILE}")


main()
