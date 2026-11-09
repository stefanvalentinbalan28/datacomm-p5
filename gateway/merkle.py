"""
Merkle tree with SHA-256.

    leaves   = one hash per message
    levels   = the tree, level by level: levels[0] = leaves, levels[-1] = [root]
    proof    = the sibling hashes needed to go from one leaf up to the root

Used by the gateway (to build the root and the proofs) and by the tamper test
(to check a stored record against the root on the blockchain).
"""
import json
import hashlib


def record_bytes(record):
    """The exact bytes we hash for one message: the full record (with its signature)
    as JSON with sorted keys, so the same record always gives the same bytes."""
    return json.dumps(record, sort_keys=True).encode('utf-8')


def hash_leaf(data):
    # A leaf starts with byte 0x00 and an inner node with 0x01, so a leaf
    # can never be mistaken for an inner node (a known trick against forged proofs).
    return hashlib.sha256(b"\x00" + data).digest()


def hash_pair(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def build_tree(leaves):
    """Build the tree from the bottom up. Returns the list of levels."""
    levels = [leaves]
    current_level = leaves
    while len(current_level) > 1:
        next_level = []
        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                # two neighbours -> one parent
                next_level.append(hash_pair(current_level[i], current_level[i + 1]))
            else:
                # odd number of nodes: the last one moves up unchanged
                next_level.append(current_level[i])
        levels.append(next_level)
        current_level = next_level
    return levels


def get_root(levels):
    return levels[-1][0]


def get_proof(levels, index):
    """Sibling hashes from leaf number `index` up to the root.
    Each step is [side, hash]: side "L" = sibling is on the left, "R" = on the right."""
    proof = []
    for level in levels[:-1]:          # every level except the root
        if index % 2 == 0:
            sibling_index = index + 1
            side = "R"
        else:
            sibling_index = index - 1
            side = "L"
        if sibling_index < len(level):  # no sibling = this node moved up unchanged
            proof.append([side, level[sibling_index].hex()])
        index = index // 2               # position of the parent in the next level
    return proof


def verify_proof(leaf, proof, root):
    """Recompute the root from one leaf and its proof. True if it matches."""
    current = leaf
    for side, sibling_hex in proof:
        sibling = bytes.fromhex(sibling_hex)
        if side == "L":
            current = hash_pair(sibling, current)
        else:
            current = hash_pair(current, sibling)
    return current == root
