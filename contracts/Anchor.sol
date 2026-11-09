// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Keeps the Merkle root of every batch of sensor messages, so stored messages
// can later be checked against it. Only the gateway that deployed the contract may anchor.
contract Anchor {
    struct Batch {
        uint32 count;        // number of messages in the batch
        uint64 ts;           // time the gateway closed the batch (ms since 1970)
        uint64 blockNumber;  // block in which the root was stored
    }

    mapping(bytes32 => Batch) public batches;  // Merkle root -> batch info
    uint256 public batchCount;
    address public gateway;

    event Anchored(uint256 indexed batchNumber, bytes32 indexed root, uint32 count, uint64 ts);

    constructor() {
        gateway = msg.sender;  // whoever deploys the contract is the gateway
    }

    function anchor(bytes32 root, uint32 count, uint64 ts) external {
        require(msg.sender == gateway, "only the gateway can anchor");
        require(count > 0, "empty batch");
        require(batches[root].count == 0, "root already anchored");

        batches[root] = Batch(count, ts, uint64(block.number));
        batchCount = batchCount + 1;
        emit Anchored(batchCount, root, count, ts);
    }
}
