import { expect } from "chai";
import { network } from "hardhat";

// Start a fresh, temporary Hardhat blockchain for these tests
const { ethers } = await network.create();

// Any 32 bytes work as a "root" for testing
const ROOT = ethers.keccak256(ethers.toUtf8Bytes("batch 1"));
const OTHER_ROOT = ethers.keccak256(ethers.toUtf8Bytes("batch 2"));
const TS = 1793842302227n;

describe("Anchor", function () {
  it("stores a root with its count, time and block", async function () {
    const anchor = await ethers.deployContract("Anchor");
    await anchor.anchor(ROOT, 58, TS);

    const batch = await anchor.batches(ROOT);
    expect(batch.count).to.equal(58n);
    expect(batch.ts).to.equal(TS);
    expect(batch.blockNumber).to.be.greaterThan(0n);
    expect(await anchor.batchCount()).to.equal(1n);
  });

  it("emits the Anchored event", async function () {
    const anchor = await ethers.deployContract("Anchor");
    await expect(anchor.anchor(ROOT, 58, TS))
      .to.emit(anchor, "Anchored")
      .withArgs(1n, ROOT, 58n, TS);
  });

  it("returns count 0 for a root that was never anchored", async function () {
    const anchor = await ethers.deployContract("Anchor");
    await anchor.anchor(ROOT, 58, TS);

    const batch = await anchor.batches(OTHER_ROOT);
    expect(batch.count).to.equal(0n);
  });

  it("rejects the same root twice", async function () {
    const anchor = await ethers.deployContract("Anchor");
    await anchor.anchor(ROOT, 58, TS);
    await expect(anchor.anchor(ROOT, 58, TS)).to.be.revertedWith("root already anchored");
  });

  it("rejects an empty batch", async function () {
    const anchor = await ethers.deployContract("Anchor");
    await expect(anchor.anchor(ROOT, 0, TS)).to.be.revertedWith("empty batch");
  });

  it("rejects anchors from anyone except the gateway", async function () {
    const anchor = await ethers.deployContract("Anchor");
    const [, someoneElse] = await ethers.getSigners();
    await expect(anchor.connect(someoneElse).anchor(ROOT, 58, TS))
      .to.be.revertedWith("only the gateway can anchor");
  });
});
